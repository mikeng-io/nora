"""Run the wake with a scripted model, scoped Postgres rows and its real tools."""

import json
from collections.abc import AsyncIterable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from nora.access import IdentityAccess
from nora.agent.factory import RetryPolicy
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.app.cognition_agent_runner import DreamerAgentRunner
from nora.app.cognition_session import AdaptorBudget
from nora.app.wake_floor import WakeRoomFloor
from nora.app.wake_pass import WakePassRunner
from nora.app.wake_rooms import WakeRoomSlots
from nora.domain.graph_stimuli import GraphStimulus
from nora.domain.models import NormalizedMessage
from nora.engagement.self_presence import PresenceReader
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.infra.sql.record_query import SqlRecordQuery
from nora.instructions.dreamer import DreamerInstructions
from nora.memory import ActRefused, WakeClaim, WakeClosed, WakeFailed, declare_act, wire
from nora.memory.app.act_scope import active_act_authority, pass_shown
from nora.obs.metrics import Metrics
from nora.testing.fakes import FakeRosterSource
from nora.testing.scripted_agent import ScriptedTurn
from tests.fakes import dreamer_runtime_for
from tests.infra.test_graph_debrief import opening
from tests.infra.test_pass_anchors import NOW, pointers
from tests.infra.test_retrieval_scope import AT, PERSON, ROOM, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_room_busy import KEY, SUBJECT, service, slots
from tests.infra.test_situation_staleness import intention
from tests.infra.test_wake_claims import GROUP, ORIGIN
from tests.unit.test_dream_memory_budget import _ScriptedModel


class WakeModel(_ScriptedModel):
    def __init__(self, turns: list[ScriptedTurn], observe: Callable[[], None] | None = None):
        calls = [call for turn in turns for call in turn.tool_calls]
        super().__init__([name for name, _ in calls] + [None],
                         inputs=[json.dumps(arguments) for _, arguments in calls])
        self.observe = observe
        self.tools: set[str] = set()
        self.system = ""

    def create(self):
        return self

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        if self.observe is not None:
            self.observe()
        self.tools = {spec["name"] for spec in args[1] or ()}
        self.system = args[2]
        async for event in super().stream(*args, **kwargs):
            yield event

    def prompt(self) -> str:
        return self.seen[0][0]["content"][0]["text"]


def runner(
    scenario, model, *, turns=4, offered=wire.WAKE_TOOLS, admission=None, occasion="wake",
    rooms=None,
):
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    lineage = LineagePolicy.from_policy(scenario.policy, access=access)
    metrics = Metrics()
    roster = FakeRosterSource(groups={ROOM: (frozenset({PERSON}),)})

    def room_for(platform, id_):
        return Room(platform=platform, conversation_id=id_, roster_source=roster, access=access)

    revisions = RuntimeRevisions({}, scenario.policy, scenario.persons)
    records = SqlRecordStore(scenario.engine)
    floor = WakeRoomFloor(
        scenario.policy, access, room_for, SqlRecordQuery(scenario.engine), records,
        PresenceReader(policy=scenario.policy, adapters={}, rosters={"signal": roster}), None,
        lambda text: "fixture-secret" if "fixture-secret" in text else None,
        display_name="Nora", record_chars=4000,
    )
    instructions = DreamerInstructions(Path("nora/dreamer"), metrics=metrics)
    surface = wire.WakeToolSurface(
        admission or service(scenario, slots(scenario)),
        wire.graph_read_tools(scenario.engine, access, lineage, None, None),
        room_for, metrics, content_checks=False, offered=offered,
        allowed_operations=wire.WAKE_OPERATIONS,
    )
    runtime = dreamer_runtime_for(
        models=model, metrics=metrics,
        retry=RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
    )
    return WakePassRunner(
        rooms=rooms if rooms is not None else floor,
        assembler=wire.pass_assembler(scenario.engine, access, lineage, None, None),
        surface=surface, agent=DreamerAgentRunner(
            runtime=runtime, metrics=metrics, bound_tool_arguments=True,
        ),
        instructions=instructions, budget=AdaptorBudget(turns, 5000, 1000, 30.0),
        model="scripted", revisions=revisions.current, occasion=occasion,
    )


def claim(*stimuli: GraphStimulus) -> WakeClaim:
    return WakeClaim(GROUP, ORIGIN, stimuli, NOW, "settled")


def observation(id_: str) -> GraphStimulus:
    return GraphStimulus(UNIT, "new_item", id_, AT, "messages", ROOM, PERSON)


async def test_claimed_items_and_touched_open_intentions_are_in_the_wake_agenda(scenario):
    await pointers(scenario)
    await scenario.item("claimed-row", body="Generated wake input")
    await intention(scenario, "touched-open")
    await write_edge(scenario, "touched-open", "claimed-row", "cites")
    shown = set()

    def observe():
        shown.update(pass_shown() or ())

    model = WakeModel([], observe)
    assert await runner(scenario, model).run(claim(observation("claimed-row"))) == WakeClosed()
    agenda = model.prompt().split("<agenda", 1)[1].split("</agenda>", 1)[0]
    assert "claimed-row" in agenda and "touched-open" in agenda
    assert {"claimed-row", "touched-open"} <= shown


async def test_wake_reports_a_reached_turn_bound(scenario):
    await scenario.item("claimed-row")
    model = WakeModel([ScriptedTurn(tool_calls=[("memory_read", {"id": "claimed-row"})])])
    assert await runner(scenario, model, turns=1).run(
        claim(observation("claimed-row")),
    ) == WakeFailed("bound")
    assert model.turn == 1


