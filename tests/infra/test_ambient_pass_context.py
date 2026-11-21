"""Participation renders the scoped graph from the actual fire context."""

import sqlalchemy as sa

from nora.app.fire_engagement import AmbientEngagement
from nora.engagement.graph_context import EngagementGraph
from nora.engagement.subagents_context import SubagentsContextReader
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import GRAPH_SEARCH
from nora.memory import wire
from nora.memory.adapters.pass_assembly import PassAssembler
from nora.memory.app.retrieval import RetrievalService
from nora.testing import FakeAgentRunner, FakeComputeJobStore, ScriptedTurn
from tests.infra.test_called_pass_context import called as called
from tests.infra.test_called_pass_context import elements
from tests.infra.test_pass_anchors import reader
from tests.infra.test_pass_assembly import CountedRetrieval
from tests.infra.test_retrieval_scope import ROOM
from tests.infra.test_retrieval_scope import scenario as scope_scenario
from tests.unit.test_fire_path import _fire_path, _participation
from tests.unit.test_fire_path import metrics as metrics
from tests.unit.test_subagents_block import _job

scenario = scope_scenario


async def test_ambient_presents_window_people_and_the_room_floor_in_assembled_order(
    scenario, called, doctrine_store, metrics,
):
    access, _, _, now = called
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.delete(GRAPH_SEARCH))
    retrieval = CountedRetrieval(scenario)
    assembler = PassAssembler(reader(scenario), RetrievalService(retrieval, None))
    jobs = FakeComputeJobStore()
    await jobs.create(_job(room=("signal", ROOM), title="Generated task in this room"))
    await jobs.create(_job(title="Generated task in another room"))
    subagents = SubagentsContextReader(jobs=jobs, clock=lambda: now, metrics=metrics)
    graph = EngagementGraph(access, wire.message_graph_ids(scenario.engine), assembler)
    runner_calls = []
    runner = FakeAgentRunner([ScriptedTurn(final_text="")], on_call=runner_calls.append)
    path = _fire_path(
        persons=scenario.persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=runner, participation_runner=runner,
        records=SqlRecordStore(scenario.engine), policy=scenario.policy,
        defer_tools=True, ambient=AmbientEngagement(graph, subagents=subagents),
    )
    path._clock = lambda: now

    assert await path.speak(_participation(ROOM)) == "silent"

    (call,) = runner_calls
    blocks = elements(call["tag_message"])
    assert [b.tag for b in blocks] == [
        "graph_profile", "graph_lessons", "graph_profile", "graph_profile",
        "members", "clock", "presence", "tool_catalogue", "subagents", "transcript",
        "tag_message", "graph_profile", "graph_standing", "related",
    ]
    assert blocks.find("subagents") is not None
    assert "Generated task in this room" in call["tag_message"]
    assert "Generated task in another room" not in call["tag_message"]
    assert blocks[11].get("of") == "person-here"
    assert "Generated sender fact" in (blocks[11].text or "")
    assert blocks[11].get("cause") == "query_embedding_failed"
    assert len(retrieval.reads) == 1
