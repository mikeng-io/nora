"""The live lexical seed, obtained for the retrieval bake-off
(`evals/retrieval_bakeoff/`, design/unified-memory/20 stage 1).

The bake-off's trigram arm has to BE the seed Nora searches with, not a copy
of its SQL: `nora.agent.memory_search.terms_of` splits the query and
`SqlMemoryIndex.seed` (S5) scores it, against nodes that Nora's own projector
(`nora.infra.sql.memory_index.project`) wrote into a database Nora's own
migration chain built, trigram index included. `evals/` may not import any of
them (`pyproject.toml`'s evals contract), so this module obtains them for it,
the way the rest of `nora.subject` obtains the composed graph.

Each pool item goes in the way a shared photo does: a `media_objects` row
holding its frozen reading, and one message in the bake-off's room that
attaches those bytes. Projecting the message writes the attachment's `media`
node with the index's own text for a reading. The read is scoped to that one
room, so the arm measures the seed alone.

**Never Nora's database, and never anyone else's.** `load` migrates and
writes, so before it touches anything it makes the database prove it is a
throwaway (`_claim`), on a plain connection, in one transaction under an
advisory lock:

- its name starts with `bakeoff`;
- it carries the `bakeoff_sandbox` marker table, which only `_claim`
  creates, and only in a database that holds no table at all. A non-empty
  database without the marker (Nora's, another application's, one a
  migration already touched) is refused untouched.

The load then re-checks the marker and refuses any message outside the
bake-off's room, under the same lock.

**A load that did not land is a failure, not a smaller pool.** The
projector's savepoint (`memory_index._guarded`) queues a failed projection
and carries on, so a node can be missing while the load "succeeds", and its
item would be scored as a miss. After projecting, the projection queue must
be empty and the room must hold exactly one media node per item, or the
load raises `IncompleteLoadError` and rolls back.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.agent.memory_search import terms_of
from nora.domain.memory_index import MemoryScope
from nora.domain.rollup import room_key
from nora.infra.sql import create_sql_engine, memory_index
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEDIA_OBJECTS, MEMORY_NODE, MEMORY_PROJECTION_QUEUE, MESSAGES

__all__ = [
    "MARKER", "ROOM", "IncompleteLoadError", "NotAThrowawayDatabaseError", "Reading", "load",
    "seed",
]

#: The opt-in a throwaway database carries; only `_claim` creates it.
MARKER = "bakeoff_sandbox"
#: Serialises claim and load on one database (`pg_advisory_xact_lock`).
_LOCK_KEY = 0x0BAC_E0FF

_PLATFORM = "bakeoff"
_CONVERSATION = "retrieval-bakeoff"
#: The one room every pool item is shared in and every read is scoped to.
ROOM = room_key(_PLATFORM, _CONVERSATION)

_SCOPE = MemoryScope(
    units=(), rooms=(ROOM,), listed_here=frozenset(), people_here=frozenset(),
    exclude_strict=False, presence_persons=frozenset(),
)

#: A pool item: its bytes' sha256 and the reading's three fields.
Reading = tuple[str, str, str, str]


class NotAThrowawayDatabaseError(RuntimeError):
    """The database has not proved it is the bake-off's throwaway."""


class IncompleteLoadError(RuntimeError):
    """A pool item's node did not land; the pool would be silently smaller."""


def load(url: str, items: Mapping[str, Reading]) -> None:
    """Migrate `url` to head and make the bake-off's room hold exactly
    `items` (pool id -> sha256, title, description, content)."""
    asyncio.run(_claim(url))
    run_migrations(url)
    asyncio.run(_load(url, items))


async def _lock_and_mark(conn: AsyncConnection) -> bool:
    await conn.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    return bool((await conn.execute(
        sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{MARKER}"}
    )).scalar_one())


