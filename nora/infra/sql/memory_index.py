"""The memory index's one writer: `project`, `reproject_media`, `drop`
(design/unified-memory/60-build-step-3.md §2, 25-memory-graph.md §7).

Each store method that writes an indexed table calls one of these INSIDE its
own `engine.begin()` block, on the same connection, beside the write it
projects: Nora has no shared unit of work, so the store's transaction is the
one place the source row and its node can change together (25 §7, amended
2026-10-15). `tests/architecture/test_every_indexed_kind_is_projected.py`
holds every write to an `INDEXED_WRITES` table to that, and holds the
`memory_*` tables to this module.

`project(conn, kind, source_id)` re-reads the source row on the caller's
connection and upserts its node and its column edges, or drops them when the
row is hidden or gone, so one call serves an insert, an edit, a hide and an
attribution alike. Every call runs in a SAVEPOINT: a projection that fails
rolls back only itself, puts the source on `memory_projection_queue` and is
counted `nora_memory_projection_total{outcome="queued"}`. It never undoes the
store's own write (60 §2): a message the index missed is a search miss until
the next pass; a message lost because its index row failed is a lost message.

S1 projects messages and their attachments only. Nothing reads the index yet
(S5), so it is written dark.

**What a node's text holds** (R-18, R-28): a message's body, and an
attachment's reading. Never a sender id, a sender name, a raw envelope or a
coordinate; the person and room are edges to hub nodes, keyed by the
roster's `person_id` and `nora.domain.rollup.room_key`, and hub nodes carry no
text until S2 names them. A failure's `last_error` is the exception's CLASS
only: a driver error's message carries the statement's parameters, which here
are message bodies.

A snapshot restore copies none of the four tables (`NOT_RESTORED`): the
restored index is empty and reads `never` until a rebuild (60 §0.5, S4).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.media_join import shas_in
from nora.domain.memory_index import ProjectionOutcome
from nora.domain.rollup import room_key
from nora.infra.sql.schema import (
    MEDIA_OBJECTS,
    MEMORY_EDGE,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
    MESSAGE_EDIT,
    MESSAGES,
)

__all__ = ["INDEXED_WRITES", "drop", "project", "reproject_media"]

log = logging.getLogger("nora.infra.sql.memory_index")

#: Every table whose writes change what the index holds, and the node kind
#: they change. `message_edit` is a message's text; `media_objects` is the
#: reading every attachment of those bytes copies in. The architecture test
#: reads this to find the write paths that must project.
INDEXED_WRITES: dict[str, str] = {
    "messages": "message",
    "message_edit": "message",
    "media_objects": "media",
}

_METRIC = "nora_memory_projection_total"


async def project(
    conn: AsyncConnection, kind: str, source_id: str | int, *, metrics: Any = None,
) -> None:
    """Upsert `kind`'s node for `source_id` and its column edges, or drop
    them when the source is hidden or gone. A message brings its attachments
    with it, so an attachment is never projected on its own."""
    sid = str(source_id)
    await _guarded(conn, kind, [sid], lambda: _PROJECTORS[kind](conn, sid), metrics)


async def reproject_media(
    conn: AsyncConnection, sha256: str, *, metrics: Any = None,
) -> None:
    """Copy the reading of `sha256` into every attachment of those bytes. A
    reading that lands before its message is ingested is copied by that
    message's own `project` instead; this is the reading landing after."""
    await _guarded(conn, "media", [sha256], lambda: _reproject_media(conn, sha256), metrics)


async def drop(
    conn: AsyncConnection, kind: str, ids: Iterable[str | int], *, metrics: Any = None,
) -> None:
    """Remove `kind`'s nodes for `ids` and every edge into or out of them; a
    message takes its attachments with it."""
    sids = [str(i) for i in ids]
    if not sids:
        return
    await _guarded(conn, kind, sids, lambda: _drop(conn, kind, sids), metrics)


async def _guarded(
    conn: AsyncConnection, kind: str, source_ids: list[str],
    work: Callable[[], Awaitable[None]], metrics: Any,
) -> None:
    """The savepoint every projection runs in (60 §2). `begin_nested` rolls
    back the projection alone; the caller's transaction, and the store write
    in it, carry on."""
    outcome: ProjectionOutcome = "ok"
    try:
        async with conn.begin_nested():
            await work()
    except Exception as exc:  # noqa: BLE001 — any failure queues; the store write stands
        log.warning(
            "memory projection failed (%s, %s); queued for the next pass",
            kind, type(exc).__name__,
        )
        outcome = await _queue(conn, kind, source_ids, type(exc).__name__)
    if metrics is not None:
        metrics.inc(_METRIC, kind=kind, outcome=outcome)


