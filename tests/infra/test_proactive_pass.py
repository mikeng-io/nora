"""The proactive occasion through the real graph, tool surface and debrief."""

from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest
import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.agent.delivery_acts import DeliveryActs
from nora.agent.lineage import LineagePolicy
from nora.agent.registry import ToolRegistry
from nora.app.proactive_fire import FireAgent, FireRoom, ProactiveFire
from nora.app.proactive_poller import ProactiveLanding, ProactivePoller
from nora.doctrine.loader import DoctrineStore
from nora.domain.quote_window import QuoteWindow
from nora.domain.tag_trace import TagTrace
from nora.engagement.stages import graph_debrief
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import (
    DEBRIEF_OBLIGATION,
    INTENTION,
    NORA_PASS_OUTPUT,
)
from nora.infra.sql.item_registration import RegistrationScope
from nora.memory import ActAuthority, ActEnvelope, DebriefObligation, wire
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from tests.fakes import SinkSender
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_graph_debrief import CONTEXT, opening, wiring
from tests.infra.test_pass_assembly import _assembler
from tests.infra.test_proactive_claims import DUE, claim_rows, seed_due, slots
from tests.infra.test_retrieval_scope import DM, OTHER, ROOM, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scenario

NOW = DUE + timedelta(minutes=1)


@dataclass(frozen=True)
class Decision:
    status: Literal["deliver", "unavailable"]
    reason: str | None = None


class ExpectedDestination:
    async def recheck(self, intention_id, destination, now):
        if (intention_id, destination, now) == ("scheduled", f"signal:{ROOM}", NOW):
            return Decision("deliver")
        return Decision("unavailable", "fixture_input_unexpected")


class ScriptedRunner:
    model_id = "fixture-model"

    def __init__(self, *scripts):
        self.scripts = iter(scripts)
        self.calls = []
        self.results = []

    async def invoke(self, **call):
        self.calls.append(call)
        tools = {tool.tool_name: tool for tool in call["tools"]}
        for name, arguments in next(self.scripts):
            result = await tools[name]._tool_func(**arguments)
            self.results.append(result)
        ctx = call["ctx"]
        return "end_turn", "", TagTrace(
            ctx.tag_id, ctx.message.conversation_id, (), (), "silent", 0,
        )


def setup(scenario, runner, *answers):
    debrief, act_store = wiring(scenario, *answers, clock=lambda: NOW)
    acts = ActEnvelope(act_store)
    scope = RegistrationScope(scenario.policy, scenario.persons)
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    audit = SqlRecordStore(
        scenario.engine, delivery_acts=DeliveryActs(acts, scope, debrief.revisions),
    )
    claims = SqlProactiveClaims(scenario.engine)
    sender = SinkSender()
    metrics = debrief.metrics
    graph = wire.graph_read_tools(scenario.engine, access, LineagePolicy.from_policy(
        scenario.policy, access=access), None, None)
    registry = ToolRegistry(display_name="Nora", content_checks=False, unit_for=scope.room_unit,
                            engagement_graph_retrieval=graph, intention_admission=debrief.admission)
    room = FireRoom(access, audit, scenario.persons, QuoteWindow(audit=audit, metrics=metrics),
                    None, scenario.policy, {"signal": sender}, {}, None)
    agent = FireAgent(
        runner, registry, DoctrineStore(Path("nora/doctrine"), metrics=metrics),
        "fixture-canary", "Nora", 16, {}, False, 4, metrics, debrief.revisions, None, None,
    )
    turn = ProactiveFire(room, agent, _assembler(scenario), claims, acts, debrief,
                         ExpectedDestination(), lambda: NOW)
    landing = ProactiveLanding(scenario.policy, access, {"signal"})
    poller = ProactivePoller(claims, slots(), landing, turn, lambda: NOW,
                             tick_seconds=1, timeout_seconds=4)
    return poller, sender, debrief


