"""Commit and close generated intentions through the real admission store."""

# ruff: noqa: F811 - pytest resolves imported fixtures by name.

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nora.agent.ports import adapt
from nora.agent.toolkit.spec import _spec
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, INTENTION, REGISTRY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.schema import MESSAGES
from nora.memory import ActAuthority, act_pass
from nora.memory.adapters.tools.intentions import _make_intention_commit
from nora.memory.domain.graph import IntentionForm
from nora.obs.metrics import Metrics
from tests.infra.test_cognitive_graph_admission import (
    ACCOUNT,
    PASS,
    PERSON,
    _rows,
    _service,
    engine,  # noqa: F401
)
from tests.infra.test_condition_matcher import _health
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.test_room_tools import _call

_CONTEXT = replace(PASS, occasion="called", writer="engagement")
_WITNESS = "item:person-event"
_TEXT = "The generated work is finished."
_TRIGGER = {
    "kind": "condition", "form": "predicate",
    "value": {"graph_id": "generated-aggregate", "path": ["state"], "equals": "ready"},
}


def _open(*, form: IntentionForm = "achieve", **changes) -> dict:
    return {
        "op": "open", "form": form, "delivery": "when_it_comes_up",
        "owner": "nora", "owed_to": PERSON.person_id, "citations": ["item-1"],
        "condition": {
            "form": "identity_event",
            "value": {
                "identity_id": PERSON.person_id, "platform": "signal",
                "room": "room-1", "text": _TEXT,
            },
        },
        **changes,
    }


@contextmanager
def _pass(*shown):
    with act_pass(ActAuthority(PASS.unit, pass_context=_CONTEXT), shown=frozenset(shown)):
        yield


def _tool(engine):
    return _make_intention_commit(SimpleNamespace(
        intention_admission=_service(engine), metrics=Metrics(),
    ))


async def _opened(tool, **fields):
    with _pass("item-1"):
        result = await tool([_open(**fields)])
    assert result["status"] == "ok", result
    return result["operations"][0]["id"]


async def _witness(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(REGISTRY).values(id=_WITNESS, unit=PASS.unit, kind="item"))
        await conn.execute(sa.insert(MESSAGES).values(
            graph_id=_WITNESS, unit=PASS.unit, platform="signal", conversation_id="room-1",
            origin_room="room-1", conversation_type="group", sender_id=ACCOUNT, ts=2000,
            person_id=PERSON.person_id, identities=[PERSON.person_id], body=_TEXT,
        ))
    await _health(engine, "signal", "up", unit=PASS.unit)


async def test_open_stamps_pass_provenance_and_computes_people_links(engine):
    await SqlCognitiveGraph(engine).create(ENTITY, {
        "id": "entity:person", "unit": PASS.unit, "kind": "entity", "basis": "computed",
        "state": "admitted", "writer": "fixture", "exposure": "internal",
        "name": PERSON.name, "pointer_kind": "identity", "pointer_ref": PERSON.person_id,
    })
    async with engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.graph_id == "item-1").values(
            exposure="internal", excluded_identities=["generated-exclusion"],
        ))
    graph_id = await _opened(_tool(engine))
    row = (await _rows(engine, INTENTION))[graph_id]
    assert row["exposure"] == "internal"
    assert row["excluded_identities"] == ["generated-exclusion"]
    assert row["state"] == "open"
    assert {name: row[name] for name in _CONTEXT.provenance()} == _CONTEXT.provenance()
    assert {(r["type"], r["dst_id"], r["basis"]) for r in (await _rows(engine, EDGE)).values()} == {
        ("cites", "item-1", "committed"), ("about", "entity:person", "computed"),
    }


async def test_a_citation_outside_the_pass_shown_set_is_refused(engine):
    with _pass():
        result = await _tool(engine)([_open()])
    assert result["status"] == "refused"
    assert result["operations"][0]["reason"] == "citation_not_shown"
    assert await _rows(engine, INTENTION) == {}


async def test_a_proactive_pass_can_persist_and_revise_its_own_passive_intention(engine):
    tool = _tool(engine)
    context = replace(_CONTEXT, occasion="proactive")
    with act_pass(ActAuthority(context.unit, pass_context=context), shown=frozenset({"item-1"})):
        opened = await tool([_open()])
        assert opened["status"] == "ok"
        base = opened["operations"][0]["id"]
        revised = await tool([_open(op="revise", base=base)])
        assert revised["status"] == "ok"

    rows = await _rows(engine, INTENTION)
    head = rows[revised["operations"][0]["id"]]
    assert head["state"] == "open"
    assert head["delivery_mode"] == "when_it_comes_up"
    assert (head["replaces"], rows[base]["state"]) == (base, "superseded")


