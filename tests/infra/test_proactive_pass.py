"""The proactive occasion through the real graph, tool surface and debrief."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.agent.delivery_acts import DeliveryActs
from nora.agent.lineage import LineagePolicy
from nora.agent.registry import ToolRegistry
from nora.app.graph_observe import graph_observe
from nora.app.proactive_fire import FireAgent, FireRoom, ProactiveFire
from nora.app.proactive_poller import ProactiveLanding, ProactivePoller
from nora.doctrine.loader import DoctrineStore
from nora.domain.ports import SendResult
from nora.domain.quote_window import QuoteWindow
from nora.domain.tag_trace import TagTrace
from nora.engagement.stages import graph_debrief
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import (
    DEBRIEF_OBLIGATION,
    EDGE,
    INTENTION,
    NORA_ACT,
    NORA_ACT_OUTCOME,
    NORA_PASS_OUTPUT,
)
from nora.infra.sql.item_registration import RegistrationScope
from nora.memory import ActAuthority, ActEnvelope, DebriefObligation, act_pass, wire
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from nora.memory.adapters.tools.intentions import _make_intention_commit
from nora.obs.metrics import Metrics
from nora.policy import Policy
from nora.testing.room_slots import FakeRoomSlots
from tests.fakes import SinkSender
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_condition_matcher import _health
from tests.infra.test_condition_triggers import _TEXT
from tests.infra.test_condition_triggers import _schedule as _schedule_condition_trigger
from tests.infra.test_graph_debrief import CONTEXT, opening, wiring
from tests.infra.test_pass_assembly import _assembler
from tests.infra.test_proactive_claims import DUE, claim_rows, publish_due, seed_due, slots
from tests.infra.test_retrieval_scope import AT, DM, OTHER, OTHER_ROOM, ROOM, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_situation_staleness import intention, refuse_count

NOW = DUE + timedelta(minutes=1)


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


def setup(scenario, runner, *answers, proactive=True, sender=None, now=NOW, room_slots=None):
    rooms = tuple(replace(room, proactive=proactive) for room in scenario.policy.rooms())
    policy = Policy(rooms, _by_id={(room.platform, room.primary_id): room for room in rooms})
    scenario = replace(scenario, policy=policy)
    debrief, act_store = wiring(scenario, *answers, clock=lambda: now)
    acts = ActEnvelope(act_store)
    scope = RegistrationScope(scenario.policy, scenario.persons)
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    audit = SqlRecordStore(
        scenario.engine, delivery_acts=DeliveryActs(acts, scope, debrief.revisions),
    )
    claims = SqlProactiveClaims(scenario.engine)
    sender = sender or SinkSender()
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
                         wire.delivery_arbitration(scenario.engine, policy), lambda: now)
    landing = ProactiveLanding(scenario.policy, access, {"signal"})
    shared_slots = slots() if room_slots is None else room_slots
    poller = ProactivePoller(claims, shared_slots, landing, turn, lambda: now,
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


async def test_a_proactive_reply_cannot_quote_its_internal_due_basis(scenario):
    await seed_due(scenario)
    internal = "The generated ledger describes a completely fictional catalogue entry"
    await scenario.item("internal-witness", body="Generated evidence for the catalogue")
    await scenario.claim("internal-basis", content={"text": internal})
    await write_edge(scenario, "internal-basis", "internal-witness", "cites")
    await write_edge(scenario, "scheduled", "internal-basis", "rests_on")
    runner = ScriptedRunner((("send_reply", {"text": internal}),))
    worker, sender, debrief = setup(scenario, runner, [])

    await worker.poll()

    [request] = runner.calls
    assert "exposure=internal" in request["tag_message"] and internal in request["tag_message"]
    assert sender.sent == []
    [claim] = await claim_rows(scenario)
    [output] = (await _rows(scenario.engine, NORA_PASS_OUTPUT)).values()
    assert (output["silence_reason"], claim["state"]) == ("egress_block", "closed")
    assert len(debrief.client.prompts) == 1


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
        "calculate", "file_fetch", "find_parking", "generate_image",
        "group_members", "instagram_read", "instagram_search", "intention_commit", "linkedin_read",
        "linkedin_search", "load_transcript", "lookup_person", "media_fetch",
        "media_search", "media_send", "memory_read", "memory_search",
        "now", "online_now", "parking_status", "place_info", "reaction_send",
        "reverse_geocode", "room_list", "room_read", "route", "routine_read", "saved_places",
        "search_gifs", "send_gif", "send_reply", "transit", "version_list",
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

_SEND = ("send_reply", {"text": "Generated reminder"})


async def test_observe_and_the_proactive_poller_deliver_a_condition_trigger_once(scenario):
    await _schedule_condition_trigger(scenario, origin_room=f"signal:{ROOM}")
    await _health(scenario.engine, "signal", "up", unit=UNIT)
    await scenario.item("witness", _TEXT, at=AT)
    runner = ScriptedRunner((_SEND,))
    poller, sender, _ = setup(scenario, runner, [])
    observe = graph_observe(scenario.engine, lambda: AT, Metrics(), room_slots=FakeRoomSlots({}))

    await observe.tick()
    await poller.poll()
    await observe.tick()
    await poller.poll()

    [claim] = await claim_rows(scenario)
    assert (claim["graph_id"], claim["occurrence"], claim["state"]) == (
        "waiting", "witness", "closed",
    )
    assert len(sender.sent) == len(runner.calls) == 1


async def test_a_due_reminder_closes_by_its_recorded_delivery_and_cannot_be_claimed_again(scenario):
    scheduled = await seed_due(scenario, done_when={"own_delivery": True})
    runner = ScriptedRunner((_SEND,))
    worker, sender, _ = setup(scenario, runner, [])

    await worker.poll()
    [claim] = await claim_rows(scenario)
    assert claim["state"] == "closed"
    assert sender.texts == ["Generated reminder"]
    async with scenario.engine.connect() as conn:
        [act] = (await conn.execute(sa.select(NORA_ACT))).mappings().all()
        [outcome] = (await conn.execute(sa.select(NORA_ACT_OUTCOME))).mappings().all()
    assert (act["authority_intention"], act["trigger_id"]) == (scheduled, claim["pass_id"])
    assert outcome["act_id"] == act["id"] and outcome["verdict"] == "done"
    await wire.condition_matcher(scenario.engine).run_once()
    [ending] = (await _rows(scenario.engine, INTENTION)).values()
    assert (ending["state"], ending["ending"]) == ("ended", "achieved")
    async with scenario.engine.connect() as conn:
        assert (await conn.execute(sa.select(EDGE.c.src_id, EDGE.c.dst_id).where(
            EDGE.c.type == "closed_by",
        ))).all() == [(scheduled, outcome["graph_id"])]

    await worker.poll()
    assert await claim_rows(scenario) == [claim]
    assert len(runner.calls) == len(sender.sent) == 1


async def test_a_withhold_recheck_records_silence_and_closes_the_claim(scenario):
    await seed_due(scenario)
    runner = ScriptedRunner((_SEND,))
    worker, sender, debrief = setup(scenario, runner, [], proactive=False)
    await worker.poll()

    [claim] = await claim_rows(scenario)
    assert (claim["state"], claim["attempts"]) == ("closed", 0)
    assert len(runner.calls) == 1 and sender.sent == []
    assert not await _rows(scenario.engine, NORA_ACT)
    [output] = (await _rows(scenario.engine, NORA_PASS_OUTPUT)).values()
    assert (output["output_kind"], output["silence_reason"], output["recheck_reason"],
            output["pass"]) == (
        "silence", "room_not_allowed", None, claim["pass_id"],
    )
    assert len(debrief.client.prompts) == 1


async def _unavailable_poll(scenario, worker):
    with refuse_count(scenario.engine, "SELECT intention.owed_to, intention.asserted_at"):
        await worker.poll()


async def test_an_unavailable_recheck_releases_without_sending_and_records_why(scenario):
    await seed_due(scenario)
    runner = ScriptedRunner((_SEND,))
    worker, sender, debrief = setup(scenario, runner)

    await _unavailable_poll(scenario, worker)

    [claim] = await claim_rows(scenario)
    assert (claim["state"], claim["attempts"]) == ("released", 1)
    assert len(runner.calls) == 1 and sender.sent == []
    assert not await _rows(scenario.engine, NORA_ACT)
    [output] = (await _rows(scenario.engine, NORA_PASS_OUTPUT)).values()
    assert (output["output_kind"], output["silence_reason"], output["recheck_reason"],
            output["pass"]) == (
        "silence", "recheck_unavailable", "read_failed", claim["pass_id"],
    )
    assert debrief.client.prompts == []


async def test_a_released_recheck_can_deliver_once_on_a_later_poll(scenario):
    await seed_due(scenario)
    runner = ScriptedRunner((_SEND,), (_SEND,))
    worker, sender, _ = setup(scenario, runner, [])
    await _unavailable_poll(scenario, worker)
    [first] = await claim_rows(scenario)
    assert sender.sent == []

    await worker.poll()
    [closed] = await claim_rows(scenario)
    assert (closed["state"], closed["attempts"]) == ("closed", 1)
    assert closed["pass_id"] != first["pass_id"]
    await worker.poll()
    assert len(runner.calls) == 2 and len(sender.sent) == 1
    assert await claim_rows(scenario) == [closed]


async def test_three_unavailable_rechecks_park_and_discard_the_staged_debrief(scenario):
    await seed_due(scenario)
    runner = ScriptedRunner((_SEND,), (_SEND,), (_SEND,))
    worker, sender, debrief = setup(scenario, runner)
    for attempt, state in enumerate(("released", "released", "parked"), 1):
        await _unavailable_poll(scenario, worker)
        [claim] = await claim_rows(scenario)
        assert (claim["state"], claim["attempts"]) == (state, attempt)
    await worker.poll()

    assert len(runner.calls) == 3 and sender.sent == []
    assert await claim_rows(scenario) == [claim]
    async with scenario.engine.connect() as conn:
        obligations = await conn.scalars(sa.select(DEBRIEF_OBLIGATION.c.pass_id))
        assert obligations.all() == []
    assert debrief.client.prompts == []
    assert debrief.metrics.get("nora_proactive_stimulus_parked_total") == 1


async def test_restart_recovery_releases_an_incomplete_recheck_without_counting_it(scenario):
    await seed_due(scenario)
    claims = SqlProactiveClaims(scenario.engine)
    [due] = await claims.due_intentions(NOW)
    claim = await claims.claim(due, NOW)
    assert claim is not None
    _, store = wiring(scenario)
    context = replace(CONTEXT, pass_id=claim.pass_id, occasion="proactive")
    authority = ActAuthority(UNIT, intention=due.intention, room=ROOM, pass_context=context)
    await ActEnvelope(store).record_silence(
        authority, reason="recheck_unavailable", recheck_reason="unresolved_destination",
    )
    [output] = (await _rows(scenario.engine, NORA_PASS_OUTPUT)).values()
    assert (output["pass"], output["recheck_reason"]) == (claim.pass_id, "unresolved_destination")

    expiry = timedelta(seconds=4)
    await claims.recover(NOW + expiry, expiry)

    [recovered] = await claim_rows(scenario)
    assert (recovered["state"], recovered["attempts"]) == ("released", 0)
    assert await claims.due_intentions(NOW + expiry) == (due,)


class UncertainSender(SinkSender):
    async def send(self, *args, **kwargs):
        await super().send(*args, **kwargs)
        return SendResult(ok=False, uncertain=True)


async def test_an_uncertain_proactive_send_cannot_be_repeated(scenario):
    scheduled = await seed_due(scenario, done_when={"own_delivery": True})
    calls = ScriptedRunner((_SEND,), (_SEND,))
    worker, sender, _ = setup(scenario, calls, [], sender=UncertainSender())
    await worker.poll()
    [claim] = await claim_rows(scenario)
    assert claim["state"] == "closed"
    await worker.poll()
    assert await claim_rows(scenario) == [claim]
    await wire.condition_matcher(scenario.engine).run_once()
    async with scenario.engine.connect() as conn:
        verdicts = list(await conn.scalars(sa.select(NORA_ACT_OUTCOME.c.verdict)))
    assert verdicts == ["uncertain"]
    assert len(sender.sent) == len(calls.calls) == 1
    assert (await _rows(scenario.engine, INTENTION))[scheduled]["state"] == "open"


async def test_a_multi_room_open_still_delivers_to_the_second_room_after_the_first(scenario):
    source = "generated-room-request"
    await scenario.item(source)
    await intention(scenario, "shared-subject", origin_room=None)
    room_slots = slots()
    second_key = ("signal", OTHER_ROOM)
    room_slots.acquire(second_key)
    runner = ScriptedRunner((_SEND,), (_SEND,))
    now = await scenario.now() + timedelta(minutes=1)
    worker, sender, debrief = setup(scenario, runner, [], [], now=now, room_slots=room_slots)
    tool = _make_intention_commit(SimpleNamespace(
        intention_admission=debrief.admission, metrics=debrief.metrics,
    ))
    context = replace(CONTEXT, room=f"signal:{ROOM}", occasion="called")
    with act_pass(ActAuthority(UNIT, pass_context=context),
                  shown=frozenset({source, "shared-subject"})):
        answer = await tool([opening(
            delivery="choose_moment",
            trigger={"kind": "time", "at": DUE.isoformat(), "zone": "UTC"},
            rooms=[f"signal:{ROOM}", f"signal:{OTHER_ROOM}"],
            serves=["shared-subject"], citations=[source],
        )])
    [opened] = answer["operations"]
    assert opened["status"] == "admitted", opened
    first, second = opened["ids"]
    for intention_id in (first, second):
        await publish_due(scenario, intention_id)

    await worker.poll()
    await wire.condition_matcher(scenario.engine).run_once()
    assert (await _rows(scenario.engine, INTENTION))[first]["ending"] == "achieved"
    assert [row["graph_id"] for row in await claim_rows(scenario)] == [first]

    room_slots.release_and_dispatch(second_key)
    await worker.poll()
    await wire.condition_matcher(scenario.engine).run_once()
    assert (await _rows(scenario.engine, INTENTION))[second]["ending"] == "achieved"
    assert [target.conversation_id for target, _ in sender.sent] == [ROOM, OTHER_ROOM]


async def test_a_migrated_legacy_reminder_delivers_once_through_the_graph(scenario):
    from tests.infra.test_legacy_agenda_migration import legacy_reminder

    async with scenario.engine.begin() as conn:
        await legacy_reminder(conn)
    migration = wire.legacy_agenda_migration(scenario.engine, scenario.policy)
    assert (await migration.run(review_deadline=NOW + timedelta(days=7)))["reminders"] == 1
    runner = ScriptedRunner((_SEND,))
    worker, sender, _ = setup(scenario, runner, [])
    clock = wire.graph_clock(scenario.engine, lambda: NOW)

    await clock.run_once()
    await worker.poll()
    await clock.run_once()
    await worker.poll()

    assert sender.texts == ["Generated reminder"]
    [request] = runner.calls
    assert "Generated appointment" in request["tag_message"]
    [claim] = await claim_rows(scenario)
    assert claim["state"] == "closed"


async def test_the_graph_clock_does_not_publish_a_future_delivery(scenario):
    await intention(
        scenario, "future-reminder", origin_room=f"signal:{ROOM}", delivery_mode="choose_moment",
        trigger={"kind": "time", "at": DUE.isoformat(), "zone": "UTC"},
    )
    claims = SqlProactiveClaims(scenario.engine)
    await wire.graph_clock(scenario.engine, lambda: DUE - timedelta(seconds=1)).run_once()
    assert await claims.due_intentions(DUE) == ()
    await wire.graph_clock(scenario.engine, lambda: DUE).run_once()
    [due] = await claims.due_intentions(DUE)
    assert (due.intention, due.eligible_at) == ("future-reminder", DUE)