async def test_the_prompt_pins_the_due_intention_and_its_basis_without_legacy_memory(scenario):
    await seed_due(scenario)
    await scenario.item("basis-item", body="Generated room text")
    await scenario.claim("basis-claim", content={"text": "Generated claim body"})
    await write_edge(scenario, "scheduled", "basis-claim", "rests_on")
    await write_edge(scenario, "basis-claim", "basis-item", "cites")
    runner = ScriptedRunner(())
    poller, _, _ = setup(scenario, runner, [])

    await poller.poll()

    [request] = runner.calls
    prompt = request["tag_message"]
    order = ("<due_intention", "scheduled", "Generated claim body", "<why_now", DUE.isoformat(),
             "<transcript", "Generated room text", "<members", "<clock", "<presence")
    offsets = []
    for part in order:
        offsets.append(prompt.index(part, offsets[-1] + 1 if offsets else 0))
    for block in ("<peer_card", "<standing", "<notes", "<past_facts", "<peer_history"):
        assert block not in prompt
    assert request["doctrine"].sha256


async def test_a_unit_scope_intention_lands_in_the_owed_to_persons_dm(scenario):
    await seed_due(scenario, origin_room=None, owed_to=OTHER)
    runner = ScriptedRunner(())
    poller, _, debrief = setup(scenario, runner, [])
    await poller.poll()
    [request] = runner.calls
    assert request["ctx"].message.target().conversation_id == DM
    assert request["ctx"].message.target().conversation_type == "dm"
    assert len(debrief.client.prompts) == 1


async def test_no_dm_records_authorized_unit_silence_without_a_model_call(scenario):
    await seed_due(scenario, origin_room=None)
    runner = ScriptedRunner()
    poller, sender, debrief = setup(scenario, runner)
    await poller.poll()
    [row] = await claim_rows(scenario)
    [output] = (await _rows(scenario.engine, NORA_PASS_OUTPUT)).values()
    assert (output["unit"], output["authority_intention"], output["origin_room"],
            output["output_kind"], output["silence_reason"], output["pass"]) == (
        UNIT, "scheduled", None, "silence", "no_destination", row["pass_id"],
    )
    assert row["state"] == "closed"
    assert runner.calls == debrief.client.prompts == sender.sent == []
    assert not await _rows(scenario.engine, DEBRIEF_OBLIGATION)
    await poller.poll()
    assert await claim_rows(scenario) == [row]


async def test_the_proactive_surface_cannot_open_another_intention(scenario):
    await seed_due(scenario)
    runner = ScriptedRunner((("intention_commit", {"operations": [opening()]}),))
    poller, _, _ = setup(scenario, runner, [])
    await poller.poll()
    [outcome] = runner.results
    assert outcome["operations"][0]["reason"] == "not_this_delivery"
    assert set(await _rows(scenario.engine, INTENTION)) == {"scheduled"}


async def test_the_proactive_surface_moves_its_trigger_with_a_shown_item(scenario):
    await seed_due(scenario)
    await scenario.item("move-evidence")
    await scenario.claim("move-basis")
    await write_edge(scenario, "scheduled", "move-basis", "rests_on")
    await write_edge(scenario, "move-basis", "move-evidence", "cites")
    trigger = {"kind": "time", "at": "2099-11-01T12:00:00Z", "zone": "UTC"}
    move = opening(op="revise", base="scheduled", delivery="choose_moment",
                   trigger=trigger, citations=["move-evidence"])
    runner = ScriptedRunner((("intention_commit", {"operations": [move]}),))
    poller, _, _ = setup(scenario, runner, [])
    await poller.poll()
    [outcome] = runner.results
    assert outcome["status"] == "ok", outcome
    intentions = await _rows(scenario.engine, INTENTION)
    [new] = [row for row in intentions.values() if row["replaces"] == "scheduled"]
    assert new["trigger"] == trigger