async def test_a_malformed_operation_leaves_its_valid_sibling_admitted(engine):
    tool = adapt(_spec("intention_commit", _tool(engine)))
    with _pass("item-1"):
        result = await _call(tool, operations=[_open(about=["entity:topic"]), _open()])
    assert result["status"] == "refused"
    refused, admitted = result["operations"]
    assert (refused["reason"], refused["field"]) == ("invalid_field", "about")
    assert admitted["status"] == "admitted"
    assert refused.get("note") and admitted.get("note")
    assert set(await _rows(engine, INTENTION)) == {admitted["id"]}


async def test_a_predicate_trigger_is_admitted_through_the_tool(engine):
    tool = adapt(_spec("intention_commit", _tool(engine)))
    with _pass("item-1"):
        result = await _call(tool, operations=[_open(delivery="choose_moment", trigger=_TRIGGER)])
    assert result["status"] == "ok", result
    row = (await _rows(engine, INTENTION))[result["operations"][0]["id"]]
    assert (row["state"], row["delivery_mode"], row["trigger"]) == (
        "open", "choose_moment", _TRIGGER,
    )


@pytest.mark.parametrize("trigger", (
    {**_TRIGGER, "value": {"graph_id": "generated-aggregate"}},
    {"kind": "condition", "form": "deadline", "value": {"at": "2029-01-01T12:00:00Z"}},
    {"kind": "condition", "form": "untyped", "value": "When the generated work starts."},
), ids=("undecodable", "deadline", "untyped"))
async def test_admission_refuses_an_invalid_condition_trigger(engine, trigger):
    with _pass("item-1"):
        result = await _tool(engine)([_open(delivery="choose_moment", trigger=trigger)])
    assert result["status"] == "refused"
    [refusal] = result["operations"]
    assert (refusal["reason"], refusal["field"]) == ("invalid_field", "trigger")
    assert "predicate" in refusal["note"]
    assert await _rows(engine, INTENTION) == {}


async def test_moving_a_condition_trigger_requires_a_citation(engine):
    tool = _tool(engine)
    base = await _opened(tool, delivery="choose_moment", trigger=_TRIGGER)
    moved = {**_TRIGGER, "value": {**_TRIGGER["value"], "equals": "started"}}
    operation = _open(op="revise", base=base, delivery="choose_moment", trigger=moved)
    with _pass(base, "item-1"):
        refused = await tool([{**operation, "citations": []}])
        accepted = await tool([operation])
    assert refused["status"] == "refused", refused
    assert (refused["operations"][0]["reason"], refused["operations"][0]["field"]) == (
        "trigger_without_evidence", "citations",
    )
    assert accepted["status"] == "ok", accepted
    row = (await _rows(engine, INTENTION))[accepted["operations"][0]["id"]]
    assert (row["replaces"], row["trigger"]) == (base, moved)


