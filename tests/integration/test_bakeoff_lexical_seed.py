"""The bake-off's trigram arm is the live seed over nodes the live projector
wrote, and it never writes into a database that is not a throwaway
(`nora.subject.lexical_seed`)."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from evals.retrieval_bakeoff.run import Media
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_index import _reading
from nora.infra.sql.schema import MESSAGES
from nora.subject import lexical_seed

SHA = {n: f"{n:064x}" for n in range(1, 4)}


def test_the_seed_ranks_the_pool_by_the_live_terms(pg_url: str) -> None:
    lexical_seed.load(pg_url, {
        "img-1": (SHA[1], "Egg tarts", "On a metal tray", ""),
        "img-2": (SHA[2], "Tram", "A tram on a wet street at night", ""),
        "img-3": (SHA[3], "點心", "蒸籠入面有四粒小籠包", ""),
    })
    got = lexical_seed.seed(pg_url, {"q-en": "the egg tarts", "q-yue": "小籠包", "q-none": "the"},
                            limit=10)
    assert got["q-en"][0] == SHA[1]
    assert got["q-yue"] == [SHA[3]]
    assert got["q-none"] == []  # no term survives terms_of: nothing, not everything


def test_load_replaces_the_bakeoff_room_and_nothing_else(pg_url: str) -> None:
    lexical_seed.load(pg_url, {"img-1": (SHA[1], "first", "", "")})
    lexical_seed.load(pg_url, {"img-2": (SHA[2], "second", "", "")})
    assert lexical_seed.seed(pg_url, {"q": "first"}, limit=10)["q"] == []
    assert lexical_seed.seed(pg_url, {"q": "second"}, limit=10)["q"] == [SHA[2]]


def test_load_refuses_a_database_that_holds_a_message_outside_the_bakeoff(pg_url: str) -> None:
    async def foreign() -> None:
        engine = create_sql_engine(pg_url)
        async with engine.begin() as conn:
            await conn.execute(sa.insert(MESSAGES).values(
                platform="signal", conversation_id="a-real-room", conversation_type="group",
                sender_id="someone", ts=1, body="x"))
        await engine.dispose()

    asyncio.run(foreign())
    with pytest.raises(lexical_seed.NotAThrowawayDatabaseError):
        lexical_seed.load(pg_url, {"img-1": (SHA[1], "egg tarts", "", "")})


@pytest.mark.parametrize("fields", [("T", "D", "C"), ("T", "", "C"), ("", "", "C"), ("", "", "")])
def test_the_text_arms_read_what_the_index_reads(fields: tuple[str, str, str]) -> None:
    """BM25 and the dense floor see the same text the trigram arm's nodes hold."""
    assert Media("0" * 64, *fields).reading == _reading(*fields)
