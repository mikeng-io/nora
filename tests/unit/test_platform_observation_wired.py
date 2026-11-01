"""The platform-observation wired acceptance paths (design/platform-observations
04/05's completion): not the helpers — the REAL surfaces a turn runs.

Each test drives a real owner: `TranscriptInjection.assemble` for the
transcript/Standing reads, `build_tools`-built tools for `record_query`,
`poll_list`/`poll_read` and `load_transcript`, `build_grounder` under a REAL
`CognitionSessionRunner` + `WakeEvaluator` for session assembly and
consumption, and `observation_from_dict` for the wire contract the bridge
lanes refuse on. The observation Record is `FakeObservationStore`, bound to
its SQL twin by `tests/contract/test_observation_store_contract.py`.

Generated rooms, people and object refs; nothing here is a real room's row.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.prefill import build_prefill
from nora.agent.room import Room as AgentRoom
from nora.agent.tools import build_tools
from nora.app.cognition_grounding import build_grounder
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.app.wake import WakeEvaluator
from nora.domain.cognition_session import Room
from nora.domain.models import EditTarget, NormalizedMessage, PersonRef
from nora.domain.observations import (
    Coverage,
    Observation,
    PinPayload,
    PollPayload,
    SourceScope,
    object_handle,
)
from nora.domain.ports import EngageDecision, EngagementRequest
from nora.domain.stimuli import NewStimulus, WakePolicy
from nora.engagement.context import TranscriptInjection
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.bridge.rehydrate import (
    observation_from_dict,
    observation_to_dict,
)
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.observations import FakeObservationStore
from nora.testing.stimuli import InMemoryStimulusStore
from tests.fakes import SinkSender

HK = ZoneInfo("Asia/Hong_Kong")
NOW_MS = int(dt.datetime(2026, 9, 30, 15, 30, tzinfo=HK).timestamp() * 1000)
NOW = dt.datetime.fromtimestamp(NOW_MS / 1000, HK)
MIN = 60_000
CID = "room-a"
SCOPE = SourceScope(platform="signal", account_key="acct", conversation_id=CID)

PERSONS = Persons(
    by_person={"ada": PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")},
    index={("signal", "+852111"): "ada"},
)


def _obs(*, kind: str, ref: str, op: str, payload: Any, ago: float,
         actor: str = "", actor_status: str = "not_supplied",
         position: int | None = None,
         coverage: Coverage | None = None) -> Observation:
    return Observation(
        object_kind=kind, scope=SCOPE, object_ref=ref, operation=op,
        payload=payload, actor_id=actor, actor_status=actor_status,
        occurred_ms=NOW_MS - int(ago * MIN), received_ms=NOW_MS - int(ago * MIN) + 5,
        time_quality="upstream", source_position=position,
        revision_scope="rev" if position is not None else "",
        coverage=coverage or Coverage(),
    )


async def _seed(store: FakeObservationStore) -> dict[str, int]:
    ids: dict[str, int] = {}

    async def put(name: str, obs: Observation) -> None:
        rid = await store.append(
            observation=obs, event_id=f"evt-{name}", source_digest=f"d-{name}")
        assert rid is not None
        ids[name] = rid

    await put("create", _obs(
        kind="poll", ref="p1", op="create",
        payload=PollPayload(question="Dinner Friday?",
                            options=(("r", "ramen"), ("s", "sushi"))),
        ago=120, actor="+852111", actor_status="known", position=1))
    await put("vote", _obs(
        kind="poll", ref="p1", op="add",
        payload=PollPayload(voter_ref="v1", option_ref="ramen"),
        ago=60, position=2))
    await put("close", _obs(
        kind="poll", ref="p1", op="revise",
        payload=PollPayload(state="closed", voter_count=1),
        ago=30, position=3))
    await put("pin", _obs(
        kind="pin", ref="pins", op="add",
        payload=PinPayload(message=EditTarget(message_id="m-1")),
        ago=100, actor="+852111", actor_status="known", position=4))
    return ids


def _assembler(audit: FakeRecordStore, observations: Any) -> TranscriptInjection:
    return TranscriptInjection(
        audit=audit, memory=FakeMemoryStore(), persons=PERSONS,
        context_window=40, metrics=Metrics(), observations=observations)


def _request() -> EngagementRequest:
    msg = NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="+852111", sender_name="Ada", timestamp=NOW_MS,
        body="nora.000 hi", raw={})
    return EngagementRequest(
        message=msg, decision=EngageDecision(engage=True, reason="trigger_text"),
        tag_id="tag-1")


async def _say(audit: FakeRecordStore, n: int) -> None:
    for i in range(n):
        await audit.append(message=NormalizedMessage(
            platform="signal", conversation_id=CID, conversation_type="group",
            sender_id="+852111", sender_name="Ada",
            timestamp=NOW_MS - (n - i) * MIN, body=f"message {i}", raw={}))


def _prompt(ctx: Any) -> str:
    return "\n".join(
        m["content"] for m in build_prefill(
            ctx, display_name="Nora", context_window=40, now=NOW))


class _NoRecord:
    async def query(self, q: Any) -> tuple[tuple, bool, frozenset[int]]:
        return (), False, frozenset()


def _tools(audit: FakeRecordStore, store: Any, cid: str = CID) -> dict[str, Any]:
    tools = build_tools(
        store=audit, conversation_id=cid, conversation_type="group",
        persons=PERSONS, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(),
        room=AgentRoom(platform="signal", conversation_id=cid,
                       roster_source=None, persons=PERSONS),
        transit_source=TransitClient(
            kmb_base_url="http://127.0.0.1:1",
            mtr_schedule_url="http://127.0.0.1:1/s",
            mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5),
        clock=lambda: NOW, record=_NoRecord(),
        lineage=LineagePolicy(rooms=(), mode="off"),
        observations=store)
    return {t.tool_name: t for t in tools}


async def _call(tools: dict[str, Any], name: str, **args: Any) -> dict:
    events = [e async for e in tools[name].stream(
        {"toolUseId": "t", "name": name, "input": args}, {"agent": None})]
    return json.loads(events[-1].tool_result["content"][0]["text"])


async def test_old_poll_pushed_beyond_40_messages_still_surfaces():
    """The acceptance case: a poll created 41+ messages ago leaves the
    message window, but its typed source event and its folded head still
    reach the turn — bounded evidence, not a permanent pin."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await _say(audit, 41)

    ctx = await _assembler(audit, store).assemble(request=_request())

    assert len(ctx.transcript) >= 40
    kinds = [e.kind for e in ctx.source_events]
    assert "poll" in kinds and "pin" in kinds
    create = next(e for e in ctx.source_events
                  if e.kind == "poll" and e.operation == "create")
    assert create.at_ms < ctx.transcript[0].ts  # older than the window
    assert create.actor == "Ada"
    heads = {o.kind for o in ctx.source_objects}
    assert "poll" in heads and "pin" in heads

    prompt = _prompt(ctx)
    assert "<source_event" in prompt and 'kind="poll"' in prompt
    assert "<source_object" in prompt and "closed" in prompt
    assert "p1" not in prompt
    assert "room-a" not in prompt and "acct" not in prompt