async def _claim(url: str) -> None:
    """Refuse, before any migration, a database that has not proved it is a
    throwaway; mark an empty one as the bake-off's."""
    name = make_url(url).database or ""
    if not name.startswith("bakeoff"):
        raise NotAThrowawayDatabaseError(
            f"refusing database {name!r}: a bake-off database's name starts with 'bakeoff'")
    engine = create_sql_engine(url)
    try:
        async with engine.begin() as conn:
            if await _lock_and_mark(conn):
                return
            tables = (await conn.execute(sa.text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            ))).scalar_one()
            if tables:
                raise NotAThrowawayDatabaseError(
                    f"refusing database {name!r}: it holds {tables} tables and no {MARKER} "
                    "marker; only an empty database becomes a bake-off database")
            await conn.execute(sa.text(
                f"CREATE TABLE {MARKER} (created_at timestamptz NOT NULL DEFAULT now())"))
    finally:
        await engine.dispose()


_OURS = sa.and_(MESSAGES.c.platform == _PLATFORM, MESSAGES.c.conversation_id == _CONVERSATION)


async def _load(url: str, items: Mapping[str, Reading]) -> None:
    engine = create_sql_engine(url)
    try:
        async with engine.begin() as conn:
            if not await _lock_and_mark(conn):
                raise NotAThrowawayDatabaseError(f"the {MARKER} marker is gone")
            foreign = (await conn.execute(
                sa.select(sa.func.count()).select_from(MESSAGES).where(~_OURS)
            )).scalar_one()
            if foreign:
                raise NotAThrowawayDatabaseError(
                    f"refusing to write: {foreign} messages outside the bake-off's room; "
                    "point --dsn at a throwaway database"
                )
            old = list((await conn.execute(
                sa.delete(MESSAGES).where(_OURS).returning(MESSAGES.c.id)
            )).scalars())
            for mid in old:
                await memory_index.project(conn, "message", mid)  # gone: dropped
            for ts, (pid, (sha, title, description, content)) in enumerate(items.items(), 1):
                reading = {"title": title, "description": description, "content": content}
                await conn.execute(
                    pg_insert(MEDIA_OBJECTS)
                    .values(sha256=sha, media_type="image/jpeg", source="bakeoff",
                            created_at_ms=ts, **reading)
                    .on_conflict_do_update(index_elements=[MEDIA_OBJECTS.c.sha256], set_=reading)
                )
                mid = (await conn.execute(
                    sa.insert(MESSAGES).values(
                        platform=_PLATFORM, conversation_id=_CONVERSATION,
                        conversation_type="group", sender_id="bakeoff", ts=ts, body="",
                        platform_message_id=pid,
                        media_json=json.dumps([{"sha256": sha, "media_type": "image/jpeg"}]),
                    ).returning(MESSAGES.c.id)
                )).scalar_one()
                await memory_index.project(conn, "message", mid)
            await _require_complete(conn, len(items))
    finally:
        await engine.dispose()


async def _require_complete(conn: AsyncConnection, expected: int) -> None:
    queued = (await conn.execute(
        sa.select(sa.func.count()).select_from(MEMORY_PROJECTION_QUEUE)
    )).scalar_one()
    nodes = (await conn.execute(
        sa.select(sa.func.count()).select_from(MEMORY_NODE).where(
            MEMORY_NODE.c.kind == "media", MEMORY_NODE.c.origin_room == ROOM,
            MEMORY_NODE.c.state == "live",
        )
    )).scalar_one()
    if queued or nodes != expected:
        raise IncompleteLoadError(
            f"{nodes} media nodes for {expected} items, {queued} projections queued")


def seed(url: str, queries: Mapping[str, str], *, limit: int) -> dict[str, list[str]]:
    """Query id -> the sha256 of each attachment the live seed returns for
    it, best first. An exception propagates: the caller marks the arm's
    queries ERROR."""
    return asyncio.run(_seed(url, queries, limit))


async def _seed(url: str, queries: Mapping[str, str], limit: int) -> dict[str, list[str]]:
    engine = create_sql_engine(url)
    try:
        index = SqlMemoryIndex(engine)
        out: dict[str, list[str]] = {}
        for qid, text in queries.items():
            seeds = await index.seed(terms_of(text), _SCOPE, limit=limit)
            out[qid] = [s.node.sha256 for s in seeds if s.node.kind == "media" and s.node.sha256]
        return out
    finally:
        await engine.dispose()