@pytest.mark.parametrize("sends", [True, False], ids=["send", "silence"])
async def test_a_routed_proactive_pass_debriefs_once_and_refuses_its_open(scenario, sends):
    await seed_due(scenario)
    script = (("send_reply", {"text": "Generated reminder"}),) if sends else ()
    runner = ScriptedRunner(script)
    poller, sender, debrief = setup(scenario, runner, [opening()])
    await poller.poll()
    await poller.poll()
    [prompt] = debrief.client.prompts
    assert f'"kind": "{"send_outcome" if sends else "silence"}"' in prompt
    assert 'delivered_intention="scheduled"' in prompt
    assert sender.texts == (["Generated reminder"] if sends else [])
    assert set(await _rows(scenario.engine, INTENTION)) == {"scheduled"}
    async with scenario.engine.connect() as conn:
        [row] = (await conn.execute(sa.select(DEBRIEF_OBLIGATION))).mappings().all()
        assert (row["state"], row["intention"], row["attempts"]) == ("settled", "scheduled", 1)


async def test_the_proactive_surface_matches_the_pinned_signal_grant(scenario):
    await seed_due(scenario)
    runner = ScriptedRunner(())
    poller, _, _ = setup(scenario, runner, [])
    await poller.poll()
    [request] = runner.calls
    assert {tool.tool_name for tool in request["tools"]} == {
        "calculate", "event_search", "file_fetch", "find_parking", "generate_image",
        "group_members", "instagram_read", "instagram_search", "intention_commit", "linkedin_read",
        "linkedin_search", "list_reminders", "load_transcript", "lookup_person", "media_fetch",
        "media_search", "media_send", "memory_read", "memory_search", "note_read", "note_search",
        "now", "online_now", "parking_status", "place_info", "reaction_send", "recall_lookups",
        "reverse_geocode", "room_list", "room_read", "route", "routine_read", "saved_places",
        "search_gifs", "send_gif", "send_reply", "suggest_venue", "transit", "version_list",
        "version_read", "weather", "web_fetch", "web_search", "where_been", "where_is", "x_read",
        "x_search",
    }


async def test_the_rooms_pending_debrief_runs_before_the_proactive_pass(scenario):
    await seed_due(scenario)
    seen_at_start = []

    class ReadPending(ScriptedRunner):
        async def invoke(self, **call):
            seen_at_start.extend(debrief.client.prompts)
            return await super().invoke(**call)

    runner = ReadPending(())
    poller, _, debrief = setup(scenario, runner, [], [])
    pending = DebriefObligation(UNIT, f"signal:{ROOM}", "earlier-pass", "ambient", None,
                                DUE, None, None)
    assert await debrief.store.begin(pending)
    await poller.poll()
    first, second = debrief.client.prompts
    assert seen_at_start == [first]
    assert 'occasion="ambient"' in first
    assert 'occasion="proactive"' in second
    assert await debrief.store.take_oldest(UNIT, f"signal:{ROOM}") is None


@pytest.mark.parametrize("recorded", [True, False], ids=["silence-recorded", "failed-pass"])
async def test_restart_makes_only_completed_claims_ready_for_debrief(scenario, recorded):
    await seed_due(scenario)
    debrief, act_store = wiring(scenario, [], clock=lambda: NOW)
    claims = SqlProactiveClaims(scenario.engine)
    [due] = await claims.due_intentions(DUE)
    claim = await claims.claim(due, DUE)
    assert claim is not None
    room = f"signal:{ROOM}"
    obligation = DebriefObligation(UNIT, room, claim.pass_id, "proactive", due.intention,
                                   DUE, None, None)
    assert await claims.stage_debrief(claim, obligation)
    assert await debrief.store.take_oldest(UNIT, room) is None

    if recorded:
        ctx = replace(CONTEXT, pass_id=claim.pass_id, occasion="proactive")
        await ActEnvelope(act_store).record_silence(ActAuthority(
            UNIT, intention=due.intention, room=ROOM, pass_context=ctx,
        ))
    await claims.recover(NOW, timedelta(seconds=30))
    await graph_debrief.retry(debrief, UNIT, "signal", ROOM)

    async with scenario.engine.connect() as conn:
        states = list(await conn.scalars(sa.select(DEBRIEF_OBLIGATION.c.state)))
    assert states == (["settled"] if recorded else [])
    assert len(debrief.client.prompts) == (1 if recorded else 0)