async def test_vote_and_close_with_no_new_human_message_still_render():
    """A later vote/close arrives with NO new message: the transcript's
    `<m>` set is unchanged and the events still render, with their source
    revision visible."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await _say(audit, 3)

    ctx = await _assembler(audit, store).assemble(request=_request())

    assert len(ctx.transcript) == 3
    ops = [(e.kind, e.operation) for e in ctx.source_events]
    assert ("poll", "add") in ops and ("poll", "revise") in ops
    close = next(e for e in ctx.source_events
                 if e.operation == "revise" and e.kind == "poll")
    assert close.revision == "rev:3"


async def test_a_failed_source_read_is_unavailable_not_empty():
    """R-27 for the transcript read: a wired store that raises renders the
    failure marker, never a clean empty block that reads as «nothing»."""

    class _Dead(FakeObservationStore):
        async def recent(self, **kw: Any) -> Any:
            raise RuntimeError("read blew up")

        async def heads(self, **kw: Any) -> Any:
            raise RuntimeError("read blew up")

    audit = FakeRecordStore()
    await _say(audit, 2)
    ctx = await _assembler(audit, _Dead()).assemble(request=_request())

    assert ctx.source_reads_failed is True
    assert "<source_reads unavailable/>" in _prompt(ctx)


async def test_record_query_and_the_task_reads_share_the_one_projection():
    """The REAL tools through `build_tools`: discovery, exact handle detail
    (a handle older than any page resolves), history, as-of, kind-from-
    handle routing and the three task reads — one projection throughout."""
    store = FakeObservationStore()
    ids = await _seed(store)
    for i in range(25):
        await store.append(
            observation=_obs(kind="pin", ref=f"pins-{i}", op="add",
                             payload=PinPayload(
                                 message=EditTarget(message_id=f"m-{i}")),
                             ago=10 - i * 0.1),
            event_id=f"flood-{i}", source_digest=f"flood-{i}")
    tools = _tools(FakeRecordStore(), store)

    listed = await _call(tools, "poll_list")
    assert listed["status"] == "ok", listed
    (poll,) = listed["polls"]
    assert poll["question"] == "Dinner Friday?" and poll["status"] == "closed"
    handle = poll["object"]
    assert handle.startswith("poll:") and "p1" not in handle

    detail = await _call(tools, "poll_read", object=handle)
    assert detail["status"] == "ok", detail
    assert detail["poll"]["question"] == "Dinner Friday?"
    assert "ramen" in json.dumps(detail) and "p1" not in json.dumps(detail)

    # The handle resolves EXACTLY — the 25 newer pins never hide it.
    rq = await _call(tools, "record_query", kind="poll", object=handle)
    assert rq["status"] == "ok" and rq["detail"]["question"] == "Dinner Friday?"

    hist = await _call(tools, "record_query", kind="poll", object=handle,
                       history=True)
    assert hist["status"] == "ok" and len(hist["events"]) == 3
    as_of = await _call(tools, "record_query", kind="poll", object=handle,
                        at_id=ids["vote"])
    assert as_of["status"] == "ok" and "closed" not in as_of["state"]

    # A pin's handle with kind omitted routes by its own prefix, never to poll.
    pin_q = await _call(tools, "record_query",
                        object=object_handle("pin", "pins-0",
                                             account_key="acct"))
    assert pin_q["status"] == "ok" and pin_q["detail"]["pin_count"] == 1

    # Scope isolation: the same tools bound to another room see nothing.
    other = _tools(FakeRecordStore(), store, cid="room-b")
    assert (await _call(other, "poll_list"))["status"] == "nothing_found"


async def test_load_transcript_interleaves_the_same_source_events():
    """Tool parity: `load_transcript` hands back what the prompt renders —
    the same typed events at their own time, with the same handles."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    # Messages spanning the events' whole window, so the window's source
    # read (bounded by the oldest shown row) covers all four.
    for i, ago in enumerate((125, 65, 5)):
        await audit.append(message=NormalizedMessage(
            platform="signal", conversation_id=CID, conversation_type="group",
            sender_id="+852111", sender_name="Ada",
            timestamp=NOW_MS - ago * MIN, body=f"message {i}", raw={}))
    tools = _tools(audit, store)

    out = await _call(tools, "load_transcript", limit=40)

    src = [m["source_event"] for m in out["messages"] if "source_event" in m]
    assert any(e["kind"] == "poll" for e in src)
    assert src[0]["object"].startswith("poll:")
    assert "p1" not in json.dumps(out) and "+852" not in json.dumps(out)


