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
from nora.domain.models import EditTarget, NormalizedMessage, PersonRef, Touch
from nora.domain.observations import (
    ArtifactPayload,
    Coverage,
    MembershipPayload,
    Observation,
    PinPayload,
    PollPayload,
    ReactionPayload,
    SourceScope,
    object_handle,
    revision_label,
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
         position: int | None = None, rev_scope: str = "rev",
         room: str = CID, platform: str = "signal",
         target: Any = None, subject: str = "",
         unknown: bool = False,
         coverage: Coverage | None = None) -> Observation:
    if unknown:
        occurred_ms, quality = None, "unknown"
    else:
        occurred_ms, quality = NOW_MS - int(ago * MIN), "upstream"
    return Observation(
        object_kind=kind,
        scope=SourceScope(platform=platform, account_key="acct",
                          conversation_id=room),
        object_ref=ref, operation=op,
        payload=payload, target=target, subject_ref=subject,
        actor_id=actor, actor_status=actor_status,
        occurred_ms=occurred_ms, received_ms=NOW_MS - int(ago * MIN) + 5,
        time_quality=quality, source_position=position,
        revision_scope=rev_scope if position is not None else "",
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
        block["text"]
        for message in build_prefill(
            ctx, display_name="Nora", context_window=40, now=NOW)
        for block in message["content"] if "text" in block)


class _NoRecord:
    async def query(self, q: Any) -> tuple[tuple, bool, frozenset[int]]:
        return (), False, frozenset()


