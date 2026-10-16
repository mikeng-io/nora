"""The memory index's one writer: `project`, `reproject_media`, `drop`.

Each store method that writes an indexed table calls one of these INSIDE its
own `engine.begin()` block, on the same connection, beside the write it
projects: Nora has no shared unit of work, so the store's transaction is the
one place the source row and its node can change together.
`tests/architecture/test_every_indexed_kind_is_projected.py` holds every
write to an `INDEXED_WRITES` table to that, and holds the `memory_*` tables to this module.

`project(conn, kind, source_id)` re-reads the source row on the caller's
connection and upserts its node and its column edges, or drops them when the
row is hidden or gone, so one call serves an insert, an edit, a hide and an
attribution alike. Every call runs in a SAVEPOINT: a projection that fails
rolls back only itself, puts the source on `memory_projection_queue` and is
counted `nora_memory_projection_total{outcome="queued"}`. It never undoes the
store's own write: a message the index missed is a search miss until
the next pass; a message lost because its index row failed is a lost message.

It projects messages and their attachments, the Notebook (notes, memos,
lessons, events, culture and Character accounts), the social stream's items
and their media, and the entity hubs they point at.
`nora.infra.sql.memory_read` reads the index.

**What a node's text holds** (R-18, R-28): a message's body, and an
attachment's reading. Never a sender id, a sender name, a raw envelope or a
coordinate; the person and room are edges to hub nodes, keyed by the
roster's `person_id` and `nora.domain.rollup.room_key`. Hub nodes carry no
text, except a venue's place node, which carries the venue's name (never its
address or coordinates). A failure's `last_error` is the exception's CLASS
only: a driver error's message carries the statement's parameters, which here
are message bodies.

**What a Notebook node is.** One node per entry, keyed by the entry's own id
(a culture key for a culture or Character account), holding its CURRENT
head: re-projected on every revision, so a search never matches a body the
entry no longer says. Retired and superseded entries keep their node, marked. An
entry's scope is the scope its own store reads it by: a note,
memo, lesson or event its unit; a room's culture its room; a person's
account its unit AND that person, so it is readable only where a room lists
them. A pre-C2b ROOM-keyed person account is never indexed:
it was written from one room's messages, and a unit scope would
show it in every room of the unit.

**`cites` comes only from a stored citations column**: memos,
lessons and Character revisions. A message id becomes an edge to its
message node; a `rollup(outing|stay|trip|...)` key an edge to that episode's
key, as cited. A key of a kind with no node (a co-location, daybook,
tie or response) and a `response:<n>` episode are skipped. Notes and events store no
citations (a note's sit in its body's brackets), so they have none.

**What a Rollup episode is.** An outing, a stay or a trip of the
CURRENT build, keyed by its citation `(subject, interval)`
(`nora.domain.rollup.episode_key`), never by its row id: a rebuild that
recomputes the same happening keeps the key, so a memo's `cites` edge reaches
it again. A build still `building` has no nodes; `switch_build` drops the old
build's and projects the new one's in its own transaction. An episode's
`cites` edges INTO it belong to the entry that cites it and outlive the node
(a dangling edge drops itself at the hop's join, 0105); its `during` edges
belong to the episode, and are rewritten with it.

Its text is the kind and the interval only. Never a place: a label can be a
saved place's name, whose existence is itself secret (R-35), or a geocoder's
street, which is an address; never a coordinate (R-28); never a person, who
is an `about` edge. Its scope is the narrowest its rows support: an outing is
its unit AND every person in it (readable only in a room of the unit that
lists them all); a stay or trip, which carries no unit and no room, is
its person alone. Both copy the row's presence lineage, which a reader must
pass as `rollup_read.visible_rows` does (R-31, DEC-222): the scope
alone never discloses a place.

**What a social item is** (design/cognition-loop/22 §9, the operator's
rulings). One `social` node per `social_item`, keyed `network:item_id`, and
one `social_media` node per medium of it, `(item, sha256)` in `media_ref`'s
order, `attached_to` the item: a carousel is several. Both are their
author's: `persons` is the item's person, no unit and no room, so they are
readable only in a room that lists that person (`PERSON_SCOPED_KINDS`). The
item's text is what it is (`social_label`: «Instagram story»), its caption
verbatim (the author's own text, as a message body is) and the reading of
each medium (`media_objects`, keyed by the bytes, whichever pass wrote it); a
medium's text is its reading, and an image is embedded from its bytes like
an attachment. Never the item's `location`, never its `alt_text` (the
network's generated alt text is phrased with the place and the tagged
handles), and never its `tagged` or `mentioned` handles, which get no edge
either: only the author is a person here (`authored_by`, `shared_by`). An item with
a `social_item_state` row (gone or hidden) has no node, like a hidden
message. A person's `social = false` is not read here: the projector is pure,
and the read leaves their items out (`MemoryScope.social_off`), so switching
it back needs no rebuild.

A snapshot restore copies none of the four tables (`NOT_RESTORED`): the
restored index is empty and reads `never` until a rebuild.

**The vector.** The node
is its own embedding queue: an upsert that changes what a node is embedded
from clears its vector and the worker's bookkeeping with it, and one that
does not keeps them, so a re-projection of an unchanged row costs no model
call and a rebuild equals live. The worker records what it embedded from
(`embed_input`): the text, or for an image attachment whose bytes it held,
those bytes, which the node's `sha256` names. So a reading landing on an
image later does not clear its vector, and one landing on an image that was
embedded from its reading does. The worker's reads and writes are `embed_pick`,
`embed_store` and `embed_failed` here, the only module that writes the
index; `nora.infra.sql.memory_embed` drives them.

**What a delete removes**: a node and the edges its own projection
writes, never an edge another node's projection owns. The edges into a
hidden message from the memos that cite it stay, dangling, exactly as a
rebuild writes them, so a live delete and a rebuild agree; a hop joins
`memory_node`, so a dangling edge drops itself at the read.

**`rebuild`** walks every source through `project` and
`reproject_episodes`, the functions live writes call, so a rebuild equals
live projection by construction; `index_status` is what a reader needs
to tell `ok` from `partial`.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NamedTuple

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.culture import split_culture_key
from nora.domain.media import blobs_from_media_json
from nora.domain.media_join import shas_in
from nora.domain.memory_index import (
    ATTACHMENT_KINDS,
    EMBEDDED_KINDS,
    HUB_KINDS,
    ProjectionOutcome,
    social_label,
)
from nora.domain.rollup import days_touched, episode_key, iso, local_date, room_key
from nora.infra.sql.schema import (
    CULTURE,
    CULTURE_REVISION,
    EMBEDDING_DIMENSIONS,
    EVENT_REVISION,
    EVENT_RSVPS,
    EVENTS,
    LESSON,
    LESSON_REVISION,
    MEDIA_OBJECTS,
    MEMO,
    MEMO_REVISION,
    MEMORY_EDGE,
    MEMORY_INDEX_STATE,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
    MESSAGE_EDIT,
    MESSAGES,
    NOTE,
    NOTE_REVISION,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
    ROLLUP_BUILD,
    ROLLUP_DAYBOOK,
    ROLLUP_OUTING,
    SOCIAL_ITEM,
    SOCIAL_ITEM_STATE,
)

__all__ = [
    "EMBED_MAX_ATTEMPTS",
    "INDEXED_WRITES",
    "REBUILD_LOCK_KEY",
    "EmbedCounts",
    "Embeddable",
    "IndexStatus",
    "RebuildRefusedError",
    "RebuildReport",
    "adopt_worker_state",
    "clear_embeddings",
    "drain_queue",
    "drop",
    "embed_counts",
    "embed_failed",
    "embed_pick",
    "embed_store",
    "index_status",
    "media_types",
    "project",
    "project_episodes",
    "rebuild",
    "reproject_episodes",
    "reproject_media",
    "social_source_id",
    "story_frame_embedded",
]

log = logging.getLogger("nora.infra.sql.memory_index")

#: Every table whose writes change what the index holds, and the node kind
#: they change. `message_edit` is a message's text; `media_objects` is the
#: reading every attachment of those bytes copies in; a revision table is its
#: entry's head; `event_rsvps` adds an `about` edge to its event. The
#: architecture test reads this to find the write paths that must project.
INDEXED_WRITES: dict[str, str] = {
    "messages": "message",
    "message_edit": "message",
    "media_objects": "media",
    "note": "note",
    "note_revision": "note",
    "memo": "memo",
    "memo_revision": "memo",
    "lesson": "lesson",
    "lesson_revision": "lesson",
    "events": "event",
    "event_revision": "event",
    "event_rsvps": "event",
    "culture": "character",
    "culture_revision": "character",
    "rollup_outing": "outing",
    "presence_stays": "stay",
    "presence_trips": "trip",
    # The build pointer: switching it changes which build's outings, stays
    # and trips have nodes (all three kinds).
    "rollup_build": "outing",
    # A social item, its gone-or-hidden state, and a perception landing (the
    # reading it wrote is in `media_objects`, reprojected there as well).
    "social_item": "social",
    "social_item_state": "social",
    "social_perception": "social",
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
    """Remove `kind`'s nodes for `ids` and the edges they own (`_delete`); a
    message takes its attachments with it."""
    sids = [str(i) for i in ids]
    if not sids:
        return
    await _guarded(conn, kind, sids, lambda: _drop(conn, kind, sids), metrics)


async def _guarded(
    conn: AsyncConnection, kind: str, source_ids: list[str],
    work: Callable[[], Awaitable[None]], metrics: Any,
) -> None:
    """The savepoint every projection runs in. `begin_nested` rolls
    back the projection alone; the caller's transaction, and the store write
    in it, carry on. A projection that lands clears any queue row its source
    left behind, in the same savepoint, so the queue holds only what is still
    unprojected (its length is the index's health)."""
    outcome: ProjectionOutcome = "ok"
    try:
        async with conn.begin_nested():
            await work()
            await conn.execute(sa.delete(MEMORY_PROJECTION_QUEUE).where(
                MEMORY_PROJECTION_QUEUE.c.kind == kind,
                _among(MEMORY_PROJECTION_QUEUE.c.source_id, source_ids),
            ))
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
    those bytes; every other row by its source's own id.

    ONE statement over `unnest` of one array parameter, never a VALUES row per
    id: a failed `drop` inside a large retention prune queues every pruned id,
    and three parameters per id passes asyncpg's 32,767 at ~10,900 ids, which
    would leave pruned text searchable with nothing queued to remove it.
    Deduplicated, because one upsert cannot touch the same row twice."""
    ids = list(dict.fromkeys(source_ids))
    stmt = pg_insert(MEMORY_PROJECTION_QUEUE).from_select(
        ["kind", "source_id", "last_error"],
        sa.select(
            sa.literal(kind, sa.Text),
            sa.func.unnest(sa.bindparam("ids", ids, type_=pg.ARRAY(sa.Text))),
            sa.literal(error, sa.Text),
        ),
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
    `media_objects` holds, in the order a reader would want them."""
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
    # Her own words are authored by Nora; a human row by the person
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
    # the edge to nobody it had before. A `during` edge is the episode's, which
    # rewrites it when it is projected (S3), so an edit keeps it.
    ends: list[tuple[str, str]] = []
    for kind, ids in (("message", [source_id]), ("media", list(wanted))):
        if ids:
            ends += await _delete_edges(conn, sa.and_(
                MEMORY_EDGE.c.src_kind == kind, _among(MEMORY_EDGE.c.src_id, ids),
                MEMORY_EDGE.c.rel != "during",
            ))
    await _insert_edges(conn, edges)
    # A re-attributed message may have been its former author's last edge.
    await _drop_orphans(conn, ends)


async def _reproject_media(conn: AsyncConnection, sha256: str) -> None:
    row = (await conn.execute(
        sa.select(
            MEDIA_OBJECTS.c.title, MEDIA_OBJECTS.c.description, MEDIA_OBJECTS.c.content,
        ).where(MEDIA_OBJECTS.c.sha256 == sha256)
    )).first()
    text = "" if row is None else _reading(row.title, row.description, row.content)
    # A new reading clears a vector made from the old one and keeps one made
    # from the bytes (`_input_changed`). In an UPDATE the right-hand side
    # reads the row as it was, so the comparison is old text to new.
    await conn.execute(
        sa.update(MEMORY_NODE)
        .where(MEMORY_NODE.c.kind == "media", MEMORY_NODE.c.sha256 == sha256)
        .values(
            text=text, projected_at=sa.func.clock_timestamp(),
            **_cleared_when(_input_changed(text, MEMORY_NODE.c.sha256)),
        )
    )
    # A social item's text holds its media's readings, so each item holding
    # these bytes is projected again, its media with it.
    held = (await conn.execute(
        sa.select(MEMORY_NODE.c.source_id).where(
            MEMORY_NODE.c.kind == "social_media", MEMORY_NODE.c.sha256 == sha256,
        )
    )).scalars()
    for item in sorted({sid.rpartition(":")[0] for sid in held}):
        await _project_social(conn, item)


async def media_types(
    conn: AsyncConnection, attachments: Iterable[tuple[str, str]],
    social: Iterable[tuple[str, str]] = (),
) -> dict[str, str]:
    """The type each medium's bytes are recorded as, keyed by its node's
    `source_id`; `""` for one whose message or entry is gone. An attachment's
    is what its own message records (`messages.media_json`); a social item's
    medium has no message, so its is what custody recorded for those bytes
    (`media_objects.media_type`). The one reading of whether a medium is an
    image, for the projector and the embedding worker alike."""
    wanted = dict(attachments)
    out: dict[str, str] = {}
    ids = sorted({int(sid.partition(":")[0]) for sid in wanted})
    if ids:
        held = {
            r.id: r.media_json
            for r in await conn.execute(
                sa.select(MESSAGES.c.id, MESSAGES.c.media_json).where(MESSAGES.c.id.in_(ids))
            )
        }
        for sid, sha in wanted.items():
            blobs = blobs_from_media_json(held.get(int(sid.partition(":")[0])))
            out[sid] = next((b.media_type for b in blobs if b.sha256 == sha), "")
    others = dict(social)
    if others:
        types = dict((await conn.execute(
            sa.select(MEDIA_OBJECTS.c.sha256, MEDIA_OBJECTS.c.media_type)
            .where(MEDIA_OBJECTS.c.sha256.in_(sorted(set(others.values()))))
        )).tuples().all())
        out.update({sid: types.get(sha, "") for sid, sha in others.items()})
    return out


# --- the social stream ---------------------------------------------------------


def social_source_id(network: str, item_id: str) -> str:
    """A social item's node id: its natural key, `(network, item_id)`. A
    network name holds no `:`, so the first one splits it back."""
    return f"{network}:{item_id}"


def _social_media_ids(item: str, shas: Iterable[str]) -> dict[str, str]:
    """A social item's media nodes, `(item, sha256)` in its order: the
    attachment's shape (`_attachment_id`), the sha256 last so the item is
    everything before the final `:`."""
    return {_attachment_id(item, sha): sha for sha in dict.fromkeys(shas)}


async def _project_social(conn: AsyncConnection, source_id: str) -> None:
    network, _, item_id = source_id.partition(":")
    i, st = SOCIAL_ITEM, SOCIAL_ITEM_STATE
    stated = sa.exists().where(st.c.network == i.c.network, st.c.item_id == i.c.item_id)
    row = (await conn.execute(
        sa.select(
            i.c.person, i.c.kind, i.c.taken_at, i.c.caption, i.c.media_ref,
            i.c.collab, stated.label("stated"),
        ).where(i.c.network == network, i.c.item_id == item_id)
    )).first()
    if row is None or row.stated:
        # Gone, hidden or never recorded: never a hit (§2), like a hidden
        # message; both states hide, so any row of either is enough.
        await _drop(conn, "social", [source_id])
        return
    shas = [sha for sha in (row.media_ref or "").split(",") if sha]
    wanted = _social_media_ids(source_id, shas)
    readings: dict[str, str] = {}
    if wanted:
        readings = {
            r.sha256: _reading(r.title, r.description, r.content)
            for r in await conn.execute(
                sa.select(
                    MEDIA_OBJECTS.c.sha256, MEDIA_OBJECTS.c.title,
                    MEDIA_OBJECTS.c.description, MEDIA_OBJECTS.c.content,
                ).where(MEDIA_OBJECTS.c.sha256.in_(sorted(set(wanted.values()))))
            )
        }
    at_ms = int(row.taken_at.timestamp() * 1000)
    persons = [row.person]
    # Never `alt_text`, `location`, `tagged` or `mentioned` (the module
    # docstring).
    await _upsert_node(
        conn, kind="social", source_id=source_id, persons=persons, at_ms=at_ms,
        text=_text(
            social_label(network, row.kind, collab=row.collab), row.caption,
            *(readings.get(sha, "") for sha in wanted.values()),
        ),
    )
    held = set((await conn.execute(
        sa.select(MEMORY_EDGE.c.src_id).where(
            MEMORY_EDGE.c.rel == "attached_to",
            MEMORY_EDGE.c.dst_kind == "social", MEMORY_EDGE.c.dst_id == source_id,
        )
    )).scalars())
    await _delete(conn, "social_media", sorted(held - set(wanted)))
    await _hub(conn, "person", row.person)
    edges: list[Edge] = [
        ("social", source_id, "authored_by", "person", row.person, "social_item.person"),
    ]
    for media, sha in wanted.items():
        await _upsert_node(
            conn, kind="social_media", source_id=media, persons=persons, at_ms=at_ms,
            text=readings.get(sha, ""), sha256=sha,
        )
        edges += [
            ("social_media", media, "attached_to", "social", source_id, "social_item.media_ref"),
            ("social_media", media, "shared_by", "person", row.person, "social_item.person"),
        ]
    ends: list[tuple[str, str]] = []
    for kind, ids in (("social", [source_id]), ("social_media", list(wanted))):
        if ids:
            ends += await _delete_edges(conn, sa.and_(
                MEMORY_EDGE.c.src_kind == kind, _among(MEMORY_EDGE.c.src_id, ids),
            ))
    await _insert_edges(conn, edges)
    await _drop_orphans(conn, ends)


async def story_frame_embedded(
    conn: AsyncConnection, frames: Iterable[tuple[str, str]], *, model: str,
) -> set[tuple[str, str]]:
    """Of `frames` (`(network, item_id)`), those whose embedding is done, for
    `StoryMediaExpiry` (design/cognition-loop/22 §5): the frame has at least
    one medium node, and every one of them holds a vector of `model` or is
    terminal, given up (`EMBED_MAX_ATTEMPTS`) or failed `empty`, which no
    later tick will change. A frame with no medium node (never projected,
    gone or hidden, or projected under a failure still queued) is not
    embedded, so its bytes wait for the hard limit: keeping bytes longer is
    the safe side of the bound."""
    by_id = {social_source_id(n, i): (n, i) for n, i in frames}
    if not by_id:
        return set()
    node, e = MEMORY_NODE.c, MEMORY_EDGE.c
    # Never NULL: `bool_and` skips a NULL, so a pending medium whose
    # `embed_error` is NULL would otherwise read as done.
    done = sa.or_(
        sa.and_(node.embedding.isnot(None), node.embedding_model.is_not_distinct_from(model)),
        sa.and_(
            node.embedding.is_(None),
            sa.or_(
                node.embed_attempts >= EMBED_MAX_ATTEMPTS,
                node.embed_error.is_not_distinct_from("empty"),
            ),
        ),
    )
    rows = await conn.execute(
        sa.select(e.dst_id)
        .select_from(MEMORY_EDGE.join(MEMORY_NODE, sa.and_(
            node.kind == e.src_kind, node.source_id == e.src_id,
        )))
        .where(
            e.rel == "attached_to", e.src_kind == "social_media", e.dst_kind == "social",
            _among(e.dst_id, sorted(by_id)),
        )
        .group_by(e.dst_id)
        .having(sa.func.bool_and(done))
    )
    return {by_id[sid] for sid in rows.scalars()}


# --- the Notebook ------------------------------------------------------------

#: The Rollup kinds that are episodes with nodes of their own.
_EPISODES = ("outing", "stay", "trip")
#: A cited Rollup key as a citations column stores it (`nora.domain.memo`).
_ROLLUP_CITATION = re.compile(r"rollup\((?P<kind>[a-z]+)\|(?P<key>[^()]+)\)")

Edge = tuple[str, str, str, str, str, str]

#: An event that will not happen, or was put out of the register, is history
#: rather than a plan: `retired`, still found, never read as current.
_EVENT_RETIRED = frozenset({"cancelled", "lapsed", "evicted"})


def _text(*parts: str | None) -> str:
    return "\n".join(p for p in parts if p)


async def _room_of(conn: AsyncConnection, conversation_id: str) -> str | None:
    """The room key of a bare `conversation_id`, from the Record (the stores
    that keep only the id, `nora.infra.sql.event_evidence._room_key_of`'s
    rule); `None` when the Record holds no message of it."""
    platform = (await conn.execute(
        sa.select(MESSAGES.c.platform)
        .where(MESSAGES.c.conversation_id == conversation_id)
        .order_by(MESSAGES.c.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    return None if platform is None else room_key(platform, conversation_id)


def _cites(kind: str, source_id: str, citations: Iterable[str], prov: str) -> list[Edge]:
    """`cites` edges to the nodes a citations column names: a message id to
    its message, a `rollup(kind|subject|from/to)` key of an episode kind to
    that episode's key, which is the cited text itself (`episode_key`), so it
    needs no lookup and reaches the node of whichever build is current. Any
    other key has no node and is skipped."""
    out: list[Edge] = []
    for c in dict.fromkeys(c.strip() for c in citations):
        if c.isdigit():
            out.append((kind, source_id, "cites", "message", c, prov))
            continue
        m = _ROLLUP_CITATION.fullmatch(c)
        if m is not None and m["kind"] in _EPISODES:
            out.append((kind, source_id, "cites", m["kind"], m["key"].strip(), prov))
    return out


async def _entry_edges(
    conn: AsyncConnection, kind: str, source_id: str, edges: list[Edge],
) -> None:
    """Replace an entry's edges with `edges`, upserting the hubs they reach.

    A `supersedes` edge belongs to the entry it points AT, because the
    superseded entry's own column (`note.replaced_by`) is what records it: an
    entry re-projecting drops its outgoing edges except `supersedes`, and the
    `supersedes` edges into it, and writes both afresh from its row."""
    for _, _, _, dk, di, _ in edges:
        if dk in ("person", "room", "unit", "nora"):
            await _hub(conn, dk, di, origin_room=di if dk == "room" else None)
    ends = await _delete_edges(conn, sa.or_(
        sa.and_(
            MEMORY_EDGE.c.src_kind == kind, MEMORY_EDGE.c.src_id == source_id,
            MEMORY_EDGE.c.rel != "supersedes",
        ),
        sa.and_(
            MEMORY_EDGE.c.dst_kind == kind, MEMORY_EDGE.c.dst_id == source_id,
            MEMORY_EDGE.c.rel == "supersedes",
        ),
    ))
    await _insert_edges(conn, edges)
    # An `about` target the entry no longer names may have no edge left.
    await _drop_orphans(conn, ends)


async def _project_note(conn: AsyncConnection, source_id: str) -> None:
    row = (await conn.execute(
        sa.select(
            NOTE.c.unit, NOTE.c.title, NOTE.c.established_in, NOTE.c.retired_at,
            NOTE.c.replaced_by, NOTE.c.event_id, NOTE.c.exclude_persons,
            NOTE_REVISION.c.body, NOTE_REVISION.c.written_at,
        )
        .select_from(NOTE.join(NOTE_REVISION, NOTE_REVISION.c.id == NOTE.c.head_revision_id))
        .where(NOTE.c.id == source_id)
    )).first()
    if row is None:
        await _drop(conn, "note", [source_id])
        return
    state = (
        "live" if row.retired_at is None
        else "superseded" if row.replaced_by is not None else "retired"
    )
    await _upsert_node(
        conn, kind="note", source_id=source_id, unit=row.unit,
        exclude_persons=row.exclude_persons, text=_text(row.title, row.body),
        at_ms=row.written_at, state=state,
    )
    # `anchor_entity_ids` is the writer's free text, «named the way this group
    # names them», never a resolved `person_id`: an `about` edge from it would
    # name a person hub by a guess. It waits for identity resolution.
    edges: list[Edge] = []
    room = await _room_of(conn, row.established_in)
    if room is not None:
        edges.append(("note", source_id, "in_room", "room", room, "note.established_in"))
    if row.event_id is not None:
        edges.append(("note", source_id, "tracks", "event", str(row.event_id), "note.event_id"))
    if row.replaced_by is not None:
        edges.append((
            "note", str(row.replaced_by), "supersedes", "note", source_id, "note.replaced_by",
        ))
    await _entry_edges(conn, "note", source_id, edges)


async def _project_memo(conn: AsyncConnection, source_id: str) -> None:
    top = MEMO_REVISION.alias("top")
    newest = sa.select(sa.func.max(top.c.seq)).where(top.c.memo_id == MEMO.c.id)
    row = (await conn.execute(
        sa.select(
            MEMO.c.unit, MEMO.c.platform, MEMO.c.conversation_id, MEMO_REVISION.c.title,
            MEMO_REVISION.c.body, MEMO_REVISION.c.citations, MEMO_REVISION.c.state,
            MEMO_REVISION.c.written_at,
        )
        .select_from(MEMO.join(MEMO_REVISION, sa.and_(
            MEMO_REVISION.c.memo_id == MEMO.c.id,
            MEMO_REVISION.c.seq == newest.scalar_subquery(),
        )))
        .where(MEMO.c.id == source_id)
    )).first()
    if row is None:
        await _drop(conn, "memo", [source_id])
        return
    await _upsert_node(
        conn, kind="memo", source_id=source_id, unit=row.unit,
        text=_text(row.title, row.body), at_ms=row.written_at,
        state="live" if row.state == "kept" else "retired",
    )
    room = room_key(row.platform, row.conversation_id)
    edges = [("memo", source_id, "in_room", "room", room, "memo.conversation_id")]
    edges += _cites("memo", source_id, row.citations, "memo_revision.citations")
    await _entry_edges(conn, "memo", source_id, edges)


async def _project_lesson(conn: AsyncConnection, source_id: str) -> None:
    row = (await conn.execute(
        sa.select(
            LESSON.c.unit, LESSON_REVISION.c.body, LESSON_REVISION.c.citations,
            LESSON_REVISION.c.state, LESSON_REVISION.c.written_at,
        )
        .select_from(LESSON.join(LESSON_REVISION, LESSON_REVISION.c.lesson_id == LESSON.c.id))
        .where(LESSON.c.id == source_id)
        .order_by(LESSON_REVISION.c.seq.desc())
        .limit(1)
    )).first()
    if row is None:
        await _drop(conn, "lesson", [source_id])
        return
    await _upsert_node(
        conn, kind="lesson", source_id=source_id, unit=row.unit, text=row.body,
        at_ms=row.written_at, state="live" if row.state == "active" else "retired",
    )
    edges = _cites("lesson", source_id, row.citations, "lesson_revision.citations")
    await _entry_edges(conn, "lesson", source_id, edges)


async def _project_event(conn: AsyncConnection, source_id: str) -> None:
    row = (await conn.execute(
        sa.select(
            EVENTS.c.conversation_id, EVENTS.c.unit_id, EVENTS.c.title, EVENTS.c.when,
            EVENTS.c.created_at, EVENTS.c.status, EVENTS.c.people,
            EVENTS.c.exclude_persons, EVENTS.c.venue_text, EVENTS.c.venue_name,
            EVENTS.c.venue_place_id, EVENT_REVISION.c.body,
        )
        .select_from(EVENTS.outerjoin(
            EVENT_REVISION, EVENT_REVISION.c.id == EVENTS.c.head_revision_id,
        ))
        .where(EVENTS.c.id == source_id)
    )).first()
    if row is None:
        await _drop(conn, "event", [source_id])
        return
    room = await _room_of(conn, row.conversation_id)
    # An event is read unit-wide (`SqlGatheringStore.search`). One written
    # before `0074` carries no unit (`unit_id = ''`) and is found only in its
    # own room, so it is scoped to that room; a room the Record cannot name
    # keeps the bare id, which matches no room key and so no read.
    unit = row.unit_id or None
    origin_room = None if unit else (room or row.conversation_id)
    at = row.when or row.created_at
    # The venue by name: the resolved `venue_name`, else the group's own
    # free-text `venue_text`, which is what was said in the room and takes the
    # event's scope like that message. Never the resolved address, map link or
    # coordinates (R-28); the unscoped place hub takes `venue_name` only.
    venue = row.venue_name or row.venue_text
    await _upsert_node(
        conn, kind="event", source_id=source_id, unit=unit, origin_room=origin_room,
        exclude_persons=row.exclude_persons, text=_text(row.title, row.body, venue),
        at_ms=int(at.timestamp() * 1000),
        state="retired" if row.status in _EVENT_RETIRED else "live",
    )
    edges: list[Edge] = []
    if room is not None:
        edges.append(("event", source_id, "in_room", "room", room, "events.conversation_id"))
    rsvps = (await conn.execute(
        sa.select(EVENT_RSVPS.c.person_id).distinct()
        .where(EVENT_RSVPS.c.event_id == source_id)
    )).scalars()
    people = dict.fromkeys(row.people or ())
    edges += [("event", source_id, "about", "person", p, "events.people") for p in people]
    edges += [
        ("event", source_id, "about", "person", p, "event_rsvps.person_id")
        for p in rsvps if p not in people
    ]
    if row.venue_place_id:
        await _named_hub(conn, "place", row.venue_place_id, text=row.venue_name)
        edges.append((
            "event", source_id, "about", "place", row.venue_place_id, "events.venue_place_id",
        ))
    await _entry_edges(conn, "event", source_id, edges)


async def _is_a_unit(conn: AsyncConnection, owner: str) -> bool:
    """Whether the database PROVES `owner` is a unit, without `policy.toml`:
    the unit's own account or Nora's in it exists, or a
    unit-keyed Notebook row names it. The proof is positive because the
    negative one cannot be had: a pre-C2b account's room may have no message
    left (retention) and no culture of its own, and "not seen as a room" then
    read as "a unit". A unit's person account written before any of these
    exists is left out until its next revision or a rebuild re-projects it;
    an account missing from search is a miss, one shown across a unit is a
    disclosure."""
    found = await conn.execute(sa.select(sa.or_(
        sa.exists().where(CULTURE.c.key.in_([f"{owner}#unit", f"{owner}#nora"])),
        sa.exists().where(NOTE.c.unit == owner),
        sa.exists().where(MEMO.c.unit == owner),
        sa.exists().where(LESSON.c.unit == owner),
        sa.exists().where(EVENTS.c.unit_id == owner),
    )))
    return bool(found.scalar())


async def _project_character(conn: AsyncConnection, source_id: str) -> None:
    form, owner, person = split_culture_key(source_id)
    if form == "character" and not (owner and await _is_a_unit(conn, owner)):
        # Indexed only when its owner is provably a unit: a pre-C2b account
        # keyed by a ROOM (`character_key`) is never indexed, and
        # dropped if it ever was.
        await _drop(conn, "character", [source_id])
        return
    row = (await conn.execute(
        sa.select(
            CULTURE_REVISION.c.body, CULTURE_REVISION.c.written_at,
            CULTURE_REVISION.c.citations, CULTURE_REVISION.c.exclude_persons,
        )
        .select_from(CULTURE.join(
            CULTURE_REVISION, CULTURE_REVISION.c.id == CULTURE.c.head_revision_id,
        ))
        .where(CULTURE.c.key == source_id)
    )).first()
    room = await _room_of(conn, owner) if form == "room" else None
    if row is None or (form == "room" and room is None):
        # No head, or a room the Record cannot name: no scope to give it.
        await _drop(conn, "character", [source_id])
        return
    persons: list[str] = []
    about: tuple[str, str]
    if form == "room":
        about = ("room", str(room))
    elif form == "unit":
        about = ("unit", owner)
    elif form == "nora":
        about = ("nora", "nora")
    else:
        # Readable only where a room lists the person.
        persons = [str(person)]
        about = ("person", str(person))
    await _upsert_node(
        conn, kind="character", source_id=source_id,
        unit=None if form == "room" else owner, origin_room=room, persons=persons,
        exclude_persons=row.exclude_persons, text=row.body, at_ms=row.written_at,
    )
    edges: list[Edge] = [("character", source_id, "about", *about, "culture.key")]
    edges += _cites("character", source_id, row.citations, "culture_revision.citations")
    await _entry_edges(conn, "character", source_id, edges)


# --- Rollup episodes ---------------------------------------------------------


class _Episode(NamedTuple):
    """One current-build outing, stay or trip, as its node needs it."""

    kind: str
    key: str
    unit: str | None
    persons: tuple[str, ...]
    start_ms: int
    end_ms: int
    lineage: Any


async def project_episodes(
    conn: AsyncConnection, build_id: int, kind: str, keys: Iterable[str], *,
    metrics: Any = None,
) -> None:
    """`apply` and `commit_fold`: project the episodes of `kind` at `keys`
    (`episode_key`) when `build_id` is the current build, and drop a key the
    build no longer holds (the delete half of a diff, or an open outing that
    grew a new end). A build still `building` gets no nodes.

    The build's row is read FOR SHARE: `switch_build` updates it, so a
    projection of the build being switched out lands before the switch drops
    its nodes, never after."""
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return
    status = (await conn.execute(
        sa.select(ROLLUP_BUILD.c.status).where(ROLLUP_BUILD.c.id == build_id)
        .with_for_update(read=True)
    )).scalar_one_or_none()
    if status != "current":
        return

    async def work() -> None:
        found = await _episodes(conn, kind, build_id, wanted)
        await _drop_episodes(conn, kind, sorted(set(wanted) - {e.key for e in found}))
        await _write_episodes(conn, build_id, found)

    await _guarded(conn, kind, wanted, work, metrics)


async def reproject_episodes(conn: AsyncConnection, *, metrics: Any = None) -> None:
    """`switch_build`, after the pointer moved in the same transaction: every
    episode node of the build switched out is dropped, unless the new build
    holds the same happening under the same key, and every episode of the new
    build is projected. A failure queues every key involved, so the next
    drain finishes the switch one key at a time."""
    for kind in _EPISODES:
        try:
            async with conn.begin_nested():
                build = await _current_build(conn)
                held = set((await conn.execute(
                    sa.select(MEMORY_NODE.c.source_id).where(MEMORY_NODE.c.kind == kind)
                )).scalars())
                found = await _episodes(conn, kind, build, None)
        except Exception as exc:  # noqa: BLE001 — the switch stands; a rebuild repairs
            log.warning(
                "memory projection of a build switch could not read (%s, %s)",
                kind, type(exc).__name__,
            )
            if metrics is not None:
                metrics.inc(_METRIC, kind=kind, outcome="lost")
            continue
        stale = sorted(held - {e.key for e in found})

        async def work(
            kind: str = kind, build: int = build, stale: list[str] = stale,
            found: list[_Episode] = found,
        ) -> None:
            await _drop_episodes(conn, kind, stale)
            await _write_episodes(conn, build, found)

        await _guarded(conn, kind, stale + [e.key for e in found], work, metrics)


class _NoCurrentBuildError(RuntimeError):
    """No build is current: an episode cannot be projected OR dropped, so the
    source stays queued (a fresh database before its first switch)."""


async def _current_build(conn: AsyncConnection) -> int:
    """The current build, its row held FOR SHARE (`project_episodes`).
    `nora.infra.sql.mobility.current_build_id` is the same pointer; it is
    not imported because `mobility` imports this module.

    Read twice when the first read finds none: a read that waited on the row
    a committing `switch_build` held re-checks only that row, now superseded,
    and never sees the build switched in (READ COMMITTED). The second
    statement reads after the switch. Still none raises, never «nothing»: a
    caller that took it for «this build holds no such episode» would drop a
    node the switch had just written."""
    stmt = (
        sa.select(ROLLUP_BUILD.c.id).where(ROLLUP_BUILD.c.status == "current")
        .with_for_update(read=True)
    )
    for _ in range(2):
        build = (await conn.execute(stmt)).scalar_one_or_none()
        if build is not None:
            return int(build)
    raise _NoCurrentBuildError("no Rollup build is current")


def _instant(text: str) -> int | None:
    """One end of a key's interval (`iso`) in epoch ms; `None` if malformed."""
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    return None if at.tzinfo is None else int(at.timestamp() * 1000)


def _episode_projector(kind: str) -> Callable[[AsyncConnection, str], Awaitable[None]]:
    """`project(conn, kind, key)` for one episode (a drained queue row):
    whatever the current build holds at that key, or nothing."""
    async def run(conn: AsyncConnection, key: str) -> None:
        build = await _current_build(conn)
        found = await _episodes(conn, kind, build, [key])
        if not found:
            await _drop_episodes(conn, kind, [key])
            return
        await _write_episodes(conn, build, found)
    return run


async def _episodes(
    conn: AsyncConnection, kind: str, build: int, keys: list[str] | None,
) -> list[_Episode]:
    """`build`'s rows of `kind` whose key is one of `keys`, or all of them.
    The read narrows on the keys' subjects and start range, then keeps the
    rows whose own key is asked: a key is printed to the second, so equality
    of keys is `rollup_read`'s matching of a citation within its second."""
    table = ROLLUP_OUTING if kind == "outing" else _PRESENCE[kind]
    start = table.c.valid_from_ms if kind == "outing" else table.c.started_ts
    where: list[Any] = [table.c.build_id == build]
    asked = None if keys is None else set(keys)
    if keys is not None:
        parsed = [p for k in keys if (p := _split_key(k)) is not None]
        if not parsed:
            return []
        subjects = sorted({p[0] for p in parsed})
        where += [
            start >= min(p[1] for p in parsed), start < max(p[1] for p in parsed) + 1000,
        ]
        if kind == "outing":
            where.append(table.c.unit.in_(sorted({s.partition("|")[0] for s in subjects})))
        else:
            where.append(table.c.subject_id.in_(subjects))
    if kind == "outing":
        stmt = sa.select(
            table.c.unit, table.c.persons, table.c.valid_from_ms.label("start"),
            table.c.valid_to_ms.label("end"), table.c.lineage,
        )
    else:
        stmt = sa.select(
            table.c.subject_id, table.c.started_ts.label("start"),
            table.c.ended_ts.label("end"), table.c.lineage,
        )
    out: dict[str, _Episode] = {}
    for r in await conn.execute(stmt.where(*where).order_by(start, table.c.id)):
        if kind == "outing":
            unit, persons = r.unit, tuple(r.persons)
            subject = f"{unit}|{','.join(persons)}"
        else:
            unit, persons, subject = None, (r.subject_id,), r.subject_id
        key = episode_key(subject, r.start, r.end)
        if asked is None or key in asked:
            out.setdefault(key, _Episode(kind, key, unit, persons, r.start, r.end, r.lineage))
    return list(out.values())


def _split_key(key: str) -> tuple[str, int, int] | None:
    subject, _, interval = key.rpartition("|")
    start_text, _, end_text = interval.partition("/")
    start, end = _instant(start_text), _instant(end_text)
    if not subject or start is None or end is None:
        return None
    return subject, start, end


async def _write_episodes(conn: AsyncConnection, build: int, found: list[_Episode]) -> None:
    """Upsert each episode's node and rewrite the edges it owns: `about` its
    persons, and the `during` edges into it."""
    if not found:
        return
    ends: list[tuple[str, str]] = []
    for kind in dict.fromkeys(e.kind for e in found):
        keys = [e.key for e in found if e.kind == kind]
        ends += await _delete_edges(conn, sa.or_(
            sa.and_(MEMORY_EDGE.c.src_kind == kind, _among(MEMORY_EDGE.c.src_id, keys)),
            sa.and_(
                MEMORY_EDGE.c.dst_kind == kind, _among(MEMORY_EDGE.c.dst_id, keys),
                MEMORY_EDGE.c.rel == "during",
            ),
        ))
    await _upsert_nodes(conn, [
        _node_values(
            kind=e.kind, source_id=e.key, unit=e.unit, persons=e.persons,
            lineage=e.lineage, at_ms=e.start_ms,
            # The kind and when, nothing else (module docstring): no place,
            # no coordinate, no person.
            text=f"{e.kind} {iso(e.start_ms)} to {iso(e.end_ms)}",
        )
        for e in found
    ])
    edges: list[Edge] = []
    for person in sorted({p for e in found for p in e.persons}):
        await _hub(conn, "person", person)
    for e in found:
        prov = (
            "rollup_outing.persons" if e.kind == "outing"
            else f"{_PRESENCE[e.kind].name}.subject_id"
        )
        edges += [(e.kind, e.key, "about", "person", p, prov) for p in e.persons]
        if e.kind == "outing":
            edges += await _during(conn, build, e)
    await _insert_edges(conn, edges)
    await _drop_orphans(conn, ends)


async def _during(conn: AsyncConnection, build: int, e: _Episode) -> list[Edge]:
    """`during` edges from the messages the outing's people wrote in the
    unit's rooms inside its interval. The unit's rooms are the policy's
    (`nora.infra` cannot read it); the build's daybooks name every room of the
    unit each person wrote in on a day, so the rooms are read from those.

    A stay or trip carries no unit, so it has no unit rooms and no `during`
    edge: every room the person wrote in would reach their whereabouts.
    """
    # ponytail: messages are matched by the person id the roster resolved, so
    # a renamed person's messages under a former id get no edge (a miss, never
    # a leak); the daybooks' days are padded a day each side for the same
    # reason, a deployment whose `tz_name` is not Hong Kong's.
    days = [local_date(e.start_ms - 86_400_000), *days_touched(e.start_ms, e.end_ms),
            local_date(e.end_ms + 86_400_000)]
    rooms: set[str] = set()
    for books in (await conn.execute(
        sa.select(ROLLUP_DAYBOOK.c.rooms).where(
            ROLLUP_DAYBOOK.c.build_id == build, ROLLUP_DAYBOOK.c.unit == e.unit,
            ROLLUP_DAYBOOK.c.person_id.in_(list(e.persons)),
            ROLLUP_DAYBOOK.c.local_date.in_(sorted(set(days))),
        )
    )).scalars():
        rooms.update(books or {})
    if not rooms:
        return []
    in_rooms = sa.or_(*(
        sa.and_(MESSAGES.c.platform == platform, MESSAGES.c.conversation_id == cid)
        for platform, _, cid in (r.partition(":") for r in sorted(rooms))
    ))
    ids = (await conn.execute(
        sa.select(MESSAGES.c.id).where(
            in_rooms, MESSAGES.c.person_id.in_(list(e.persons)), MESSAGES.c.is_nora == 0,
            MESSAGES.c.hidden_at.is_(None),
            MESSAGES.c.ts >= e.start_ms, MESSAGES.c.ts <= e.end_ms,
        ).order_by(MESSAGES.c.id)
    )).scalars()
    return [("message", str(i), "during", e.kind, e.key, "messages.ts") for i in ids]


async def _drop_episodes(conn: AsyncConnection, kind: str, keys: list[str]) -> None:
    """An episode's node and the edges it owns. The `cites` edges into it
    stay: they are the citing entry's, and reach the node again if a later
    build holds the same happening under the same key."""
    if not keys:
        return
    ends = await _delete_edges(conn, sa.or_(
        sa.and_(MEMORY_EDGE.c.src_kind == kind, _among(MEMORY_EDGE.c.src_id, keys)),
        sa.and_(
            MEMORY_EDGE.c.dst_kind == kind, _among(MEMORY_EDGE.c.dst_id, keys),
            MEMORY_EDGE.c.rel != "cites",
        ),
    ))
    await conn.execute(sa.delete(MEMORY_NODE).where(
        MEMORY_NODE.c.kind == kind, _among(MEMORY_NODE.c.source_id, keys),
    ))
    await _drop_orphans(conn, ends)


_PRESENCE: dict[str, sa.Table] = {"stay": PRESENCE_STAYS, "trip": PRESENCE_TRIPS}

#: A kind whose media go with it, and their kind: a message's attachments, a
#: social item's media.
_CARRIES: dict[str, str] = {"message": "media", "social": "social_media"}


# --- the queue ---------------------------------------------------------------


async def drain_queue(conn: AsyncConnection, *, limit: int = 500, metrics: Any = None) -> int:
    """Re-project up to `limit` queued sources, the least-tried first, then
    the oldest. A projection that lands clears its row and one that fails
    again counts an attempt (`_guarded`), so a drain is idempotent and a
    source that keeps failing stays queued, visible in the queue's length,
    and sinks behind every newer row instead of starving it. Nothing is
    abandoned: a row given up on would be a silent miss.

    The current build's row is locked FIRST, before any projection writes a
    node: `switch_build` takes that row and then writes nodes, so a drain
    that wrote a hub node and then waited on the build row could deadlock
    with it. With no build yet, there is nothing to lock."""
    with contextlib.suppress(_NoCurrentBuildError):
        await _current_build(conn)
    # SKIP LOCKED: two processes drain (the analytics tick and the nora
    # process's `memory_drain_loop`, S4), and each takes only rows the other
    # is not already re-projecting, so they never wait on or repeat each other.
    rows = (await conn.execute(
        sa.select(MEMORY_PROJECTION_QUEUE.c.kind, MEMORY_PROJECTION_QUEUE.c.source_id)
        .order_by(
            MEMORY_PROJECTION_QUEUE.c.attempts, MEMORY_PROJECTION_QUEUE.c.first_failed_at,
            MEMORY_PROJECTION_QUEUE.c.kind, MEMORY_PROJECTION_QUEUE.c.source_id,
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )).all()
    for r in rows:
        if r.kind == "media":
            # A media row is keyed by its sha256 (`_queue`).
            await reproject_media(conn, r.source_id, metrics=metrics)
        else:
            await project(conn, r.kind, r.source_id, metrics=metrics)
    return len(rows)


_PROJECTORS: dict[str, Callable[[AsyncConnection, str], Awaitable[None]]] = {
    "message": _project_message,
    "note": _project_note,
    "memo": _project_memo,
    "lesson": _project_lesson,
    "event": _project_event,
    "character": _project_character,
    "social": _project_social,
    **{kind: _episode_projector(kind) for kind in _EPISODES},
}


# --- the writes --------------------------------------------------------------


def _node_values(
    *, kind: str, source_id: str, text: str, at_ms: int | None,
    origin_room: str | None = None, unit: str | None = None,
    persons: Iterable[str] = (), exclude_persons: Iterable[str] | None = (),
    sha256: str | None = None, state: str = "live", lineage: Any = None,
) -> dict[str, Any]:
    return {
        "kind": kind, "source_id": source_id, "unit": unit, "origin_room": origin_room,
        "persons": list(persons), "exclude_persons": list(exclude_persons or ()),
        "lineage": lineage if lineage is not None else [], "text": text,
        "at_ms": at_ms, "sha256": sha256, "state": state,
    }


async def _upsert_node(conn: AsyncConnection, **values: Any) -> None:
    await _upsert_nodes(conn, [_node_values(**values)])


#: The worker's bookkeeping, cleared with the vector it describes.
_EMBED_COLUMNS = (
    "embedding", "embedding_model", "embedded_at", "embed_attempts", "embed_error", "embed_input",
)


def _input_changed(text: Any, sha256: Any) -> Any:
    """Whether the input the node's vector (or its last failed attempt) was
    made from differs from `text` / `sha256`, its new values: the bytes for
    a vector made from them (`embed_input = 'bytes'`), the text for any
    other. So an image embedded from its reading, because its bytes were not
    held, is cleared by a new reading, and one embedded from its bytes is
    not. Never NULL."""
    node = MEMORY_NODE.c
    return sa.case(
        (node.embed_input == "bytes", node.sha256.is_distinct_from(sha256)),
        else_=node.text.is_distinct_from(text),
    )


def _cleared_when(changed: sa.ColumnElement[bool]) -> dict[str, Any]:
    """SET values that clear the vector and the worker's bookkeeping where
    `changed` holds and keep them where it does not: a node whose
    embedded input changed is unembedded again, and the worker finds it
    through `idx_memory_node_unembedded`."""
    node = MEMORY_NODE.c
    return {
        name: sa.case((changed, 0 if name == "embed_attempts" else sa.null()), else_=node[name])
        for name in _EMBED_COLUMNS
    }


#: Rows per statement: a switch projects every stay of every person, and one
#: statement's parameters must stay under asyncpg's 32,767.
_CHUNK = 1000


async def _upsert_nodes(conn: AsyncConnection, rows: list[dict[str, Any]]) -> None:
    """Insert or update `rows`, clearing a vector whose input changed."""
    stmt = pg_insert(MEMORY_NODE)
    changed = _input_changed(stmt.excluded.text, stmt.excluded.sha256)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_memory_node_kind_source",
        set_={
            # Read before the new values land: a CASE in DO UPDATE sees the
            # existing row through the table's columns.
            **_cleared_when(changed),
            "unit": stmt.excluded.unit,
            "origin_room": stmt.excluded.origin_room,
            "persons": stmt.excluded.persons,
            "exclude_persons": stmt.excluded.exclude_persons,
            "lineage": stmt.excluded.lineage,
            "text": stmt.excluded.text,
            "at_ms": stmt.excluded.at_ms,
            "sha256": stmt.excluded.sha256,
            "state": stmt.excluded.state,
            "projected_at": sa.func.clock_timestamp(),
        },
    )
    for i in range(0, len(rows), _CHUNK):
        await conn.execute(stmt, rows[i:i + _CHUNK])


async def _insert_edges(conn: AsyncConnection, edges: list[Edge]) -> None:
    unique = list(dict.fromkeys(edges))
    for i in range(0, len(unique), _CHUNK):
        await conn.execute(pg_insert(MEMORY_EDGE).values([
            {"src_kind": sk, "src_id": si, "rel": rel, "dst_kind": dk, "dst_id": di,
             "provenance": prov}
            for sk, si, rel, dk, di, prov in unique[i:i + _CHUNK]
        ]).on_conflict_do_nothing())


async def _hub(
    conn: AsyncConnection, kind: str, source_id: str, *, origin_room: str | None = None,
) -> None:
    """An entity node, written the first time an edge needs it: a bare
    endpoint with no text, so it names nobody (a person's name is identity's,
    which a pure projector cannot read)."""
    await _held_hub(conn, kind, source_id, (
        pg_insert(MEMORY_NODE)
        .values(kind=kind, source_id=source_id, origin_room=origin_room, state="live")
        .on_conflict_do_nothing(constraint="uq_memory_node_kind_source")
    ))


async def _held_hub(conn: AsyncConnection, kind: str, source_id: str, write: Any) -> None:
    """Write a hub, then hold it FOR KEY SHARE until the transaction ends, so
    a concurrent `_drop_orphan_hubs` skips it while the caller attaches its
    edge. A hub that orphan drop deleted between the write and the lock is
    written again."""
    for _ in range(3):
        await conn.execute(write)
        held = (await conn.execute(
            sa.select(MEMORY_NODE.c.id).where(
                MEMORY_NODE.c.kind == kind, MEMORY_NODE.c.source_id == source_id,
            ).with_for_update(read=True, key_share=True)
        )).first()
        if held is not None:
            return
    raise RuntimeError(f"memory index: {kind} hub kept vanishing")


async def _named_hub(
    conn: AsyncConnection, kind: str, source_id: str, *, text: str | None,
) -> None:
    """A hub whose name its source states (a venue's place node, by the name
    the place lookup resolved, never the group's own words for it: the hub
    has no scope). The latest name wins; no name keeps the one it has."""
    stmt = pg_insert(MEMORY_NODE).values(
        kind=kind, source_id=source_id, text=text or "", state="live",
    )
    if not text:
        write = stmt.on_conflict_do_nothing(constraint="uq_memory_node_kind_source")
    else:
        write = stmt.on_conflict_do_update(
            constraint="uq_memory_node_kind_source",
            set_={"text": stmt.excluded.text, "projected_at": sa.func.clock_timestamp()},
        )
    await _held_hub(conn, kind, source_id, write)


async def _drop(conn: AsyncConnection, kind: str, ids: list[str]) -> None:
    if kind in _EPISODES:
        await _drop_episodes(conn, kind, ids)
        return
    if kind in _CARRIES:
        attachments = list((await conn.execute(
            sa.select(MEMORY_EDGE.c.src_id).where(
                MEMORY_EDGE.c.rel == "attached_to",
                MEMORY_EDGE.c.dst_kind == kind, _among(MEMORY_EDGE.c.dst_id, ids),
            )
        )).scalars())
        await _delete(conn, _CARRIES[kind], attachments)
    await _delete(conn, kind, ids)


def _among(column: sa.ColumnElement[Any], ids: list[str]) -> sa.ColumnElement[bool]:
    """`column = ANY(:ids)` with ONE array parameter: a retention prune drops
    every message past the window in one call, and `IN` would bind one
    parameter per id, past asyncpg's 32,767 on a large first prune."""
    return column == sa.any_(sa.bindparam(None, ids, type_=pg.ARRAY(sa.Text)))


async def _delete(conn: AsyncConnection, kind: str, ids: list[str]) -> None:
    """Nodes and the edges their own projection writes: every edge out of
    them except `supersedes`, and the `supersedes` edges into them (that edge
    is its target's, `_entry_edges`). Every other edge INTO a deleted node is
    its source's (a memo's `cites` of a hidden message, a note's `tracks` of
    a gone event), outlives it and drops itself at a read's join to
    `memory_node` (0105). A rebuild writes exactly these dangling edges, so a
    live delete and a rebuild agree (S4); removing them here was the one
    place the two disagreed."""
    if not ids:
        return
    ends = await _delete_edges(conn, sa.or_(
        sa.and_(
            MEMORY_EDGE.c.src_kind == kind, _among(MEMORY_EDGE.c.src_id, ids),
            MEMORY_EDGE.c.rel != "supersedes",
        ),
        sa.and_(
            MEMORY_EDGE.c.dst_kind == kind, _among(MEMORY_EDGE.c.dst_id, ids),
            MEMORY_EDGE.c.rel == "supersedes",
        ),
    ))
    await conn.execute(sa.delete(MEMORY_NODE).where(
        MEMORY_NODE.c.kind == kind, _among(MEMORY_NODE.c.source_id, ids),
    ))
    await _drop_orphans(conn, ends)


async def _delete_edges(conn: AsyncConnection, where: Any) -> list[tuple[str, str]]:
    """Delete the edges `where` selects; return the hub ends they reached,
    for `_drop_orphans` once the caller has written its new edges."""
    rows = (await conn.execute(
        sa.delete(MEMORY_EDGE).where(where)
        .returning(MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id)
    )).all()
    return [(k, i) for k, i in rows if k in HUB_KINDS]


async def _drop_orphans(conn: AsyncConnection, ends: Iterable[tuple[str, str]]) -> None:
    """A hub the removed edges were the last to reach goes too: a rebuild
    writes a hub only when an edge needs it, so a live index that kept one
    would differ from an index rebuilt from empty."""
    by_kind: dict[str, set[str]] = {}
    for kind, sid in ends:
        by_kind.setdefault(kind, set()).add(sid)
    for kind in sorted(by_kind):
        await _drop_orphan_hubs(conn, kind, sorted(by_kind[kind]))


def _touched() -> Any:
    """Some edge starts or ends at the `memory_node` row in hand."""
    node = MEMORY_NODE
    return sa.exists().where(sa.or_(
        sa.and_(MEMORY_EDGE.c.src_kind == node.c.kind, MEMORY_EDGE.c.src_id == node.c.source_id),
        sa.and_(MEMORY_EDGE.c.dst_kind == node.c.kind, MEMORY_EDGE.c.dst_id == node.c.source_id),
    ))


async def _drop_orphan_hubs(
    conn: AsyncConnection, kind: str, ids: list[str] | None = None,
) -> int:
    """Hub nodes of `kind` (among `ids`, or all) with no edge in either
    direction. Returns how many were removed.

    Two statements, on purpose. The first locks the candidates FOR UPDATE
    SKIP LOCKED: a hub another transaction holds is being attached to
    (`_hub` holds it FOR KEY SHARE until that transaction ends, and writes
    an edge to it), so it is skipped, never waited on, and this can never
    be one side of a deadlock over hubs. The second re-checks under a fresh
    READ COMMITTED snapshot, which sees every edge an attacher committed
    before the lock was granted; an attacher arriving after it waits on the
    row, finds it gone, and writes it again."""
    where: list[Any] = [MEMORY_NODE.c.kind == kind, ~_touched()]
    if ids is not None:
        where.append(_among(MEMORY_NODE.c.source_id, ids))
    locked = list((await conn.execute(
        sa.select(MEMORY_NODE.c.source_id).where(*where)
        .with_for_update(skip_locked=True)
    )).scalars())
    if not locked:
        return 0
    result = await conn.execute(sa.delete(MEMORY_NODE).where(
        MEMORY_NODE.c.kind == kind, _among(MEMORY_NODE.c.source_id, locked), ~_touched(),
    ))
    return int(result.rowcount or 0)


# --- rebuild (S4) ------------------------------------------------------------

#: The session-level advisory lock a `rebuild` holds from start to finish, so
#: two never run at once. Not a row lock on `memory_index_state`: a rebuild is
#: many transactions, one per batch, and a row lock ends with the first of
#: them. Spells "NORAMIDX" in ASCII (`MIGRATION_LOCK_KEY`'s convention), so it
#: is recognisable in `pg_locks`; the top byte keeps it a positive `bigint`.
REBUILD_LOCK_KEY = 0x4E4F52414D494458

#: Sources per transaction. A message is a few statements, so a batch is a
#: few thousand statements and a few seconds: short enough that a live write
#: waiting on a node the batch holds is not held up for long.
REBUILD_BATCH = 500


class RebuildRefusedError(RuntimeError):
    """Another rebuild holds `REBUILD_LOCK_KEY`."""


class IndexStatus(NamedTuple):
    """What a reader needs to tell `ok` from `partial`."""

    #: `never` (no rebuild has run on this database, or a restore emptied
    #: it), `building` (one is running, or died part-way), `complete`.
    status: str
    #: What the last completed rebuild left on the queue.
    unprojected: int
    #: What is on the queue now.
    queue: int


async def index_status(conn: AsyncConnection) -> IndexStatus:
    """The index's state row and its queue's length. A database with no
    state row reads `never`: a snapshot restore copies none of the four
    tables (`NOT_RESTORED`), and a sandbox built without the migration's
    seed has no row at all, so an empty index is never read as complete."""
    row = (await conn.execute(
        sa.select(MEMORY_INDEX_STATE.c.status, MEMORY_INDEX_STATE.c.unprojected)
    )).first()
    queue = int((await conn.execute(
        sa.select(sa.func.count()).select_from(MEMORY_PROJECTION_QUEUE)
    )).scalar_one())
    if row is None:
        return IndexStatus("never", 0, queue)
    return IndexStatus(str(row.status), int(row.unprojected), queue)


@dataclass(frozen=True, slots=True)
class RebuildReport:
    """Counts only: kinds and numbers, never an id or a text."""

    #: Sources handed to the projector, per kind (a source the projector
    #: declines, such as a room-keyed person account, is counted here and has
    #: no node). An episode kind counts the current build's episodes.
    projected: dict[str, int]
    #: Nodes the final pass removed because their source is hidden or gone,
    #: or, for an episode, not in the current build; for a hub kind, hubs no
    #: edge reaches.
    dropped: dict[str, int]
    #: The queue's length at the end: what could not be projected.
    unprojected: int
    dry_run: bool


#: What a rebuild walks, in key order so a batch boundary is a plain `>`:
#: each kind's root table and the column its node is keyed by. A hidden
#: message is not walked; the final pass removes any node it still has.
def _sources() -> list[tuple[str, Any, list[Any]]]:
    return [
        ("message", MESSAGES.c.id, [MESSAGES.c.hidden_at.is_(None)]),
        ("note", NOTE.c.id, []),
        ("memo", MEMO.c.id, []),
        ("lesson", LESSON.c.id, []),
        ("event", EVENTS.c.id, []),
        ("character", CULTURE.c.key, []),
        ("social", _social_key(), []),
    ]


def _social_key() -> Any:
    """A social item's node id in SQL (`social_source_id`)."""
    return SOCIAL_ITEM.c.network + ":" + SOCIAL_ITEM.c.item_id


def _source_of(kind: str) -> Any:
    """An existence test, against `memory_node`'s current row, for the
    source a node of `kind` was projected from. The casts are safe because
    only this module writes `source_id`, from these columns."""
    node = MEMORY_NODE.c.source_id
    if kind == "message":
        return sa.exists().where(
            MESSAGES.c.id == sa.cast(node, sa.BigInteger), MESSAGES.c.hidden_at.is_(None),
        )
    if kind == "character":
        return sa.exists().where(CULTURE.c.key == node)
    if kind == "social":
        st = SOCIAL_ITEM_STATE
        return sa.exists().where(
            _social_key() == node,
            ~sa.exists().where(
                st.c.network == SOCIAL_ITEM.c.network, st.c.item_id == SOCIAL_ITEM.c.item_id,
            ),
        )
    table = {"note": NOTE, "memo": MEMO, "lesson": LESSON, "event": EVENTS}[kind]
    return sa.exists().where(table.c.id == sa.cast(node, pg.UUID(as_uuid=False)))


async def _stale(conn: AsyncConnection, kind: str) -> list[str]:
    return list((await conn.execute(
        sa.select(MEMORY_NODE.c.source_id)
        .where(MEMORY_NODE.c.kind == kind, ~_source_of(kind))
        .order_by(MEMORY_NODE.c.source_id)
    )).scalars())


async def _episode_keys(
    conn: AsyncConnection, kind: str, *, lock: bool = True,
) -> tuple[set[str], set[str]]:
    """(the current build's keys of `kind`, the keys the index holds). No
    current build holds none. `lock` holds the build row FOR SHARE, so a
    switch cannot move the pointer between this read and the drop that
    follows it; a dry run reads without it (its connection may be
    read-only)."""
    build: int | None
    if lock:
        try:
            build = await _current_build(conn)
        except _NoCurrentBuildError:
            build = None
    else:
        build = (await conn.execute(
            sa.select(ROLLUP_BUILD.c.id).where(ROLLUP_BUILD.c.status == "current")
        )).scalar_one_or_none()
    current = set() if build is None else {e.key for e in await _episodes(conn, kind, build, None)}
    held = set((await conn.execute(
        sa.select(MEMORY_NODE.c.source_id).where(MEMORY_NODE.c.kind == kind)
    )).scalars())
    return current, held


async def _queue_length(conn: AsyncConnection) -> int:
    return int((await conn.execute(
        sa.select(sa.func.count()).select_from(MEMORY_PROJECTION_QUEUE)
    )).scalar_one())


async def _set_state(conn: AsyncConnection, **values: Any) -> None:
    """The one state row, written whether or not it exists (a restored
    database has none)."""
    stmt = pg_insert(MEMORY_INDEX_STATE).values(id=True, **values)
    await conn.execute(stmt.on_conflict_do_update(
        index_elements=[MEMORY_INDEX_STATE.c.id], set_=values,
    ))


async def rebuild(
    engine: AsyncEngine, *, dry_run: bool = False, batch: int = REBUILD_BATCH,
    metrics: Any = None,
) -> RebuildReport:
    """`nora memory rebuild`, and the first deployment's backfill: every source through the SAME
    projector a live write calls, so a rebuild equals live projection by construction.

    1. `building`, under `REBUILD_LOCK_KEY` (refused if another holds it).
    2. Every row of every kind, `batch` per transaction, in key order.
    3. The current build's episodes through `reproject_episodes`, the
       function a build switch calls, which also drops every episode node
       the current build does not hold.
    4. A final pass re-projecting every node whose source is now hidden or
       gone. A live hide that commits while a batch holds that message would
       otherwise be overwritten by the batch's upsert; the projector drops
       the node when it re-reads the hidden row.
    5. One drain of the queue, then `complete` with what stayed queued.

    Idempotent: a second run rewrites the same nodes and edges. A run that
    dies part-way leaves `building` (a reader answers `partial`, which is
    true), and running it again finishes the job. Draining concurrently
    (the analytics tick, the nora process) is harmless: a drain calls the
    same projector, which converges on the source's current row, and
    whichever lands last writes what the other would have.

    `dry_run` counts what it would project and drop and writes nothing."""
    if dry_run:
        return await _count(engine)
    lock = await engine.connect()
    try:
        held = (await lock.execute(
            sa.select(sa.func.pg_try_advisory_lock(REBUILD_LOCK_KEY))
        )).scalar_one()
        await lock.commit()
        if not held:
            raise RebuildRefusedError(
                "another `nora memory rebuild` holds the advisory lock "
                f"{REBUILD_LOCK_KEY:#x} (pg_locks: locktype = 'advisory'); "
                "wait for it to finish"
            )
        try:
            return await _rebuild(engine, batch=batch, metrics=metrics)
        finally:
            await lock.execute(sa.select(sa.func.pg_advisory_unlock(REBUILD_LOCK_KEY)))
            await lock.commit()
    except BaseException:
        # A pooled connection keeps a session lock: never hand one back that
        # might still hold it.
        await lock.invalidate()
        raise
    finally:
        await lock.close()


async def _rebuild(engine: AsyncEngine, *, batch: int, metrics: Any) -> RebuildReport:
    now = sa.func.clock_timestamp()
    async with engine.begin() as conn:
        await _set_state(conn, status="building", started_at=now, finished_at=None)
    projected: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for kind, key, where in _sources():
        projected[kind] = 0
        last: Any = None
        while True:
            async with engine.begin() as conn:
                ids = list((await conn.execute(
                    sa.select(key)
                    .where(*where, *([] if last is None else [key > last]))
                    .order_by(key).limit(batch)
                )).scalars())
                for sid in ids:
                    await project(conn, kind, sid, metrics=metrics)
            if not ids:
                break
            projected[kind] += len(ids)
            last = ids[-1]
    async with engine.begin() as conn:
        if await _has_current_build(conn):
            await reproject_episodes(conn, metrics=metrics)
    for kind in _EPISODES:
        async with engine.begin() as conn:
            current, held = await _episode_keys(conn, kind)
            stale = sorted(held - current)
            if stale:
                # Not the current build's (or no build is current): no node.
                await drop(conn, kind, stale, metrics=metrics)
        projected[kind] = len(current)
        dropped[kind] = len(stale)
    for kind, _, _ in _sources():
        async with engine.begin() as conn:
            stale = await _stale(conn, kind)
        dropped[kind] = len(stale)
        for i in range(0, len(stale), batch):
            async with engine.begin() as conn:
                for sid in stale[i:i + batch]:
                    await project(conn, kind, sid, metrics=metrics)
    async with engine.begin() as conn:
        queued = await _queue_length(conn)
    tried = 0
    while tried < queued:
        async with engine.begin() as conn:
            n = await drain_queue(conn, limit=min(batch, queued - tried), metrics=metrics)
        if n == 0:
            break
        tried += n
    # Last: a hub no edge reaches is not written by a rebuild from empty, so
    # one left behind (a re-attribution, a pre-S4 delete) goes.
    for kind in HUB_KINDS:
        async with engine.begin() as conn:
            dropped[kind] = await _drop_orphan_hubs(conn, kind)
    async with engine.begin() as conn:
        unprojected = await _queue_length(conn)
        await _set_state(conn, status="complete", finished_at=now, unprojected=unprojected)
    return RebuildReport(projected, dropped, unprojected, dry_run=False)


async def _has_current_build(conn: AsyncConnection) -> bool:
    return bool((await conn.execute(
        sa.select(sa.exists().where(ROLLUP_BUILD.c.status == "current"))
    )).scalar())


async def _count(engine: AsyncEngine) -> RebuildReport:
    """What `rebuild` would project and drop, read in one transaction."""
    projected: dict[str, int] = {}
    dropped: dict[str, int] = {}
    async with engine.connect() as conn:
        for kind, key, where in _sources():
            projected[kind] = int((await conn.execute(
                sa.select(sa.func.count(key)).where(*where)
            )).scalar_one())
            dropped[kind] = len(await _stale(conn, kind))
        for kind in _EPISODES:
            current, held = await _episode_keys(conn, kind, lock=False)
            projected[kind] = len(current)
            dropped[kind] = len(held - current)
        for kind in HUB_KINDS:
            dropped[kind] = int((await conn.execute(
                sa.select(sa.func.count()).select_from(MEMORY_NODE).where(
                    MEMORY_NODE.c.kind == kind, ~_touched(),
                )
            )).scalar_one())
        unprojected = await _queue_length(conn)
    return RebuildReport(projected, dropped, unprojected, dry_run=True)


# --- the vectors ----------------------------------------------------------

#: A failed node waits `BASE * 2^(attempts - 1)`, at most `CAP`, before the
#: worker tries it again: a node the embedder keeps refusing costs one call
#: every few hours, never one per tick.
EMBED_BACKOFF_BASE_MS = 60_000
EMBED_BACKOFF_CAP_MS = 6 * 3_600_000
#: After this many failed attempts a node is never picked again until its
#: input changes or the operator clears vectors: an input that crashes the
#: shared server must not crash it every 6 hours forever. `embed_counts`
#: reports these as `given_up`.
EMBED_MAX_ATTEMPTS = 8


def _literal_in(column: str, values: tuple[str, ...]) -> Any:
    """`column IN (...)` with the values as SQL literals, never parameters:
    `idx_memory_node_unembedded` is a partial index, and the planner can use
    one only when the query's own predicate implies the index's, which a bind
    parameter under a generic plan never does. The values are module
    constants."""
    listed = ", ".join(f"'{v}'" for v in values)
    return sa.text(f"memory_node.{column} IN ({listed})")


def _embeddable() -> list[Any]:
    """Live nodes of an embedded kind with something to embed: a text, or
    an attachment, which may be embedded from its bytes."""
    node = MEMORY_NODE.c
    return [
        sa.text("memory_node.state = 'live'"),
        _literal_in("kind", EMBEDDED_KINDS),
        sa.or_(node.text != "", node.kind.in_(ATTACHMENT_KINDS)),
    ]


@dataclass(frozen=True, slots=True)
class Embeddable:
    """A node the worker is to embed: what it is embedded from, never more."""

    id: int
    kind: str
    source_id: str
    text: str
    sha256: str | None


async def embed_pick(
    conn: AsyncConnection, *, limit: int, now_ms: int, media: bool = True,
) -> list[Embeddable]:
    """Up to `limit` embeddable nodes with no vector, the fewest failures
    first and then the newest, so a new message is embedded on the next tick
    while a backfill works backwards. A failed node waits out its backoff; one
    that failed `EMBED_MAX_ATTEMPTS` times is not picked. `media=False` leaves
    attachments out, while the image throttle is spent. A vector of another
    model is not picked here: the worker clears those once per process
    (`clear_embeddings`), and they come back through this pick."""
    rows = await conn.execute(_pick_statement(limit=limit, now_ms=now_ms, media=media))
    return [Embeddable(r.id, r.kind, r.source_id, r.text, r.sha256) for r in rows]


def _pick_statement(*, limit: int, now_ms: int, media: bool) -> sa.Select[Any]:
    """`embed_pick`'s query, apart so a test can ask the planner about it."""
    node = MEMORY_NODE.c
    wait = sa.cast(sa.func.least(
        EMBED_BACKOFF_BASE_MS * sa.func.power(2, sa.func.least(node.embed_attempts - 1, 30)),
        EMBED_BACKOFF_CAP_MS,
    ), sa.BigInteger)
    where = [
        node.embedding.is_(None), *_embeddable(),
        node.embed_attempts < EMBED_MAX_ATTEMPTS,
        sa.or_(node.embed_attempts == 0, node.embedded_at <= now_ms - wait),
    ]
    if not media:
        where.append(node.kind.not_in(ATTACHMENT_KINDS))
    return (
        sa.select(node.id, node.kind, node.source_id, node.text, node.sha256)
        .where(*where)
        .order_by(node.embed_attempts, node.id.desc())
        .limit(limit)
    )


def _same_input(item: Embeddable, by_bytes: bool) -> Any:
    """The node still holds the input `item` was embedded from: a write
    landing after an edit would pin the old text's vector on the new text."""
    node = MEMORY_NODE.c
    same = node.sha256 == item.sha256 if by_bytes else node.text == item.text
    return sa.and_(node.id == item.id, same)


async def embed_store(
    conn: AsyncConnection, item: Embeddable, *, vector: list[float], model: str,
    now_ms: int, by_bytes: bool,
) -> bool:
    """Store `item`'s vector, the fingerprint it was made with and what it
    was made from. False if the node changed (or went) while it was being
    embedded: the worker finds it again, unembedded."""
    result = await conn.execute(
        sa.update(MEMORY_NODE).where(_same_input(item, by_bytes)).values(
            embedding=vector, embedding_model=model, embedded_at=now_ms,
            embed_attempts=0, embed_error=None, embed_input="bytes" if by_bytes else "text",
        )
    )
    return bool(result.rowcount)


async def embed_failed(
    conn: AsyncConnection, item: Embeddable, *, error: str, now_ms: int, by_bytes: bool,
    counts: bool = True,
) -> int:
    """Record a failed attempt, back the node off, and return its attempts
    now (0 if the node changed meanwhile). `error` is a short class
    (`nora.infra.sql.memory_embed`), never content. `embed_input` records
    what was tried, so a new reading frees a node whose reading failed and
    not one whose bytes did.

    `counts=False` for a failure that says nothing about the input (the
    embedder or the blob store unavailable): the attempt still backs the
    node off, but never to `EMBED_MAX_ATTEMPTS`, so an outage gives up on
    nothing. Only a failure of the input itself (rejected,
    empty, error) can reach the cap."""
    bumped = MEMORY_NODE.c.embed_attempts + 1
    attempts = bumped if counts else sa.func.greatest(
        MEMORY_NODE.c.embed_attempts, sa.func.least(bumped, EMBED_MAX_ATTEMPTS - 1),
    )
    result = (await conn.execute(
        sa.update(MEMORY_NODE).where(_same_input(item, by_bytes)).values(
            embedding=None, embedding_model=None, embedded_at=now_ms,
            embed_attempts=attempts, embed_error=error[:80],
            embed_input="bytes" if by_bytes else "text",
        ).returning(MEMORY_NODE.c.embed_attempts)
    )).scalar_one_or_none()
    return int(result or 0)


def _clearable(keep_model: str | None) -> Any:
    node = MEMORY_NODE.c
    if keep_model is None:
        return sa.or_(
            node.embedding.isnot(None), node.embed_attempts > 0, node.embedding_model.isnot(None),
        )
    return sa.and_(node.embedding.isnot(None), node.embedding_model.is_distinct_from(keep_model))


async def clear_embeddings(
    conn: AsyncConnection, *, keep_model: str | None = None, dry_run: bool = False,
) -> int:
    """Clear every vector and the worker's bookkeeping (given-up nodes
    included), or, with `keep_model`, only the vectors of any other model.
    The worker backfills what this clears. Returns the nodes cleared, or
    with `dry_run` counts them and writes nothing."""
    if dry_run:
        return int((await conn.execute(
            sa.select(sa.func.count()).select_from(MEMORY_NODE).where(_clearable(keep_model))
        )).scalar_one())
    result = await conn.execute(
        sa.update(MEMORY_NODE).where(_clearable(keep_model)).values(
            embedding=None, embedding_model=None, embedded_at=None,
            embed_attempts=0, embed_error=None, embed_input=None,
        )
    )
    return int(result.rowcount or 0)


class EmbedCounts(NamedTuple):
    """The live, embeddable nodes with something to embed: holding a vector
    of `model`; waiting for one; of those waiting, how many have failed at
    least once; how many the worker has given up on (`EMBED_MAX_ATTEMPTS`),
    which only a changed input or a clear frees; and how many, not given up,
    last failed as `empty` (an attachment with no reading, whose bytes are
    not an image's or not held): those wait for their input, not the
    worker. `fresh`, of those waiting and neither given up nor `empty`, how
    many were projected within `embed_counts`' `fresh_seconds` (0 unless
    asked)."""

    embedded: int
    pending: int
    failing: int
    given_up: int
    empty: int = 0
    fresh: int = 0


async def embed_counts(
    conn: AsyncConnection, model: str, *where: sa.ColumnElement[bool],
    fresh_seconds: float = 0.0,
) -> EmbedCounts:
    """`EmbedCounts` over every node, or over those `where` also holds (the
    search passes its scope, `nora.infra.sql.memory_read`). `fresh_seconds`
    is measured on the database's clock, the one `projected_at` is written
    with."""
    node = MEMORY_NODE.c
    # IS NOT DISTINCT FROM, so a NULL model is «not current», never NULL.
    current = sa.and_(
        node.embedding.isnot(None), node.embedding_model.is_not_distinct_from(model),
    )
    given_up = node.embed_attempts >= EMBED_MAX_ATTEMPTS
    # A NULL `embed_error` is not `empty`: IS DISTINCT FROM, never `!=`.
    owed = sa.and_(~current, ~given_up, node.embed_error.is_distinct_from("empty"))
    since = sa.func.clock_timestamp() - sa.bindparam(
        None, timedelta(seconds=max(0.0, fresh_seconds)), type_=sa.Interval,
    )
    row = (await conn.execute(
        sa.select(
            sa.func.count().filter(current),
            sa.func.count().filter(~current),
            sa.func.count().filter(sa.and_(~current, node.embed_attempts > 0)),
            sa.func.count().filter(sa.and_(~current, given_up)),
            sa.func.count().filter(sa.and_(~current, ~given_up, node.embed_error == "empty")),
            sa.func.count().filter(sa.and_(owed, node.projected_at > since)),
        ).where(*_embeddable(), *where)
    )).one()
    return EmbedCounts(*(int(v) for v in row))


# --- an eval sandbox's vectors ----------------------------------------------


class _Source(NamedTuple):
    id: int
    kind: str
    text: str
    sha256: str | None
    embed_input: str


def _input_key(kind: str, text: str, sha256: str | None) -> str:
    """What a worker result is made from, as far as one key says: an
    attachment's file, any other node's text. Equal keys are the identity
    `adopt_worker_state` copies on."""
    return (sha256 or "") if kind in ATTACHMENT_KINDS else text


def _adoptable(node: Any, source: _Source) -> bool:
    """Of a source row with `node`'s key: whether its result was made from
    `node`'s input. An attachment's vector made from its bytes, or from a
    reading equal to `node`'s (the projector's keep rule, `_input_changed`);
    any other node's from its text, never an attachment's."""
    if node.kind in ATTACHMENT_KINDS:
        return source.kind in ATTACHMENT_KINDS and (
            source.embed_input == "bytes" or node.text == source.text
        )
    return source.kind not in ATTACHMENT_KINDS and source.embed_input == "text"


async def adopt_worker_state(
    source: AsyncConnection, target: AsyncConnection, *, model: str, batch: int = 500,
) -> None:
    """Give `target`'s unembedded nodes the embedding worker's result from
    `source` wherever the input it was made from is IDENTICAL: a vector of
    `model`, or a failure the worker will not retry (given up, or `empty`).

    For an eval sandbox (`nora.subject.sandbox_index`): `target` is a sandbox
    whose index `rebuild` has just projected from rows as of an instant,
    `source` the deployment's clone, read only. The worker never runs there.

    **Why this is as-of safe.** A vector is a pure function of the model and
    the input it was embedded from (`nora.infra.sql.memory_embed`: the text
    as a document, or an image's bytes), whatever node, kind or instant it
    belongs to. Copying one onto a node whose input is identical is therefore
    the vector re-embedding that node would produce, never a later state of
    it. The identity is the projector's own keep rule (`_input_changed`): the
    bytes the `sha256` names for a vector made from them, the text for any
    other. A node whose text changed after the instant (an edit, a later
    reading on an image embedded from its reading) differs from the source's
    and gets nothing: it stays unembedded and is counted so. An attachment
    adopts only from the source's node for the same bytes, so a reading's
    vector, or an `empty` failure, never moves to another file that shares
    its text. Matched on the input alone, never on `(kind, source_id)`: the
    snapshot path renumbers every row it writes, and an id says nothing about
    what was embedded."""
    node = MEMORY_NODE.c
    held = sa.and_(node.embedding.isnot(None), node.embedding_model == model)
    terminal = sa.and_(
        node.embedding.is_(None),
        sa.or_(node.embed_attempts >= EMBED_MAX_ATTEMPTS, node.embed_error == "empty"),
    )
    by_input: dict[str, list[_Source]] = {}
    for r in await source.execute(
        sa.select(node.id, node.kind, node.text, node.sha256, node.embed_input)
        .where(sa.or_(held, terminal), node.embed_input.isnot(None))
        # A vector before a failure, then the oldest: one choice per input.
        .order_by(sa.case((held, 0), else_=1), node.id)
    ):
        row = _Source(*r)
        by_input.setdefault(_input_key(row.kind, row.text, row.sha256), []).append(row)
    adopted: dict[int, list[int]] = {}
    for t in await target.execute(
        sa.select(node.id, node.kind, node.text, node.sha256).where(
            node.embedding.is_(None), node.embed_attempts == 0, *_embeddable(),
        )
    ):
        candidates = by_input.get(_input_key(t.kind, t.text, t.sha256), ())
        match = next((r for r in candidates if _adoptable(t, r)), None)
        if match is not None:
            adopted.setdefault(match.id, []).append(t.id)
    ids = list(adopted)
    for i in range(0, len(ids), batch):
        for r in await source.execute(
            sa.select(
                node.id, node.embed_input, sa.cast(node.embedding, sa.Text).label("vector"),
                node.embedding_model, node.embedded_at, node.embed_attempts, node.embed_error,
            ).where(node.id.in_(ids[i:i + batch]))
        ):
            await target.execute(
                sa.update(MEMORY_NODE).where(node.id.in_(adopted[r.id])).values(
                    embedding=(
                        None if r.vector is None
                        else sa.cast(sa.literal(r.vector, sa.Text), HALFVEC(EMBEDDING_DIMENSIONS))
                    ),
                    embedding_model=r.embedding_model, embedded_at=r.embedded_at,
                    embed_attempts=r.embed_attempts, embed_error=r.embed_error,
                    embed_input=r.embed_input,
                )
            )