async def _queue(
    conn: AsyncConnection, kind: str, source_ids: list[str], error: str,
) -> ProjectionOutcome:
    """A queue row per source, or `lost` if even that cannot be written. A
    media row is keyed by its `sha256`, which re-projects every attachment of
    those bytes; every other row by its source's own id."""
    stmt = pg_insert(MEMORY_PROJECTION_QUEUE).values(
        [{"kind": kind, "source_id": sid, "last_error": error} for sid in source_ids]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[MEMORY_PROJECTION_QUEUE.c.kind, MEMORY_PROJECTION_QUEUE.c.source_id],
        set_={
            "attempts": MEMORY_PROJECTION_QUEUE.c.attempts + 1,
            "last_error": stmt.excluded.last_error,
        },
    )
    try:
        async with conn.begin_nested():
            await conn.execute(stmt)
    except Exception as exc:  # noqa: BLE001 — counted `lost`; the store write stands
        log.warning(
            "memory projection queue write failed (%s, %s); only a rebuild finds it",
            kind, type(exc).__name__,
        )
        return "lost"
    return "queued"


# --- messages and their attachments ------------------------------------------


def _attachment_id(message_id: str, sha256: str) -> str:
    """An attachment is one sha256 in one message (15 «Every store»): the same
    bytes shared twice are two attachments with two scopes."""
    return f"{message_id}:{sha256}"


def _reading(title: str, description: str, content: str) -> str:
    """The text an attachment is found by: the three comprehension fields
    `media_objects` holds (25 §7), in the order a reader would want them."""
    return "\n".join(part for part in (title, description, content) if part)


async def _project_message(conn: AsyncConnection, source_id: str) -> None:
    # Newest edit BY ARRIVAL (`id`), `nora.infra.sql.audit._latest_edit`'s
    # rule: the last edit written is the current text.
    latest = (
        sa.select(MESSAGE_EDIT.c.id, MESSAGE_EDIT.c.body)
        .where(MESSAGE_EDIT.c.message_id == MESSAGES.c.id)
        .order_by(MESSAGE_EDIT.c.id.desc())
        .limit(1)
        .lateral("latest_edit")
    )
    row = (await conn.execute(
        sa.select(
            MESSAGES.c.platform, MESSAGES.c.conversation_id, MESSAGES.c.ts,
            MESSAGES.c.body, MESSAGES.c.is_nora, MESSAGES.c.person_id,
            MESSAGES.c.media_json, MESSAGES.c.hidden_at,
            latest.c.id.label("edit_id"), latest.c.body.label("edit_body"),
        )
        .select_from(MESSAGES.outerjoin(latest, sa.true()))
        .where(MESSAGES.c.id == int(source_id))
    )).first()
    if row is None or row.hidden_at is not None:
        # Gone (pruned) or deleted on the platform: never a hit again (15
        # «Three rules»). Nothing derived from it is withdrawn.
        await _drop(conn, "message", [source_id])
        return
    room = room_key(row.platform, row.conversation_id)
    text = (row.edit_body if row.edit_id is not None else row.body) or ""
    # Her own words are authored by Nora (25 §2); a human row by the person
    # the roster resolved, once it has (`set_person_id` re-projects).
    author: tuple[str, str, str] | None = None
    if row.is_nora:
        author = ("nora", "nora", "messages.is_nora")
    elif row.person_id:
        author = ("person", row.person_id, "messages.person_id")

    await _upsert_node(
        conn, kind="message", source_id=source_id, origin_room=room,
        text=text, at_ms=row.ts,
    )
    await _hub(conn, "room", room, origin_room=room)
    edges = [("message", source_id, "in_room", "room", room, "messages.conversation_id")]
    if author is not None:
        await _hub(conn, author[0], author[1])
        edges.append(("message", source_id, "authored_by", author[0], author[1], author[2]))

    shas = list(dict.fromkeys(shas_in(row.media_json)))
    wanted = {_attachment_id(source_id, sha): sha for sha in shas}
    held = set((await conn.execute(
        sa.select(MEMORY_EDGE.c.src_id).where(
            MEMORY_EDGE.c.rel == "attached_to",
            MEMORY_EDGE.c.dst_kind == "message", MEMORY_EDGE.c.dst_id == source_id,
        )
    )).scalars())
    await _delete(conn, "media", sorted(held - set(wanted)))
    readings: dict[str, str] = {}
    if shas:
        readings = {
            r.sha256: _reading(r.title, r.description, r.content)
            for r in await conn.execute(
                sa.select(
                    MEDIA_OBJECTS.c.sha256, MEDIA_OBJECTS.c.title,
                    MEDIA_OBJECTS.c.description, MEDIA_OBJECTS.c.content,
                ).where(MEDIA_OBJECTS.c.sha256.in_(shas))
            )
        }
    for att, sha in wanted.items():
        await _upsert_node(
            conn, kind="media", source_id=att, origin_room=room,
            text=readings.get(sha, ""), at_ms=row.ts, sha256=sha,
        )
        edges.append(("media", att, "attached_to", "message", source_id, "messages.media_json"))
        if author is not None:
            edges.append(("media", att, "shared_by", author[0], author[1], author[2]))

    # Outgoing edges are rewritten whole: a re-attributed message must not keep
    # the edge to nobody it had before.
    for kind, ids in (("message", [source_id]), ("media", list(wanted))):
        if ids:
            await conn.execute(sa.delete(MEMORY_EDGE).where(
                MEMORY_EDGE.c.src_kind == kind, _among(MEMORY_EDGE.c.src_id, ids),
            ))
    await conn.execute(pg_insert(MEMORY_EDGE).values([
        {"src_kind": sk, "src_id": si, "rel": rel, "dst_kind": dk, "dst_id": di,
         "provenance": prov}
        for sk, si, rel, dk, di, prov in edges
    ]))