async def test_wake_offers_only_graph_tools_and_its_one_objective(scenario):
    await scenario.item("claimed-row")
    model = WakeModel([])
    assert await runner(scenario, model).run(claim(observation("claimed-row"))) == WakeClosed()
    assert model.tools == {"graph_propose", "intention_commit", "memory_search", "memory_read"}
    instruction = DreamerInstructions(Path("nora/dreamer"), metrics=Metrics()).for_objective("wake")
    assert model.system == instruction.text
    assert set(instruction.versions) == {"wake", "dreamer"}


async def test_wake_guidance_follows_the_offered_surface(scenario):
    await scenario.item("claimed-row")
    model = WakeModel([])
    assert await runner(scenario, model, offered={"memory_read", "intention_commit"}).run(
        claim(observation("claimed-row")),
    ) == WakeClosed()
    catalogue = model.prompt().split("<tool_catalogue", 1)[1].split("</tool_catalogue>", 1)[0]
    assert "memory_read" in catalogue and "intention_commit" in catalogue
    assert "graph_propose" not in catalogue and "memory_search" not in catalogue
    assert "your reply promises" not in catalogue


async def test_wake_act_pass_refuses_an_act(scenario):
    await scenario.item("claimed-row")

    def observe():
        authority = active_act_authority()
        assert authority is not None
        with pytest.raises(ActRefused, match="non_engagement_pass"):
            declare_act("platform.reply", ORIGIN, event="fixture", authority=authority)

    model = WakeModel([], observe)
    assert await runner(scenario, model).run(claim(observation("claimed-row"))) == WakeClosed()


async def test_claimed_verdict_and_registered_correction_are_lesson_evidence(scenario):
    self_id = "fixture-wake-bot"
    registration = SqlItemRegistration(
        RegistrationScope(scenario.policy, scenario.persons),
        RuntimeRevisions({}, scenario.policy, scenario.persons),
    )
    records = SqlRecordStore(
        scenario.engine, registration=registration,
        self_ids=lambda platform: frozenset({self_id}) if platform == "signal" else frozenset(),
    )
    for index, (sender, own, body) in enumerate((
        (PERSON, False, "Please use the original title next time."),
        (self_id, True, "Please use my own title next time."),
        (PERSON, False, "I prefer the third title."),
        (PERSON, False, "The generated title was ready."),
    )):
        sent_at = AT + timedelta(seconds=index)
        msg = NormalizedMessage(
            "signal", ROOM, "group", sender, None, int(sent_at.timestamp() * 1000), body, {},
            mentions=frozenset({self_id}) if index < 2 else frozenset(),
        )
        await records.append(message=msg, is_nora=own, person_id=None if own else PERSON)
    await scenario.item(
        "previous-room-unit", unit="other-unit", body="A different unit's transcript.",
        at=AT + timedelta(seconds=2),
    )
    deadline = AT + timedelta(minutes=1)
    await intention(scenario, "claimed-deadline", origin_room=ORIGIN, deadline=deadline)
    await intention(scenario, "unclaimed-deadline", origin_room=ORIGIN,
                    deadline=deadline + timedelta(seconds=1))
    room_slots = WakeRoomSlots(scenario.policy, slots(scenario))
    verifier = wire.intention_verifier(
        scenario.engine, lambda: deadline, max_poll_gap_seconds=900, room_slots=room_slots,
    )
    await verifier.run_once()
    stimuli = await SqlGraphStimuli(scenario.engine).read(UNIT)
    verdict, = (s for s in stimuli if s.kind == "verdict")
    await wire.intention_verifier(
        scenario.engine, lambda: deadline + timedelta(seconds=1), max_poll_gap_seconds=900,
        room_slots=room_slots,
    ).run_once()
    assert sum(s.kind == "verdict" for s in await SqlGraphStimuli(scenario.engine).read(UNIT)) == 2
    assert any(s.cues == ("self_reference",) for s in stimuli)
    assert {s.kind for s in stimuli} == {"strong_cue", "new_item", "verdict"}
    model = WakeModel([])
    latest = await scenario.now()
    assert await runner(scenario, model).run(
        WakeClaim(GROUP, ORIGIN, stimuli, latest, "settled"),
    ) == WakeClosed()
    evidence = model.prompt().split("<lesson_evidence", 1)[1].split("</lesson_evidence>")[0]
    assert "Intention verdicts and possible corrections" in evidence
    assert verdict.graph_id in evidence and 'kind="verdict"' in evidence
    assert '"intention_id": "claimed-deadline"' in evidence
    assert '"ending": "unknown"' in evidence
    assert f'"ended_at": "{deadline.isoformat()}"' in evidence
    assert f"owed_to={PERSON}" in evidence
    assert "unclaimed-deadline" not in evidence
    assert "Please use the original title next time." in evidence
    assert "Please use my own title next time." not in evidence
    assert "I prefer the third title." not in evidence
    assert "The generated title was ready." not in evidence
    transcript = model.prompt().split("<transcript", 1)[1].split("</transcript>")[0]
    assert "The generated title was ready." in transcript
    assert "A different unit" not in transcript


async def test_wake_with_a_room_busy_write_fails_with_the_refusal(scenario):
    await intention(scenario, SUBJECT, origin_room=ORIGIN)
    room_slots = slots(scenario)
    room_slots.acquire(KEY)
    payload = {"operations": [opening(op="revise", base=SUBJECT)]}
    model = WakeModel([ScriptedTurn(tool_calls=[("intention_commit", payload)])])
    row = GraphStimulus(UNIT, "review_time", SUBJECT, AT, "clock", ORIGIN)
    assert await runner(scenario, model, admission=service(scenario, room_slots)).run(
        claim(row),
    ) == WakeFailed("room_busy")
    assert model.turn == 2
    assert "room_busy" in json.dumps(model.seen[-1])