def _tools(audit: FakeRecordStore, store: Any, cid: str = CID,
           platform: str = "signal") -> dict[str, Any]:
    tools = build_tools(
        store=audit, conversation_id=cid, conversation_type="group",
        persons=PERSONS, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(),
        room=AgentRoom(platform=platform, conversation_id=cid,
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
    # The revision label is the shared opaque projection — the raw
    # `revision_scope` value never renders, the position does.
    assert close.revision == revision_label(3, "rev")
    assert "rev" not in close.revision.split(":")[0]


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
    for name, kind, payload, subject in (
        ("share", "artifact", ArtifactPayload(artifact_kind="post", custody="declared"), ""),
        ("join", "membership", MembershipPayload(state="member"), "new-member"),
    ):
        ids[name] = await obs_store.append(
            observation=_obs(kind=kind, ref=name, op="create" if kind == "artifact" else "add",
                             payload=payload, subject=subject, ago=1),
            event_id=f"session-{name}", source_digest=f"session-{name}")
        assert ids[name] is not None

    ids["delivery"] = await obs_store.append(
        observation=_obs(kind="artifact", ref="delivery", op="create", ago=1,
                         payload=ArtifactPayload(artifact_kind="post", custody="declared")),
        event_id="delivery", source_digest="delivery",
        target_message_id=8, delivered_message_id=9)

    ledger = InMemoryCognitionStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    agent = _CaptureAgent()

    async def no_notes(unit: str) -> Any:
        return ()

    async def no_events(unit: str, platform: str, cid: str) -> Any:
        return ()

    class _NoRecords:
        async def by_refs(self, refs: Any, rooms: Any) -> Any:
            from nora.domain.models import TranscriptRow
            from nora.domain.record_query import RecordHit, RecordRefHit

            if ("messages", 9) not in refs or ("signal", CID) not in rooms:
                return ()
            row = TranscriptRow(9, "author", "Ada", NOW_MS,
                                "This is the co-delivered utterance", False,
                                "observed", None)
            return (RecordRefHit("messages", 9, RecordHit("signal", CID, row)),)

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

    for name, kind in (("close", "poll"), ("pin", "pin"),
                       ("share", "artifact"), ("join", "membership"),
                       ("delivery", "artifact")):
        sid = await stimuli.append(stimulus=NewStimulus(
            unit="u1", kind="observation", item=kind,
            ref=f"platform_observation:{ids[name]}", at_ms=NOW_MS,
            platform="signal", conversation_id=CID), recorded_at_ms=NOW_MS)
        assert sid is not None

    await ev.tick()
    await ev.drain()

    assert agent.calls == 1
    (prompt,) = agent.prompts
    assert "<observation" in prompt and 'kind="poll"' in prompt
    for kind in ("pin", "artifact", "membership"):
        assert f'kind="{kind}"' in prompt
    assert 'op="revise"' in prompt
    assert f'rev="{revision_label(3, "rev")}"' in prompt
    assert "closed" in prompt and "p1" not in prompt
    assert "This is the co-delivered utterance" in prompt

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
                payload=PollPayload(question="Q?", options=(("a", "A"),)),
                ago=1, position=1)
    wrong = observation_to_dict(dataclasses.replace(
        good, scope=SourceScope(platform="signal", account_key="acct",
                                conversation_id="other-room")))
    with pytest.raises(ValueError):
        observation_from_dict(wrong, expect_scope=SCOPE)
    with pytest.raises(ValueError):
        observation_from_dict({})  # no contract — refused at the boundary


async def test_high_cardinality_reads_mark_their_overflow():
    """Bounded reads never pretend to be the whole: a 50-event window read
    returns `source_more` and 16 folded objects return `source_objects_more`
    — exact counts below the fetch page, lower bounds above it."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _say(audit, 3)
    for i in range(50):  # one pin SET, fifty pin events
        await store.append(
            observation=_obs(
                kind="pin", ref="pins", op="add",
                payload=PinPayload(message=EditTarget(message_id=f"m-{i}")),
                ago=200 - i, actor="+852111", actor_status="known"),
            event_id=f"cap-{i}", source_digest=f"cap-{i}")
    for i in range(15):  # fifteen artifact objects
        await store.append(
            observation=_obs(
                kind="artifact", ref=f"art-{i}", op="create",
                payload=ArtifactPayload(artifact_kind="post"),
                ago=10 + i * 0.01),
            event_id=f"art-{i}", source_digest=f"art-{i}")

    ctx = await _assembler(audit, store).assemble(request=_request())

    assert len(ctx.source_events) == 40 and ctx.source_events_more == 1
    assert len(ctx.source_objects) == 12 and ctx.source_objects_more == 4
    prompt = _prompt(ctx)
    assert 'source_more="1"' in prompt
    assert '<source_objects_more count="4"/>' in prompt


async def test_revision_and_head_ref_agree_across_prompt_and_tools():
    """One revision label everywhere: the prompt event, the Standing
    source_object and `record_query`'s detail carry the same opaque
    `digest:position`, and the folded head's ref is the newest applied
    event's own `platform_observation:` id — event and current views
    deduplicate on it."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    ids = await _seed(store)
    # A raw revision scope that must NEVER render — digest only.
    for i in range(3):
        await store.append(
            observation=_obs(
                kind="pin", ref="pins", op="add",
                payload=PinPayload(message=EditTarget(message_id=f"x-{i}")),
                ago=3 - i, position=10 + i, rev_scope="discord-sess-9f8c"),
            event_id=f"rv-{i}", source_digest=f"rv-{i}")
    tools = _tools(audit, store)
    ctx = await _assembler(audit, store).assemble(request=_request())

    close = next(e for e in ctx.source_events
                 if e.kind == "poll" and e.operation == "revise")
    pin_head = next(o for o in ctx.source_objects if o.kind == "pin")
    poll_head = next(o for o in ctx.source_objects if o.kind == "poll")
    assert pin_head.revision == revision_label(12, "discord-sess-9f8c")
    # The current view points at the event that produced it.
    assert poll_head.ref == f"platform_observation:{ids['close']}"
    assert close.ref == poll_head.ref

    listed = await _call(tools, "poll_list")
    handle = listed["polls"][0]["object"]
    detail = await _call(tools, "record_query", kind="poll", object=handle)
    assert detail["detail"]["revision"] == close.revision

    prompt = _prompt(ctx)
    assert f'rev="{close.revision}"' in prompt
    assert f'rev="{pin_head.revision}"' in prompt
    assert "discord-sess" not in prompt
    assert "sess-9f8c" not in json.dumps(detail)


async def test_unknown_source_time_never_dresses_up_as_upstream():
    """A bridge row with no source clock keeps `time="unknown"` and its
    receipt stamps, on every surface; nothing fabricates `at`/`observed`."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _say(audit, 2)
    await store.append(
        observation=_obs(
            kind="pin", ref="pins", op="add",
            payload=PinPayload(message=EditTarget(message_id="m-1")),
            ago=8, unknown=True),
        event_id="unk-1", source_digest="unk-1")
    tools = _tools(audit, store)
    ctx = await _assembler(audit, store).assemble(request=_request())

    (event,) = [e for e in ctx.source_events if e.kind == "pin"]
    assert event.time_quality == "unknown"
    assert event.at_ms == event.received_ms  # ordered by receipt, honestly
    (head,) = [o for o in ctx.source_objects if o.kind == "pin"]
    assert head.time_quality == "unknown" and head.received_ms

    prompt = _prompt(ctx)
    ev_line = next(ln for ln in prompt.splitlines()
                   if ln.startswith("<source_event"))
    assert 'time="unknown"' in ev_line and "received=" in ev_line
    assert "at=" not in ev_line
    obj_line = next(ln for ln in prompt.splitlines()
                    if ln.startswith("<source_object"))
    assert 'time="unknown"' in obj_line and "received=" in obj_line
    assert "observed=" not in obj_line

    found = await _call(tools, "record_query", kind="pin")
    assert found["status"] == "ok" and found["objects"][0]["time"] == "unknown"
    detail = await _call(tools, "record_query", kind="pin",
                         object=found["objects"][0]["object"])
    d = detail["detail"]
    assert d["time_quality"] == "unknown"
    assert "observed_ms" not in d and d["received_ms"]
    hist = await _call(tools, "record_query", kind="pin",
                       object=found["objects"][0]["object"], history=True)
    (row,) = hist["events"]
    assert row["time"] == "unknown" and "at_ms" not in row
    assert row["received_ms"]


async def test_clockless_update_does_not_inherit_the_previous_source_clock():
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await store.append(
        observation=_obs(
            kind="pin", ref="pins", op="remove",
            payload=PinPayload(message=EditTarget(message_id="m-1")),
            ago=1, unknown=True),
        event_id="clockless-remove", source_digest="clockless-remove")
    ctx = await _assembler(audit, store).assemble(request=_request())
    head = next(o for o in ctx.source_objects if o.kind == "pin")
    assert head.time_quality == "unknown" and head.at_ms == 0
    assert head.received_ms > 0 and head.ordering == "arrival"
    detail = await _call(_tools(audit, store), "record_query", object=head.handle)
    assert detail["status"] == "ok"
    assert "observed_ms" not in detail["detail"]
    assert detail["detail"]["time_quality"] == "unknown"
    assert detail["detail"]["received_ms"] == head.received_ms
    line = next(line for line in _prompt(ctx).splitlines()
                if line.startswith("<source_object") and 'kind="pin"' in line)
    assert 'time="unknown"' in line and "observed=" not in line


async def test_mixed_share_member_pin_poll_journey_all_surfaces():
    """The journey case: an artifact share, a member join and a pin land
    between the poll's vote and close. Every surface folds them into the
    right kinds; the session runner's agenda wake carries them typed."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await store.append(
        observation=_obs(
            kind="artifact", ref="private-artifact-origin", op="create",
            payload=ArtifactPayload(artifact_kind="post", custody="declared",
                                    metadata={"title": "a clip"}),
            ago=45, actor="+852111", actor_status="known", position=5),
        event_id="art-1", source_digest="art-1")
    await store.append(
        observation=_obs(
            kind="membership", ref="members", op="add",
            payload=MembershipPayload(state="member"),
            ago=40, actor="+852111", actor_status="known",
            subject="+999000", position=6,
            coverage=Coverage(covered="channel")),
        event_id="mem-1", source_digest="mem-1")
    await _say(audit, 5)
    tools = _tools(audit, store)

    state = await _call(tools, "room_state_read")
    assert state["status"] == "ok", state
    assert "memberships" in state and "pins" in state
    member_detail = state["memberships"][0]["detail"]
    assert member_detail["member_count"] == 1
    assert "+999000" not in json.dumps(state)

    ctx = await _assembler(audit, store).assemble(request=_request())
    seen = {(e.kind, e.operation) for e in ctx.source_events}
    assert {("artifact", "create"), ("membership", "add"),
            ("pin", "add"), ("poll", "revise")} <= seen
    heads = {o.kind for o in ctx.source_objects}
    assert {"artifact", "membership", "pin", "poll"} <= heads
    prompt = _prompt(ctx)
    assert 'kind="artifact"' in prompt and 'kind="membership"' in prompt
    assert "private-artifact-origin" not in prompt and "+999000" not in prompt
    # The join names nobody — the subject isn't on the roster.
    member_ev = next(e for e in ctx.source_events if e.kind == "membership")
    assert "+999000" not in member_ev.summary

    # The same room reads stay typed and scoped through the tools.
    found = await _call(tools, "record_query", kind="artifact")
    assert found["status"] == "ok" and len(found["objects"]) == 1
    out = await _call(tools, "record_query", kind="artifact",
                      object=found["objects"][0]["object"])
    assert out["status"] == "ok" and out["detail"]["artifact_kinds"] == ["post"]
    out = await _call(tools, "record_query", kind="membership")
    assert out["status"] == "ok" and "+999000" not in json.dumps(out)


