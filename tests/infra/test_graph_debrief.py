"""The graph debrief's scope and writes through real Postgres admission."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nora.agent.egress import LeakGuard
from nora.agent.guards import ToolCallLog
from nora.doctrine.loader import Doctrine
from nora.engagement.stages import graph_debrief
from nora.engagement.stages.debrief import Completion
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.memory import ActAuthority, ActEnvelope, Outcome, act_pass
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.debrief_store import SqlDebriefStore
from nora.memory.adapters.tools.intentions import _make_intention_commit
from nora.memory.domain.acts import Act, ActIdentity
from nora.memory.wire import admission
from nora.obs.metrics import Metrics
from tests.infra.test_cognitive_graph_admission import PASS, _rows
from tests.infra.test_condition_matcher import _health
from tests.infra.test_pass_assembly import _assembler
from tests.infra.test_retrieval_scope import AT, OTHER_ROOM, ROOM, UNIT
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_situation_staleness import intention
from tests.unit.test_engagement_pipeline import _msg

CONTEXT = replace(PASS, unit=UNIT, room=ROOM, occasion="called", pass_id="finished-pass")  # noqa: S106
AUTHORITY = ActAuthority(UNIT, room=ROOM, pass_context=CONTEXT)
MESSAGE = replace(_msg(ROOM), admitted_unit=UNIT)
WALL = datetime.now(UTC)


class TranscriptCompletion:
    def __init__(self, *answers: list | BaseException):
        self.answers = iter(answers)
        self.prompts: list[str] = []
        self.model = "fixture-model"

    async def complete(self, *, system, prompt, trace_attributes=None):
        assert "Re-judge" in system
        assert "<debrief_items" in prompt
        self.prompts.append(prompt)
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        return Completion(json.dumps({"operations": answer}), "stop")


class Objective:
    def objective_body(self, name):
        assert name == "debrief"
        return "Re-judge the recorded pass."


def wiring(scenario, *answers, clock=lambda: WALL + timedelta(hours=1), service=None):
    registration = SqlItemRegistration(
        RegistrationScope(scenario.policy, scenario.persons),
        RuntimeRevisions({}, scenario.policy, scenario.persons),
    )
    acts = SqlActStore(scenario.engine, registration)
    if service is None:
        service = admission(
            scenario.engine, scenario.persons, scenario.policy,
            LeakGuard(Doctrine((), "", "", "fixture", "", 0), "fixture-canary"),
            content_checks=False,
        )
    return graph_debrief.Wiring(
        SqlDebriefStore(scenario.engine, acts), service, _assembler(scenario),
        TranscriptCompletion(*answers), Objective(), clock, registration.revisions,
        Metrics(), 1.0, 5.0,
    ), acts


def opening(**changes):
    return {
        "op": "open", "form": "achieve", "delivery": "when_it_comes_up",
        "owner": "nora", "owed_to": "nora",
        "condition": {"form": "act_event", "value": {"own_delivery": True}},
        **changes,
    }


def tool_use(name, key="use-1", **arguments):
    return {"name": name, "toolUseId": key, "input": arguments}


def event(use, result, *, cancel=None):
    return SimpleNamespace(
        tool_use=use, cancel_message=cancel,
        result={"status": "success", "content": [{"text": json.dumps(result)}]},
    )


async def send(acts, authority=AUTHORITY):
    act = Act(ActIdentity("platform.reply", CONTEXT.pass_id, 0), "delivery",
              f"signal:{ROOM}", authority, "unknown")
    assert await acts.reserve(act)
    await acts.observe(act, Outcome("done", {"text": "Saved send"}))
    return next(item.id for item in await acts.read_pass_items(UNIT, CONTEXT.pass_id)
                if item.kind == "send_outcome")


async def run(w, authority=AUTHORITY):
    await graph_debrief.run(w, authority, MESSAGE, ())


async def test_the_model_receives_pass_evidence_with_ids_and_without_reasoning(scenario):
    w, acts = wiring(scenario, [])
    envelope = ActEnvelope(acts)
    await envelope.record_expression(AUTHORITY, [
        {"text": "Visible draft"}, {"reasoningContent": {"text": "Private model reasoning"}},
    ])
    await envelope.record_tool(AUTHORITY, 0, tool_use("web_fetch", query="Generated term"), "ok",
                               [{"text": '{"answer":"Visible result","analysis":"Hidden trace"}'}])
    await send(acts)

    await run(w)

    [prompt] = w.client.prompts
    items = await acts.read_pass_items(UNIT, CONTEXT.pass_id)
    assert {item.kind for item in items} == {"expression", "tool_call", "send", "send_outcome"}
    for item in items:
        assert item.id in prompt and item.at.isoformat() in prompt
    for text in ("Visible draft", "Visible result", "Saved send"):
        assert text in prompt
    for text in ("Private model reasoning", "Hidden trace"):
        assert text not in prompt


async def test_debrief_standing_includes_unit_and_local_intentions_only(scenario):
    w, _ = wiring(scenario, [], clock=lambda: AT + timedelta(hours=2))
    await intention(scenario, "local-open")
    await intention(scenario, "other-room-open", origin_room=OTHER_ROOM)
    await intention(scenario, "unit-open", origin_room=None)

    await run(w)

    [prompt] = w.client.prompts
    assert "local-open" in prompt and "unit-open" in prompt
    assert "other-room-open" not in prompt
    assert "<graph_standing" in prompt
    assert "<graph_profile" not in prompt and "<related" not in prompt


async def test_read_only_refused_and_cancelled_tools_are_individual_graph_items(scenario):
    w, acts = wiring(scenario, [])
    hook = ToolCallLog(conversation_id=ROOM, conversation_type="group",
                       tag_id=CONTEXT.pass_id, cacheable=frozenset(), store=None,
                       metrics=w.metrics)
    tool = _make_intention_commit(SimpleNamespace(
        intention_admission=w.admission, metrics=w.metrics,
    ))
    with act_pass(AUTHORITY, ActEnvelope(acts)):
        read = tool_use("memory_read", "use-read", ref="generated-item")
        denied = tool_use("intention_commit", "use-commit", operations=[{"op": "keep"}])
        cancelled = tool_use("web_fetch", "use-cancelled")
        for use, answer in (
            (read, {"status": "ok", "text": "Read-only finding"}),
            (denied, await tool(denied["input"]["operations"])),
            (cancelled, {"status": "error"}),
        ):
            call = event(use, answer, cancel="guard veto" if use is cancelled else None)
            hook._before_pass_tool(call)
            await hook._record_pass_tool(call)

    await run(w)
    [prompt] = w.client.prompts
    rows = await acts.read_pass_items(UNIT, CONTEXT.pass_id)
    assert len({row.id for row in rows}) == 3
    for row in rows:
        assert row.kind == "tool_call" and row.id in prompt
    assert "Read-only finding" in prompt and "refused" in prompt and "cancelled" in prompt


async def test_debrief_revises_and_drops_the_named_bases_and_leaves_the_third_open(scenario):
    w, _ = wiring(scenario, [
        opening(op="revise", base="revise-base"),
        {"op": "drop", "base": "drop-base"},
    ], clock=lambda: AT + timedelta(hours=1))
    for key in ("revise-base", "drop-base", "kept-base"):
        await intention(scenario, key)

    await run(w)

    rows = await _rows(scenario.engine, INTENTION)
    assert rows["kept-base"]["state"] == "open"
    assert {(r["replaces"], r["state"], r["ending"]) for r in rows.values()
            if r["occasion"] == "debrief"} == {
        ("revise-base", "open", None), ("drop-base", "ended", "dropped"),
    }


async def test_debrief_writes_only_commit_operations_citing_its_pass_items(scenario):
    w, acts = wiring(scenario, [])
    envelope = ActEnvelope(acts)
    prior = replace(AUTHORITY, pass_context=replace(CONTEXT, pass_id="prior-pass"))  # noqa: S106
    await envelope.record_expression(prior, "Prior draft")
    await envelope.record_expression(AUTHORITY, "Current draft")
    [previous] = await acts.read_pass_items(UNIT, "prior-pass")
    [current] = await acts.read_pass_items(UNIT, CONTEXT.pass_id)
    w.client.answers = iter([[
        opening(citations=[previous.id]),
        {"op": "claim", "citations": [current.id], "content": "Prohibited claim"},
        {"op": "drop", "base": "not-shown-anymore"},
        opening(citations=[current.id]),
    ]])

    await run(w)

    assert len(await _rows(scenario.engine, INTENTION)) == 1
    assert not await _rows(scenario.engine, CLAIM)
    assert {(r["type"], r["dst_id"]) for r in (await _rows(scenario.engine, EDGE)).values()} == {
        ("cites", current.id),
    }
    assert graph_debrief.operation(opening(citations=[previous.id]), CONTEXT,
                                   frozenset({current.id}), frozenset(), None).reason == (
        "citation_outside_pass")
    assert graph_debrief.operation({"op": "claim"}, CONTEXT, frozenset(), frozenset(),
                                   None).reason == "not_a_commit"
    assert graph_debrief.operation({"op": "drop", "base": "stale"}, CONTEXT, frozenset(),
                                   frozenset(), None).reason == "stale_base"


async def test_debrief_opens_a_finding_child_with_a_serves_edge(scenario):
    w, acts = wiring(scenario, [])
    await intention(scenario, "decision", asserted_at=WALL)
    await ActEnvelope(acts).record_tool(AUTHORITY, 0, tool_use("place_search"), "ok",
                                       [{"text": "An additional option."}])
    [result] = await acts.read_pass_items(UNIT, CONTEXT.pass_id)
    w.client.answers = iter([[opening(citations=[result.id], serves=["decision"])]])

    await run(w)

    rows = await _rows(scenario.engine, INTENTION)
    [child] = [r for r in rows.values() if r["occasion"] == "debrief"]
    assert child["delivery_mode"] == "when_it_comes_up"
    assert {(r["src_id"], r["type"], r["dst_id"])
            for r in (await _rows(scenario.engine, EDGE)).values()} == {
        (child["id"], "cites", result.id), (child["id"], "serves", "decision"),
    }


async def test_debrief_closes_through_the_matcher_on_its_send_outcome(scenario):
    w, acts = wiring(scenario, [])
    await intention(scenario, "delivered", asserted_at=WALL, done_when={"own_delivery": True})
    authority = replace(AUTHORITY, intention="delivered")
    witness = await send(acts, authority)
    await _health(scenario.engine, "platform.reply", "up", unit=UNIT)
    w.client.answers = iter([[{"op": "close", "base": "delivered", "witness": witness}]])

    await run(w, authority)

    assert (await _rows(scenario.engine, INTENTION))["delivered"]["ending"] == "achieved"
    assert {(r["type"], r["src_id"], r["dst_id"])
            for r in (await _rows(scenario.engine, EDGE)).values()} == {
        ("closed_by", "delivered", witness),
    }


@pytest.mark.parametrize("with_citation", [True, False], ids=["cited", "uncited"])
async def test_proactive_debrief_can_only_move_its_own_trigger_with_evidence(
    scenario, with_citation,
):
    w, acts = wiring(scenario, [])
    await intention(scenario, "delivery-base", asserted_at=WALL)
    await intention(scenario, "other-base", asserted_at=WALL)
    await ActEnvelope(acts).record_expression(AUTHORITY, "Move the generated delivery.")
    [item] = await acts.read_pass_items(UNIT, CONTEXT.pass_id)
    trigger = {"kind": "time", "at": "2099-11-01T12:00:00Z", "zone": "UTC"}
    w.client.answers = iter([[
        opening(), {"op": "drop", "base": "other-base"},
        opening(op="revise", base="delivery-base", delivery="choose_moment",
                trigger=trigger, citations=[item.id] if with_citation else []),
    ]])

    await run(w, replace(AUTHORITY, intention="delivery-base"))

    rows = await _rows(scenario.engine, INTENTION)
    assert len(w.client.prompts) == 1
    assert rows["other-base"]["state"] == "open"
    for raw in (opening(), {"op": "drop", "base": "other-base"}):
        refused = graph_debrief.operation(
            raw, CONTEXT, frozenset({item.id}), frozenset({"delivery-base", "other-base"}),
            "delivery-base",
        )
        assert refused.reason == "not_this_delivery"
    replacements = [r for r in rows.values() if r["occasion"] == "debrief"]
    assert [(r["replaces"], r["trigger"]) for r in replacements] == (
        [("delivery-base", trigger)] if with_citation else []
    )