async def _reproject_media(conn: AsyncConnection, sha256: str) -> None:
    row = (await conn.execute(
        sa.select(
            MEDIA_OBJECTS.c.title, MEDIA_OBJECTS.c.description, MEDIA_OBJECTS.c.content,
        ).where(MEDIA_OBJECTS.c.sha256 == sha256)
    )).first()
    text = "" if row is None else _reading(row.title, row.description, row.content)
    await conn.execute(
        sa.update(MEMORY_NODE)
        .where(MEMORY_NODE.c.kind == "media", MEMORY_NODE.c.sha256 == sha256)
        .values(text=text, projected_at=sa.func.clock_timestamp())
    )


_PROJECTORS: dict[str, Callable[[AsyncConnection, str], Awaitable[None]]] = {
    "message": _project_message,
}


# --- the writes --------------------------------------------------------------


async def _upsert_node(
    conn: AsyncConnection, *, kind: str, source_id: str, origin_room: str | None,
    text: str, at_ms: int | None, sha256: str | None = None,
) -> None:
    stmt = pg_insert(MEMORY_NODE).values(
        kind=kind, source_id=source_id, origin_room=origin_room, text=text,
        at_ms=at_ms, sha256=sha256, state="live",
    )
    await conn.execute(stmt.on_conflict_do_update(
        constraint="uq_memory_node_kind_source",
        set_={
            "origin_room": stmt.excluded.origin_room,
            "text": stmt.excluded.text,
            "at_ms": stmt.excluded.at_ms,
            "sha256": stmt.excluded.sha256,
            "state": stmt.excluded.state,
            "projected_at": sa.func.clock_timestamp(),
        },
    ))


async def _hub(
    conn: AsyncConnection, kind: str, source_id: str, *, origin_room: str | None = None,
) -> None:
    """An entity node, written the first time an edge needs it. S2 gives it
    its name; until then it is a bare endpoint with no text."""
    await conn.execute(
        pg_insert(MEMORY_NODE)
        .values(kind=kind, source_id=source_id, origin_room=origin_room, state="live")
        .on_conflict_do_nothing(constraint="uq_memory_node_kind_source")
    )


async def _drop(conn: AsyncConnection, kind: str, ids: list[str]) -> None:
    if kind == "message":
        attachments = list((await conn.execute(
            sa.select(MEMORY_EDGE.c.src_id).where(
                MEMORY_EDGE.c.rel == "attached_to",
                MEMORY_EDGE.c.dst_kind == "message", _among(MEMORY_EDGE.c.dst_id, ids),
            )
        )).scalars())
        await _delete(conn, "media", attachments)
    await _delete(conn, kind, ids)


def _among(column: sa.ColumnElement[Any], ids: list[str]) -> sa.ColumnElement[bool]:
    """`column = ANY(:ids)` with ONE array parameter: a retention prune drops
    every message past the window in one call, and `IN` would bind one
    parameter per id, past asyncpg's 32,767 on a large first prune."""
    return column == sa.any_(sa.bindparam(None, ids, type_=pg.ARRAY(sa.Text)))


async def _delete(conn: AsyncConnection, kind: str, ids: list[str]) -> None:
    """Nodes and every edge touching them, in or out."""
    if not ids:
        return
    await conn.execute(sa.delete(MEMORY_EDGE).where(sa.or_(
        sa.and_(MEMORY_EDGE.c.src_kind == kind, _among(MEMORY_EDGE.c.src_id, ids)),
        sa.and_(MEMORY_EDGE.c.dst_kind == kind, _among(MEMORY_EDGE.c.dst_id, ids)),
    )))
    await conn.execute(sa.delete(MEMORY_NODE).where(
        MEMORY_NODE.c.kind == kind, _among(MEMORY_NODE.c.source_id, ids),
    ))