class _CaptureAgent:
    """The scripted model: records its prompt, closes with `end_turn`."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def run(self, *, system_prompt: str, prompt: str, tools: list[Any],
                  budget: AdaptorBudget,
                  trace_attributes: dict[str, str]) -> CognitionRunResult:
        self.calls += 1
        self.prompts.append(prompt)
        return CognitionRunResult(stop_reason="end_turn")


async def _ret(value: Any) -> Any:
    return value


async def test_the_session_runner_assembles_and_consumes_an_observation_stimulus():
    """The REAL `CognitionSessionRunner` + `WakeEvaluator`: an observation
    stimulus wakes a session, the REAL grounder grounds it, the model's
    prompt carries the event — and the stimulus is consumed exactly once."""
    obs_store = FakeObservationStore()
    ids = await _seed(obs_store)

    ledger = InMemoryCognitionStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    agent = _CaptureAgent()

    async def no_notes(unit: str) -> Any:
        return ()

    async def no_events(unit: str, platform: str, cid: str) -> Any:
        return ()

    class _NoRecords:
        async def by_refs(self, refs: Any, rooms: Any) -> Any:
            return ()

        async def own_sends(self, rooms: Any, *, start_ms: int,
                            end_ms: int, limit: int) -> Any:
            return ()

    grounder = build_grounder(
        records=_NoRecords(), persons=PERSONS, media_join=None,
        note_index=no_notes, events_in=no_events,
        lineage=LineagePolicy(rooms=(), mode="off"),
        unit_rooms=lambda unit: {("signal", CID)},
        room_label=lambda platform, cid: "Room A",
        observations=obs_store)

    policy = WakePolicy(
        quiet_seconds=0, cue_quiet_seconds=0, batch_max=100,
        max_age_seconds=3600, cooldown_seconds=0, max_concurrent_sessions=2,
        session_seconds=60, session_timeout_seconds=900)
    clock_ms = [NOW_MS]

    runner = CognitionSessionRunner(
        store=ledger, stimulus_store=stimuli, agent_runner=agent,
        home_rooms={"u1": Room("signal", CID)},
        read_tools_for=lambda unit, rooms: [],
        skill_text_for=lambda kinds: "", grounder=grounder,
        doctrine_text="DOCTRINE", doctrine_sha256="sha",
        open_note_titles=lambda *a, **k: _ret(()),
        open_events=lambda *a, **k: _ret(()),
        prior_standing=lambda *a, **k: _ret(None),
        budget=AdaptorBudget(turns=4, total_tokens=1000, output_tokens=100,
                             timeout_seconds=30.0),
        cooldown_seconds=0.0, units=("u1",),
        session_timeout_seconds=policy.session_timeout_seconds,
        max_items=12, max_strikes=3, metrics=Metrics(),
        clock=lambda: clock_ms[0] / 1000)
    ev = WakeEvaluator(
        store=stimuli, units=lambda: ("u1",), policy=policy, metrics=Metrics(),
        tz=HK, clock=lambda: clock_ms[0] / 1000, settle_seconds=0,
        on_wake=runner.wake_hook)

    sid = await stimuli.append(stimulus=NewStimulus(
        unit="u1", kind="observation", item="poll",
        ref=f"platform_observation:{ids['close']}", at_ms=NOW_MS,
        platform="signal", conversation_id=CID), recorded_at_ms=NOW_MS)
    assert sid is not None

    await ev.tick()
    await ev.drain()

    assert agent.calls == 1
    (prompt,) = agent.prompts
    assert "<observation" in prompt and 'kind="poll"' in prompt
    assert 'op="revise"' in prompt and 'rev="rev:3"' in prompt
    assert "closed" in prompt and "p1" not in prompt

    # Consumed exactly once: a second tick with nothing new wakes nothing.
    clock_ms[0] += 120_000
    await ev.tick()
    await ev.drain()
    assert agent.calls == 1


async def test_the_wire_contract_refuses_a_wrong_scope_and_a_malformed_row():
    """The durable-ingest gate the bridge lanes honour: an observation
    whose wire scope is not the envelope's is refused, and a payload with
    no contract is refused — retained failures, never silent success."""
    good = _obs(kind="poll", ref="p9", op="create",
                payload=PollPayload(question="Q?"), ago=1, position=1)
    wrong = observation_to_dict(dataclasses.replace(
        good, scope=SourceScope(platform="signal", account_key="acct",
                                conversation_id="other-room")))
    with pytest.raises(ValueError):
        observation_from_dict(wrong, expect_scope=SCOPE)
    with pytest.raises(ValueError):
        observation_from_dict({})  # no contract — refused at the boundary