async def test_drop_writes_the_dropped_ending(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    with _pass(base, "item-1"):
        result = await tool([{"op": "drop", "base": base, "citations": ["item-1"]}])
    assert result["status"] == "ok", result
    row = (await _rows(engine, INTENTION))[result["operations"][0]["id"]]
    assert (row["state"], row["ending"], row["replaces"]) == ("ended", "dropped", base)


@pytest.mark.parametrize("ending", ["achieved", "ceased", "leaked", "unknown", "deadline"])
async def test_drop_refuses_any_other_model_supplied_ending(engine, ending):
    tool = _tool(engine)
    base = await _opened(tool)
    with _pass(base):
        result = await tool([{"op": "drop", "base": base, "ending": ending}])
    assert result["status"] == "refused"
    refusal = result["operations"][0]
    assert (refusal["reason"], refusal["field"]) == ("invalid_field", "ending")
    assert (await _rows(engine, INTENTION))[base]["state"] == "open"


async def test_no_active_pass_cannot_commit(engine):
    result = await _tool(engine)([_open()])
    assert result["status"] == "unavailable"
    assert result["operations"] == [{
        "status": "unavailable", "commit_uncertain": False,
        "note": "The operation could not be submitted.",
    }]
    assert await _rows(engine, INTENTION) == {}


async def test_commit_uncertainty_is_reported_as_unavailable(engine):
    tool = _tool(engine)

    def fail_commit(_conn):
        raise sa.exc.OperationalError(None, None, ConnectionError("generated commit failure"))

    sa.event.listen(engine.sync_engine, "commit", fail_commit)
    try:
        with _pass("item-1"):
            result = await tool([_open()])
    finally:
        sa.event.remove(engine.sync_engine, "commit", fail_commit)
    assert result["status"] == "unavailable"
    assert result["operations"] == [{
        "status": "unavailable", "commit_uncertain": True,
        "note": "Read back the intention before retrying.",
    }]


async def test_close_uses_the_named_message_and_writes_one_closed_by_edge(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    await _witness(engine)
    with _pass(base, _WITNESS):
        result = await tool([{"op": "close", "base": base, "witness": _WITNESS}])
    assert result["status"] == "ok", result
    assert (await _rows(engine, INTENTION))[base]["ending"] == "achieved"
    with _pass(base, _WITNESS):
        closed = await tool([{"op": "close", "base": base, "witness": _WITNESS}])
    assert closed["operations"][0]["reason"] == "not_open"
    edges = (await _rows(engine, EDGE)).values()
    assert [(r["src_id"], r["dst_id"]) for r in edges if r["type"] == "closed_by"] == [
        (base, _WITNESS),
    ]


async def test_close_refuses_the_wrong_or_unshown_witness(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    await _witness(engine)
    with _pass(base, "item-1"):
        wrong = await tool([{"op": "close", "base": base, "witness": "item-1"}])
        missing = await tool([{"op": "close", "base": base, "witness": _WITNESS}])
    assert wrong["operations"][0]["reason"] == "witness_not_met"
    assert missing["operations"][0]["reason"] == "citation_not_shown"
    assert (await _rows(engine, INTENTION))[base]["state"] == "open"


async def test_close_refuses_an_untyped_condition(engine):
    tool = _tool(engine)
    base = await _opened(
        tool, condition={"form": "untyped", "value": "When the fixture is ready."},
        deadline="2029-01-01T12:00:00Z",
    )
    with _pass(base, "item-1"):
        result = await tool([{"op": "close", "base": base, "witness": "item-1"}])
    assert result["operations"][0]["reason"] == "untyped_condition"
    assert (await _rows(engine, INTENTION))[base]["state"] == "open"


async def test_revise_stores_the_replacement_and_close_refuses_the_stale_base(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    with _pass(base, "item-1"):
        revised = await tool([_open(op="revise", base=base)])
        closed = await tool([{"op": "close", "base": base, "witness": "item-1"}])
    assert revised["status"] == "ok", revised
    current = revised["operations"][0]["id"]
    rows = await _rows(engine, INTENTION)
    assert (rows[current]["replaces"], rows[base]["state"]) == (base, "superseded")
    assert closed["operations"][0]["reason"] == "stale_base"


async def test_a_later_commit_can_revise_an_intention_opened_in_the_same_pass(engine):
    tool = _tool(engine)
    with _pass("item-1"):
        opened = await tool([_open()])
        assert opened["status"] == "ok", opened
        base = opened["operations"][0]["id"]
        revised = await tool([_open(op="revise", base=base)])
    assert revised["status"] == "ok", revised
    current = revised["operations"][0]["id"]
    rows = await _rows(engine, INTENTION)
    assert (rows[current]["replaces"], rows[base]["state"]) == (base, "superseded")


async def _wake_tools(scenario, *operations, shown):
    from tests.infra.test_graph_propose import surface, wake_scope
    from tests.infra.test_pass_anchors import NOW, SCOPE

    tool = next(tool for tool in surface(scenario).bind(SCOPE, NOW, lambda: None)
                if tool.tool_name == "intention_commit")
    with wake_scope(*shown):
        return await _call(tool, operations=list(operations))


async def _inferred_topics(scenario, row_id):
    async with scenario.engine.connect() as conn:
        return set(await conn.scalars(sa.select(EDGE.c.dst_id).where(
            EDGE.c.src_id == row_id, EDGE.c.type == "about", EDGE.c.basis == "inferred",
            EDGE.c.state == "live",
        )))


async def test_wake_open_links_a_new_topic_and_rediscover_returns_it(scenario):
    from tests.infra.test_graph_debrief import opening
    from tests.infra.test_graph_propose import about, topic

    await scenario.item("observed-source")
    result = await _wake_tools(
        scenario, topic(), opening(citations=["observed-source"], about=[about()]),
        shown=("observed-source",),
    )
    assert result["status"] == "ok", result
    new_topic, commitment = result["operations"]
    assert await _inferred_topics(scenario, commitment["id"]) == {new_topic["id"]}
    assert ["entity", new_topic["id"]] in commitment["neighbourhood"]["keys"]


async def test_decide_writes_committed_rows_and_indexes_about_by_derivation(scenario):
    from tests.infra.test_graph_debrief import CONTEXT, opening
    from tests.infra.test_graph_propose import about, topic
    from tests.infra.test_retrieval_scope import PERSON as MEMBER
    from tests.infra.test_room_busy import service, slots

    await scenario.item("observed-source")
    await scenario.entity("fixture-person-pointer", pointer_kind="identity", pointer_ref=MEMBER)
    result = await _wake_tools(
        scenario, topic("live-topic"), topic("retired-topic"),
        opening(citations=["observed-source"], owed_to=MEMBER,
                about=[about("live-topic"), about("retired-topic")]),
        shown=("observed-source",),
    )
    live, retired, base = [entry["id"] for entry in result["operations"]]
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.update(EDGE).where(
            EDGE.c.src_id == base, EDGE.c.dst_id == retired, EDGE.c.type == "about",
        ).values(state="retired"))
    context = replace(CONTEXT, occasion="called", writer="engagement")
    tool = adapt(_spec("intention_commit", _make_intention_commit(SimpleNamespace(
        intention_admission=service(scenario, slots(scenario)), metrics=Metrics(),
    ))))
    before = set(await _rows(scenario.engine, REGISTRY))
    with act_pass(
        ActAuthority(context.unit, pass_context=context),
        shown=frozenset({base, "observed-source"}),
    ):
        revised = await _call(tool, operations=[
            opening(op="revise", base=base, owed_to=MEMBER, citations=["observed-source"]),
        ])
    assert revised["status"] == "ok", revised
    new_id = revised["operations"][0]["id"]
    assert await _inferred_topics(scenario, new_id) == {live}
    registered = await _rows(scenario.engine, REGISTRY)
    written = registered.keys() - before
    edges = (await _rows(scenario.engine, EDGE)).items()
    new_edges = {id_: edge for id_, edge in edges if id_ in written}
    assert written == {new_id, *new_edges}
    assert (await _rows(scenario.engine, INTENTION))[new_id]["basis"] == "committed"
    assert {
        (edge["src_id"], edge["type"], edge["dst_id"], edge["basis"])
        for edge in new_edges.values()
    } == {
        (new_id, "cites", "observed-source", "committed"),
        (new_id, "about", live, "inferred"),
        (new_id, "about", "fixture-person-pointer", "computed"),
    }


async def test_graph_propose_claim_revision_carries_subject_unless_explicitly_replaced(scenario):
    from tests.infra.test_graph_propose import about, claim_payload, propose, topic, wake_scope

    await scenario.item("observed-source")
    with wake_scope("observed-source"):
        opened = await propose(scenario, [topic(), claim_payload(about=[about()])])
        entity, first = [entry["id"] for entry in opened["operations"]]
        revise = await propose(scenario, [claim_payload(op="revise", base=first)])
        [next_head] = revise["operations"]
        assert await _inferred_topics(scenario, next_head["id"]) == {entity}
        explicit = await propose(scenario, [
            topic("replacement"),
            claim_payload(op="revise", base=next_head["id"], about=[about("replacement")]),
        ])
    replacement, new_head = [entry["id"] for entry in explicit["operations"]]
    assert await _inferred_topics(scenario, new_head) == {replacement}


async def test_scheduled_deliveries_with_the_same_topic_and_recipient_are_arbitrated(scenario):
    from tests.infra.test_graph_debrief import opening
    from tests.infra.test_graph_propose import about
    from tests.infra.test_retrieval_scope import PERSON as MEMBER

    await scenario.item("observed-source")
    await scenario.entity("delivery-topic")
    scheduled = opening(
        owed_to=MEMBER, citations=["observed-source"], delivery="choose_moment",
        trigger={"kind": "time", "at": "2027-01-01T12:00:00Z", "zone": "UTC"},
        about=[about("delivery-topic")],
    )
    result = await _wake_tools(
        scenario, scheduled, scheduled, shown=("observed-source", "delivery-topic"),
    )
    first, duplicate = result["operations"]
    assert first["status"] == "admitted", result
    assert (duplicate.get("reason"), duplicate.get("field")) == ("duplicate_delivery", first["id"])


async def test_a_called_pass_refuses_the_valid_wake_about_shape(engine):
    operation = _open(about=[{
        "target": "entity:generated-topic", "reasoning": "deduced", "confidence": "high",
    }])
    with _pass("item-1"):
        result = await _tool(engine)([operation])
    [refusal] = result["operations"]
    assert (refusal.get("reason"), refusal.get("field")) == ("invalid_field", "about")
