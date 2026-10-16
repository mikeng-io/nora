"""The live lexical seed, obtained for the retrieval bake-off
(`evals/retrieval_bakeoff/`).

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
    "MARKER", "ROOM", "IncompleteLoadError", "NotAThrowawayDatabaseError", "Reading", "Said",
    "load", "seed",
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
    social_off=frozenset(),
)

#: A photo: its bytes' sha256 and the reading's three fields.
Reading = tuple[str, str, str, str]
#: A message: when it was sent (epoch ms) and its text.
Said = tuple[int, str]


class NotAThrowawayDatabaseError(RuntimeError):
    """The database has not proved it is the bake-off's throwaway."""


class IncompleteLoadError(RuntimeError):
    """A pool item's node did not land; the pool would be silently smaller."""


def load(url: str, items: Mapping[str, Reading | Said]) -> None:
    """Migrate `url` to head and make the bake-off's room hold exactly
    `items`: per pool id, one message, which for a photo (`Reading`) is an
    empty message attaching it and for a message (`Said`) is its text, sent
    when it was sent."""
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


async def _load(url: str, items: Mapping[str, Reading | Said]) -> None:
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
            photos = 0
            for n, (pid, item) in enumerate(items.items(), 1):
                row: dict[str, object] = {"ts": n, "body": ""}
                if len(item) == 4:
                    sha, title, description, content = item  # type: ignore[misc]
                    reading = {"title": title, "description": description, "content": content}
                    await conn.execute(
                        pg_insert(MEDIA_OBJECTS)
                        .values(sha256=sha, media_type="image/jpeg", source="bakeoff",
                                created_at_ms=n, **reading)
                        .on_conflict_do_update(index_elements=[MEDIA_OBJECTS.c.sha256],
                                               set_=reading)
                    )
                    row["media_json"] = json.dumps([{"sha256": sha, "media_type": "image/jpeg"}])
                    photos += 1
                else:
                    row["ts"], row["body"] = item  # type: ignore[misc]
                mid = (await conn.execute(
                    sa.insert(MESSAGES).values(
                        platform=_PLATFORM, conversation_id=_CONVERSATION,
                        conversation_type="group", sender_id="bakeoff",
                        platform_message_id=pid, **row,
                    ).returning(MESSAGES.c.id)
                )).scalar_one()
                await memory_index.project(conn, "message", mid)
            await _require_complete(conn, messages=len(items), media=photos)
    finally:
        await engine.dispose()


async def _require_complete(conn: AsyncConnection, *, messages: int, media: int) -> None:
    queued = (await conn.execute(
        sa.select(sa.func.count()).select_from(MEMORY_PROJECTION_QUEUE)
    )).scalar_one()
    nodes = dict((await conn.execute(
        sa.select(MEMORY_NODE.c.kind, sa.func.count()).where(
            MEMORY_NODE.c.origin_room == ROOM, MEMORY_NODE.c.state == "live",
            MEMORY_NODE.c.kind.in_(("message", "media")),
        ).group_by(MEMORY_NODE.c.kind)
    )).tuples().all())
    got = (nodes.get("message", 0), nodes.get("media", 0))
    if queued or got != (messages, media):
        raise IncompleteLoadError(
            f"{got[0]} message and {got[1]} media nodes for {messages} messages and "
            f"{media} photos, {queued} projections queued")


def seed(url: str, queries: Mapping[str, str], *, limit: int) -> dict[str, list[str]]:
    """Query id -> the pool ids the live seed returns for it, best first: a
    message node is its message's item, an attachment its message's photo.
    An exception propagates: the caller marks the arm's queries ERROR."""
    return asyncio.run(_seed(url, queries, limit))


async def _seed(url: str, queries: Mapping[str, str], limit: int) -> dict[str, list[str]]:
    engine = create_sql_engine(url)
    try:
        async with engine.connect() as conn:
            pool = {str(i): pid for i, pid in await conn.execute(
                sa.select(MESSAGES.c.id, MESSAGES.c.platform_message_id).where(_OURS))}
        index = SqlMemoryIndex(engine)
        out: dict[str, list[str]] = {}
        for qid, text in queries.items():
            seeds = await index.seed(terms_of(text), _SCOPE, limit=limit)
            # A message node's source is the message id; an attachment's is
            # `<message id>:<sha256>` (`memory_index._attachment_id`).
            ids = [pool[s.node.source_id.partition(":")[0]] for s in seeds
                   if s.node.kind in ("message", "media")]
            out[qid] = list(dict.fromkeys(ids))
        return out
    finally:
        await engine.dispose()