async def test_named_and_anonymous_voters_stay_distinct_everywhere():
    """Named voters resolve; anonymous and unmapped voters count unnamed —
    never invented, never leaking their refs."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await store.append(
        observation=_obs(
            kind="poll", ref="p1", op="create",
            payload=PollPayload(question="Lunch?",
                                options=(("r", "ramen"), ("s", "sushi"))),
            ago=120, actor="+852111", actor_status="known", position=1),
        event_id="e1", source_digest="e1")
    await store.append(
        observation=_obs(
            kind="poll", ref="p1", op="add",
            payload=PollPayload(voter_ref="+852111", option_ref="ramen"),
            ago=60, actor="+852111", actor_status="known", position=2),
        event_id="e2", source_digest="e2")
    await store.append(
        observation=_obs(
            kind="poll", ref="p1", op="add",
            payload=PollPayload(voter_ref="anon-77", option_ref="sushi"),
            ago=55, actor_status="anonymous", position=3),
        event_id="e3", source_digest="e3")
    await store.append(
        observation=_obs(
            kind="poll", ref="p1", op="add",
            payload=PollPayload(voter_ref="v-9", option_ref="sushi"),
            ago=50, actor="999", actor_status="known", position=4),
        event_id="e4", source_digest="e4")
    tools = _tools(audit, store)

    listed = await _call(tools, "poll_list")
    detail = await _call(tools, "poll_read",
                         object=listed["polls"][0]["object"])
    d = detail["poll"]
    assert d["voters_named"] == ["Ada"] and d["voters_unnamed"] == 2
    dumped = json.dumps(detail)
    assert "anon-77" not in dumped and "v-9" not in dumped and '"999"' not in dumped

    ctx = await _assembler(audit, store).assemble(request=_request())
    prompt = _prompt(ctx)
    anon = next(e for e in ctx.source_events if e.actor_status == "anonymous")
    assert anon.actor is None and "anon-77" not in prompt


async def test_source_event_links_to_its_shown_transcript_row():
    """`target_message_id` finally renders: a pin of a shown message gains
    `on="<n>"` matching that row's `#n`; a target outside the window, and
    an unresolved target, carry no `on`. `resolve_targets` re-links the
    durable row and the next assemble shows the link."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    rid = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="+852111", sender_name="Ada",
        timestamp=NOW_MS - 10 * MIN, body="the pinned words", raw={}))
    await store.append(
        observation=_obs(
            kind="pin", ref="pins", op="add",
            payload=PinPayload(message=EditTarget(message_id="m-1")),
            ago=5, target=EditTarget(message_id="m-1")),
        event_id="pin-1", source_digest="pin-1")
    await store.append(
        observation=_obs(
            kind="pin", ref="pins", op="add",
            payload=PinPayload(message=EditTarget(message_id="m-2")),
            ago=4, target=EditTarget(message_id="m-2")),
        event_id="pin-2", source_digest="pin-2")

    ctx = await _assembler(audit, store).assemble(request=_request())
    prompt = _prompt(ctx)
    pin_events = [e for e in ctx.source_events if e.kind == "pin"]
    assert len(pin_events) == 2
    assert all(e.target_message_id is None for e in pin_events)
    transcript_part = prompt.split("<group_transcript")[1]
    assert all(' on="' not in line
               for line in transcript_part.splitlines()
               if line.startswith("<source_event"))

    # Late resolution: the durable row links, the next assemble renders it.
    linked = await store.resolve_targets(
        scope=SCOPE, target_key="id:m-1", message_id=rid)
    assert linked == 1
    ctx = await _assembler(audit, store).assemble(request=_request())
    prompt = _prompt(ctx)
    index_of = {row.id: i for i, row in enumerate(ctx.transcript, 1)}
    want = index_of[rid]
    pin_lines = [ln for ln in prompt.splitlines()
                 if ln.startswith("<source_event") and 'kind="pin"' in ln]
    # Both pins name the same object handle; exactly ONE carries `on=`,
    # and it points at the shown message's own `#n` index.
    with_on = [ln for ln in pin_lines if "on=" in ln]
    assert len(pin_lines) == 2 and len(with_on) == 1
    assert f'on="{want}"' in with_on[0]
    assert any(e.target_message_id is None for e in ctx.source_events
               if e.kind == "pin")

    tools = _tools(audit, store)
    out = await _call(tools, "load_transcript", limit=40)
    pins = [m["source_event"] for m in out["messages"]
            if m.get("source_event", {}).get("kind") == "pin"]
    assert sorted(p.get("on") or 0 for p in pins) == [0, want]


