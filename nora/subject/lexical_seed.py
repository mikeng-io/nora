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

**Never Nora's database.** `load` writes, so it refuses any database holding
a message outside the bake-off's room: Nora's always has some, a throwaway
one built for the run has none.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nora.agent.memory_search import terms_of
from nora.domain.memory_index import MemoryScope
from nora.domain.rollup import room_key
from nora.infra.sql import create_sql_engine, memory_index
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEDIA_OBJECTS, MESSAGES

__all__ = ["ROOM", "NotAThrowawayDatabaseError", "Reading", "load", "seed"]

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
    """The database holds data that is not the bake-off's."""


def load(url: str, items: Mapping[str, Reading]) -> None:
    """Migrate `url` to head and make the bake-off's room hold exactly
    `items` (pool id -> sha256, title, description, content)."""
    run_migrations(url)
    asyncio.run(_load(url, items))


_OURS = sa.and_(MESSAGES.c.platform == _PLATFORM, MESSAGES.c.conversation_id == _CONVERSATION)


async def _load(url: str, items: Mapping[str, Reading]) -> None:
    engine = create_sql_engine(url)
    try:
        async with engine.begin() as conn:
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
    finally:
        await engine.dispose()


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
