"""`profile_dispute` writes the graph's dispute and forget verbs for a cited claim id.

Through the real `SqlMemoryIntake` and Postgres, so what a call changes is what
every graph read sees (`visible_to_reads`).
"""

# ruff: noqa: F811 - pytest resolves the imported engine fixture.

import sqlalchemy as sa

from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.models import PersonRef
from nora.infra.sql.cognitive_graph_schema import CLAIM, DISPUTE, FORGET_REQUEST
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.memory.wire import memory_intake
from nora.obs.metrics import Metrics
from tests.fakes import SinkSender
from tests.infra.test_disputes import _OTHER, _claim, _visible
from tests.infra.test_item_registration import (
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    engine,  # noqa: F401
)
from tests.test_room_tools import _UNREACHABLE_TRANSIT, _call, _NullStore


def _dispute(engine, *, speaker: str = _PERSON):
    room = Room(
        platform="signal", conversation_id=_ROOM, roster_source=None,
        tagger=PersonRef(speaker, "Generated"),
    )
    built = build_tools(
        store=_NullStore(), conversation_id=_ROOM, conversation_type="group",
        persons=None, deliverer=SinkSender(), metrics=Metrics(), room=room,
        transit_source=_UNREACHABLE_TRANSIT, question="", context_window=40,
        supplied_unit=_UNIT, graph_retrieval=object(),
        memory_intake=memory_intake(engine, _registration()),
    )
    return {t.tool_name: t for t in built}["profile_dispute"]


async def _rows(engine, table):
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(table))).mappings().all()


async def test_the_subjects_own_dispute_hides_the_claim_at_once(engine):
    await _claim(SqlCognitiveGraph(engine), "generated-claim", persons=(_PERSON,))
    result = await _call(_dispute(engine), claim_id="generated-claim")
    assert result["status"] == "ok" and result["effect"] == "hidden"
    assert [(r["author"], r["claim_id"]) for r in await _rows(engine, DISPUTE)] == [
        (_PERSON, "generated-claim"),
    ]
    assert not await _visible(engine, CLAIM)


async def test_another_persons_dispute_marks_the_claim_for_weighing(engine):
    await _claim(SqlCognitiveGraph(engine), "generated-claim", persons=(_PERSON,))
    result = await _call(_dispute(engine, speaker=_OTHER), claim_id="generated-claim")
    assert result["status"] == "ok" and result["effect"] == "disputed"
    [claim] = await _rows(engine, CLAIM)
    assert claim["state"] == "awaiting_weighing"
    assert await _visible(engine, CLAIM) == {"generated-claim"}


async def test_the_subjects_own_forget_hides_the_claim(engine):
    await _claim(SqlCognitiveGraph(engine), "generated-claim", persons=(_PERSON,))
    result = await _call(_dispute(engine), claim_id="generated-claim", kind="forget")
    assert result["status"] == "ok" and result["effect"] == "hidden"
    [request] = await _rows(engine, FORGET_REQUEST)
    assert (request["author"], request["targets"]) == (_PERSON, ["generated-claim"])
    assert not await _visible(engine, CLAIM)


async def test_only_the_person_a_claim_is_about_may_ask_to_forget_it(engine):
    await _claim(SqlCognitiveGraph(engine), "generated-claim", persons=(_PERSON,))
    result = await _call(
        _dispute(engine, speaker=_OTHER), claim_id="generated-claim", kind="forget",
    )
    assert result["status"] == "refused" and "only they can ask" in result["note"]
    assert not await _rows(engine, FORGET_REQUEST)
    assert await _visible(engine, CLAIM) == {"generated-claim"}


async def test_an_id_no_claim_of_this_unit_holds_is_not_found_and_writes_nothing(engine):
    await _claim(SqlCognitiveGraph(engine), "generated-claim", persons=(_PERSON,), unit="other")
    for kind in ("dispute", "forget"):
        result = await _call(_dispute(engine), claim_id="generated-claim", kind=kind)
        assert result["status"] == "not_found"
    assert not await _rows(engine, DISPUTE) and not await _rows(engine, FORGET_REQUEST)