async def test_model_facing_surfaces_carry_no_platform_ids():
    """One sweep: raw refs, actor numbers, account keys, room ids, revision
    scopes and message ids appear on NONE of the model-facing surfaces."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await store.append(
        observation=_obs(
            kind="membership", ref="members", op="add",
            payload=MembershipPayload(state="member"),
            ago=40, subject="+999000",
            coverage=Coverage(covered="guild:123456789")),
        event_id="mg-1", source_digest="mg-1")
    tools = _tools(audit, store)
    ctx = await _assembler(audit, store).assemble(request=_request())

    surfaces = [_prompt(ctx)]
    for name, kw in (
        ("poll_list", {}), ("room_state_read", {}),
        ("record_query", {"kind": "poll"}),
        ("record_query", {"kind": "membership"}),
        ("load_transcript", {"limit": 40}),
    ):
        surfaces.append(json.dumps(await _call(tools, name, **kw)))
    blob = "\n".join(surfaces)
    for raw in ("p1", "+852111", "+999000", "123456789", "acct", "m-1"):
        assert raw not in blob, raw


async def test_unsupported_platform_kind_is_unavailable_never_empty():
    """The frozen coverage matrix: an Instagram poll read says
    `unavailable`, a kind the platform could serve says `nothing_found`
    — and the answer never widens authority."""
    store = FakeObservationStore()
    await store.append(
        observation=_obs(
            kind="pin", ref="pins", op="add",
            payload=PinPayload(message=EditTarget(message_id="m-1")),
            platform="instagram", ago=5),
        event_id="ig-1", source_digest="ig-1")
    ig_tools = _tools(FakeRecordStore(), store, platform="instagram")

    listed = await _call(ig_tools, "poll_list")
    assert listed["status"] == "unavailable"
    read = await _call(ig_tools, "poll_read", object="poll:0123456789ab")
    assert read["status"] == "unavailable"
    rq = await _call(ig_tools, "record_query", kind="poll")
    assert rq["status"] == "unavailable"
    # A kind the same source CAN carry reads normally — not unavailable.
    rq = await _call(ig_tools, "record_query", kind="pin")
    assert rq["status"] == "ok" and len(rq["objects"]) == 1
    # And a signal-bound read in the same room still says nothing_found,
    # not unavailable — the matrix marks Instagram's polls, not polls.
    sig_tools = _tools(FakeRecordStore(), store)
    rq = await _call(sig_tools, "record_query", kind="poll")
    assert rq["status"] == "nothing_found"
    listed = await _call(sig_tools, "poll_list")
    assert listed["status"] == "nothing_found"


@pytest.mark.parametrize("outside_window", [False, True])
async def test_old_target_reaction_union_reaches_standing_and_real_tools(outside_window):
    audit = FakeRecordStore()
    store = FakeObservationStore(records=audit)
    audit.observations = store
    mid = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="+852111", sender_name="Ada", timestamp=NOW_MS - 180 * MIN,
        body="an old target", raw={}, message_id="old-target"))

    async def react(sender: str, ago: int) -> None:
        await audit.append_reaction(
            message_id=mid, person_id=None,
            message=NormalizedMessage(
                platform="signal", conversation_id=CID, conversation_type="group",
                sender_id=sender, sender_name=None, timestamp=NOW_MS - ago * MIN,
                body="", raw={}, touch=Touch(
                    kind="reaction", target=EditTarget(message_id="old-target"),
                    emoji="👍")))

    await react("first-actor", 120)
    await store.append(
        observation=_obs(kind="reaction", ref="msg:old-target", op="clear",
                         payload=ReactionPayload(), ago=60,
                         target=EditTarget(message_id="old-target")),
        event_id="old-clear", source_digest="old-clear", target_message_id=mid)
    for i in range(18):
        await store.append(
            observation=_obs(kind="artifact", ref=f"later-{i}", op="create",
                             payload=ArtifactPayload(artifact_kind="post", custody="declared"),
                             ago=-i-1),
            event_id=f"later-{i}", source_digest=f"later-{i}")
    await react("second-actor", 1)
    if outside_window:
        await _say(audit, 41)
    ctx = await _assembler(audit, store).assemble(request=_request())
    assert (mid not in {row.id for row in ctx.transcript}) == outside_window
    prompt = _prompt(ctx)
    if outside_window:
        assert "Recorded reaction state on transcript" not in prompt
    else:
        assert "Recorded reaction state on transcript #" in prompt
        assert "reactions: 👍=1" not in prompt
    head = next(obj for obj in ctx.source_objects if obj.kind == "reaction")
    assert "👍=1" in head.state
    line = next(line for line in _prompt(ctx).splitlines()
                if line.startswith("<source_object") and 'kind="reaction"' in line)
    assert ("👍=1" in line) == outside_window
    detail = await _call(_tools(audit, store), "record_query", object=head.handle)
    assert detail["status"] == "ok" and detail["detail"]["counts"] == {"👍": 1}


async def test_typed_tools_refuse_wrong_handle_kind_and_unsupported_room_scope():
    store = FakeObservationStore()
    await _seed(store)
    tools = _tools(FakeRecordStore(), store)
    other_kind = await _call(tools, "record_query", kind="pin")
    assert other_kind["status"] == "ok"
    handle = other_kind["objects"][0]["object"]
    bad = await _call(tools, "poll_read", object=handle)
    assert bad["status"] == "unavailable"
    broad = await _call(tools, "record_query", kind="poll", scope="all_rooms")
    assert broad["status"] == "unavailable"
