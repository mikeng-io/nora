"""Run reflection proposals through production admission and scoped retrieval."""

from dataclasses import replace

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.memory import ActAuthority, act_pass, wire
from nora.memory.app.act_scope import pass_shown
from nora.obs.metrics import Metrics
from tests.infra.test_graph_debrief import CONTEXT
from tests.infra.test_pass_anchors import NOW, SCOPE
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_room_busy import service, slots
from tests.test_room_tools import _call

WAKE = replace(CONTEXT, occasion="wake", writer="reflection")
BASIS = {"reasoning": "deduced", "confidence": "high"}


def surface(scenario):
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    lineage = LineagePolicy.from_policy(scenario.policy, access=access)
    return wire.WakeToolSurface(
        service(scenario, slots(scenario)),
        wire.graph_read_tools(scenario.engine, access, lineage, None, None),
        lambda platform, id_: Room(
            platform=platform, conversation_id=id_, roster_source=None, access=access,
        ),
        Metrics(), content_checks=False, offered=wire.WAKE_TOOLS,
        allowed_operations=wire.WAKE_OPERATIONS,
    )


def topic(ref="generated-topic"):
    return {"op": "create_entity", "name": "Generated subject", "citations": [], "ref": ref,
            **BASIS}


def claim_payload(**changes):
    return {"op": "create_claim", "part": "fact", "content": "Generated fact",
            "citations": ["observed-source"], **BASIS, **changes}


def about(target="generated-topic"):
    return {"target": target, **BASIS}


async def propose(scenario, operations):
    tool = next(tool for tool in surface(scenario).bind(SCOPE, NOW, lambda: None)
                if tool.tool_name == "graph_propose")
    return await _call(tool, operations=operations)


def wake_scope(*shown):
    return act_pass(ActAuthority(WAKE.unit, pass_context=WAKE), shown=frozenset(shown))


async def test_propose_reads_the_new_claim_and_its_topic_depth_one(scenario):
    await scenario.item("observed-source")
    with wake_scope("observed-source"):
        result = await propose(scenario, [
            topic(), claim_payload(about=[about()]),
        ])
        assert result["status"] == "ok", result
        entity, claim = result["operations"]
        neighbourhood = claim["neighbourhood"]
        assert {entity["id"], "observed-source", claim["id"]} <= {
            id_ for _, id_ in neighbourhood["keys"]
        }
        assert {entity["id"], claim["id"]} <= pass_shown()


async def test_graph_propose_refuses_a_real_citation_the_pass_did_not_see(scenario):
    await scenario.item("observed-source")
    with wake_scope():
        result = await propose(scenario, [claim_payload()])
    [refused] = result["operations"]
    assert (refused["status"], refused.get("reason")) == ("refused", "citation_not_shown")


async def test_orphan_entity_needs_an_evidenced_sibling(scenario):
    await scenario.item("observed-source")
    with wake_scope("observed-source"):
        result = await propose(scenario, [topic()])
        assert result["operations"][0].get("reason") == "orphan_entity"
        result = await propose(scenario, [topic(), claim_payload(about=[about()])])
        assert [entry["status"] for entry in result["operations"]] == ["admitted", "admitted"]


async def test_graph_propose_checks_lessons_separately_from_unknown_fields(scenario):
    await scenario.item("observed-source")
    with wake_scope("observed-source"):
        result = await propose(scenario, [
            claim_payload(part="lesson"), claim_payload(typo="ignored?"), claim_payload(),
        ])
    lesson, unknown, admitted = result["operations"]
    assert (lesson.get("reason"), lesson.get("field")) == ("lesson_not_about_nora", "about")
    assert (unknown.get("reason"), unknown.get("field")) == ("invalid_field", "typo")
    assert admitted["status"] == "admitted"


async def test_an_inferred_edge_keeps_its_own_observed_citation(scenario):
    import sqlalchemy as sa

    from nora.infra.sql.cognitive_graph_schema import EDGE

    await scenario.item("observed-source")
    await scenario.item("observed-evidence")
    await scenario.entity("observed-topic")
    with wake_scope("observed-source", "observed-evidence", "observed-topic"):
        result = await propose(scenario, [{
            "op": "add_edge", "type": "about", "source": "observed-source",
            "target": "observed-topic", "citations": ["observed-evidence"], **BASIS,
        }])
    [edge] = result["operations"]
    assert edge["status"] == "admitted", result
    async with scenario.engine.connect() as conn:
        links = (await conn.execute(sa.select(
            EDGE.c.type, EDGE.c.src_id, EDGE.c.dst_id,
        ).order_by(EDGE.c.type))).all()
    assert links == [
        ("about", "observed-source", "observed-topic"),
        ("cites", edge["id"], "observed-evidence"),
    ]
