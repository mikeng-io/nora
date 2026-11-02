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
from nora.domain.notes import SourceRef
from nora.domain.observations import (
    ArtifactPayload,
    Coverage,
    MembershipPayload,
    MessageMetadataPayload,
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
from nora.engagement.context import (
    _OBSERVATION_TARGET_MAX,
    _SOURCE_OBJECT_MAX,
    TranscriptInjection,
)
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.bridge.rehydrate import (
    observation_from_dict,
    observation_to_dict,
)
from nora.policy import Conversation, Policy
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.note_store import InMemoryNoteStore
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
        payload=PollPayload(voter_ref="v1", option_ref="r"),
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


async def test_source_objects_and_shared_state_render_in_one_standing_block():
    from nora.domain.cognition_ground import Grounding
    from nora.domain.cognition_session import AgendaItem
    from nora.domain.shared_state import SharedEntry, SharedView
    from nora.language.cognition_render import render_agenda

    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    ctx = await _assembler(audit, store).assemble(request=_request())
    shared = SharedView(
        unit="u1", status="ok", as_of_ms=NOW_MS,
        entries=(SharedEntry(
            kind="decision", id="d-generated", scope="unit", status="open",
            subject="Generated choice remains conditional",
        ),),
    )
    tagged = _prompt(dataclasses.replace(ctx, shared=shared))
    item = AgendaItem(
        kind="message", room=Room("signal", CID), summary="",
        stimulus_ids=(), cues=(), surprise=0.0,
    )
    cognition = render_agenda(
        [item], grounding=Grounding(source_objects={f"signal:{CID}": ctx.source_objects}),
        now_ms=NOW_MS, tz=HK, shared=shared,
    )
    for prompt in (tagged, cognition):
        assert prompt.count("<standing ") == 1
        assert "Generated choice remains conditional" in prompt
        assert '<source_object kind="poll"' in prompt
        assert "closed" in prompt


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


@pytest.mark.parametrize("target_deleted", [False, True])
async def test_the_session_runner_assembles_and_consumes_an_observation_stimulus(target_deleted):
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
    notes = InMemoryNoteStore()
    await _note_with_ref(notes, _POLL_HANDLE)
    await _newer_heads(obs_store, 9)

    async def no_notes(unit: str) -> Any:
        return await notes.standing(unit=unit)

    async def no_events(unit: str, platform: str, cid: str) -> Any:
        return ()

    class _NoRecords:
        async def by_refs(self, refs: Any, rooms: Any) -> Any:
            from nora.domain.models import TranscriptRow
            from nora.domain.record_query import RecordHit, RecordRefHit

            if ("signal", CID) not in rooms:
                return ()
            hits = []
            if ("messages", 9) in refs:
                hits.append(RecordRefHit("messages", 9, RecordHit(
                    "signal", CID,
                    TranscriptRow(9, "author", "Ada", NOW_MS,
                                  "This is the co-delivered utterance",
                                  False, "observed", None))))
            # The AFFECTED row — `target_message_id`, an older message the
            # event acted on, independent of the co-delivered caption.
            if ("messages", 8) in refs:
                hits.append(RecordRefHit("messages", 8, RecordHit(
                    "signal", CID,
                    TranscriptRow(8, "target-author", "Target Author", NOW_MS - 60_000,
                                  "the affected words",
                                  False, "observed", None, person_id="target-person"),
                    deleted=target_deleted)))
            return tuple(hits)

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
    assert '<retained_objects' in prompt
    assert f'object="{_POLL_HANDLE}" matter="Dinner venue still open"' in prompt
    assert "<observation" in prompt and 'kind="poll"' in prompt
    for kind in ("pin", "artifact", "membership"):
        assert f'kind="{kind}"' in prompt
    assert 'op="revise"' in prompt
    assert f'rev="{revision_label(3, "rev")}"' in prompt
    assert "closed" in prompt and "p1" not in prompt
    assert "This is the co-delivered utterance" in prompt
    # The two links stay independent: the co-delivered utterance is a plain
    # `<m>` sibling, the AFFECTED message a marked `target` row — labelled
    # context, never a fresh utterance and never attributed to the actor.
    affected = next(ln for ln in prompt.splitlines() if ln.startswith('<m n="8"'))
    assert 'target="yes"' in affected and 'from="Target Author"' in affected
    if target_deleted:
        assert 'deleted="yes"' in affected
        assert "the affected words" not in prompt
    else:
        assert "the affected words" in affected
    delivered = next(ln for ln in prompt.splitlines() if ln.startswith('<m n="9"'))
    assert "target=" not in delivered

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
    clear_id = await store.append(
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
    line = next(line for line in prompt.splitlines()
                if line.startswith("<source_object") and 'kind="reaction"' in line)
    # The pointer is the typed `on` attribute — the SAME row numbering
    # `replying_to="n"` uses — present only while the target is visible, so no
    # pointer invents a target the transcript does not show.
    assert ('on="#' in line) != outside_window
    if outside_window:
        # Off-window the row keeps the plain source state in Standing.
        assert "on=" not in line
        # …and the affected message's OWN words and author still reach the
        # turn: labelled `target_*` context on the interleaved event —
        # the target's author ("Ada"), never the reactor's actor field
        # (this clear names none), and ONE expansion per assembly (the
        # Standing object repeats the state, not the words).
        ev = next(ln for ln in prompt.splitlines()
                  if ln.startswith("<source_event")
                  and 'kind="reaction"' in ln)
        assert 'target_author="Ada"' in ev
        assert 'target_text="an old target"' in ev
        assert "target_author" not in line
    else:
        # In-window the tally annotates the target row ONCE: the row it is on
        # repeats no `reactions: …` text of its own, and the event carries
        # the `on` link, not a second copy of the words.
        assert "reactions: 👍=1" not in prompt
        ev = next(ln for ln in prompt.splitlines()
                  if ln.startswith("<source_event")
                  and 'kind="reaction"' in ln)
        assert "target_author" not in ev
    head = next(obj for obj in ctx.source_objects if obj.kind == "reaction")
    assert "👍=1" in head.state
    assert ("👍=1" in line) == outside_window
    tools = _tools(audit, store)
    detail = await _call(tools, "record_query", object=head.handle)
    assert detail["status"] == "ok" and detail["detail"]["counts"] == {"👍": 1}
    # The SAME affected message through the detail read — its current
    # words and its author's roster name, never the reactor's.
    target = detail["detail"]["target"]
    assert target["sender"] == "Ada" and target["body"] == "an old target"
    hist = await _call(tools, "record_query", object=head.handle, history=True)
    assert any(e.get("target", {}).get("body") == "an old target"
               for e in hist["events"])
    # `at_id` is source history, not a historical message read: no target
    # text attaches, and the note says so when the object names one.
    asof = await _call(tools, "record_query", object=head.handle,
                       at_id=clear_id)
    assert asof["status"] == "ok" and "target" not in asof
    assert "as-of" in asof["note"] and "current text" in asof["note"]


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


async def test_target_context_names_the_author_never_the_actor():
    """A reaction's actor is not the affected message's author: the
    `target_*` context carries the ROW's own author and words, labelled —
    while the event keeps its own actor. And every unresolvable target
    says `target_unresolved`, never a guessed quote."""
    audit = FakeRecordStore()
    store = FakeObservationStore(records=audit)
    audit.observations = store
    said = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="author-1", sender_name="Author Name",
        timestamp=NOW_MS - 200 * MIN, body="the reacted words", raw={}))
    foreign = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id="room-b", conversation_type="group",
        sender_id="author-2", sender_name="Elsewhere",
        timestamp=NOW_MS - 190 * MIN, body="not this room", raw={}))
    hidden = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="author-1", sender_name="Author Name",
        timestamp=NOW_MS - 185 * MIN, body="taken back silently", raw={}))
    assert await audit.hide_message(message_id=hidden, now_ms=NOW_MS) is True
    gone = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="author-1", sender_name="Author Name",
        timestamp=NOW_MS - 180 * MIN, body="deleted words", raw={}))
    await audit.append_delete(
        message_id=gone, person_id=None,
        message=NormalizedMessage(
            platform="signal", conversation_id=CID,
            conversation_type="group", sender_id="author-1",
            sender_name=None, timestamp=NOW_MS - 179 * MIN,
            body="", raw={}, touch=Touch(
                kind="delete",
                target=EditTarget(message_id="gone"))))
    await _say(audit, 41)
    # The SAME reactor on five different targets — a resolved one, and the
    # four ways a named target cannot be shown.
    for name, mid in (("said", said), ("foreign", foreign),
                      ("hidden", hidden), ("gone", gone),
                      ("dangling", 999999)):
        await store.append(
            observation=_obs(
                kind="reaction", ref=f"msg:{name}", op="add",
                payload=ReactionPayload(emoji="👍"), ago=5,
                actor="+852111", actor_status="known"),
            event_id=f"r-{name}", source_digest=f"r-{name}",
            target_message_id=mid)

    ctx = await _assembler(audit, store).assemble(request=_request())
    prompt = _prompt(ctx)
    lines = [ln for ln in prompt.splitlines()
             if ln.startswith("<source_event")]
    good = next(ln for ln in lines if 'target_text' in ln)
    assert 'target_author="Author Name"' in good
    assert 'target_text="the reacted words"' in good
    # The event's OWN actor is the reactor ("Ada"), never lent the
    # target's authorship.
    assert 'actor="Ada"' in good and 'target_author="Ada"' not in good
    withheld = [ln for ln in lines if 'target_unresolved' in ln]
    assert len(withheld) == 3
    assert "not this room" not in prompt
    assert "taken back silently" not in prompt
    deleted_line = next(ln for ln in lines if 'target_deleted="yes"' in ln)
    assert "deleted words" not in prompt
    assert 'target_text' not in deleted_line
    assert "room-b" not in prompt

    # The real tools carry the same projection: `target` resolves or is
    # `target_unresolved`, and a detail read names the author, not the actor.
    tools = _tools(audit, store)
    out = await _call(tools, "load_transcript", limit=40)
    events = [m["source_event"] for m in out["messages"] if "source_event" in m]
    said_ev = next(e for e in events if e.get("target", {}).get("body"))
    assert said_ev["target"]["sender"] == "Author Name"
    assert said_ev["target"]["body"] == "the reacted words"
    assert said_ev["actor"] == "Ada"
    assert sum(1 for e in events if e.get("target_unresolved")) == 3
    gone_ev = next(e for e in events
                   if e.get("target", {}).get("deleted"))
    assert "body" not in gone_ev["target"]
    detail = await _call(
        tools, "record_query",
        object=object_handle("reaction", "msg:said", account_key="acct"))
    assert detail["status"] == "ok"
    assert detail["detail"]["target"]["sender"] == "Author Name"


async def test_repeated_and_over_bound_targets_stay_bounded_and_explicit():
    """Several events on ONE affected message resolve it once and carry the
    same context; more distinct targets than the bound leaves the rest
    `target_unresolved` — a counted bound, never an unbounded scan."""
    audit = FakeRecordStore()
    store = FakeObservationStore(records=audit)
    audit.observations = store
    shared = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="+852111", sender_name="Ada",
        timestamp=NOW_MS - 220 * MIN, body="shared target", raw={}))
    extras = []
    for i in range(_OBSERVATION_TARGET_MAX + 1):
        extras.append(await audit.append(message=NormalizedMessage(
            platform="signal", conversation_id=CID,
            conversation_type="group", sender_id="author-1",
            sender_name="Author Name",
            timestamp=NOW_MS - (200 - i) * MIN, body=f"extra {i}", raw={})))
    await _say(audit, 41)
    for i in range(3):
        await store.append(
            observation=_obs(kind="reaction", ref="msg:shared", op="add",
                             payload=ReactionPayload(emoji="👍"), ago=10 - i),
            event_id=f"d-{i}", source_digest=f"d-{i}",
            target_message_id=shared)
    for i, mid in enumerate(extras):
        await store.append(
            observation=_obs(
                kind="pin", ref=f"set-{i}", op="add",
                payload=PinPayload(message=EditTarget(message_id=f"t-{i}")),
                ago=8 - i * 0.05),
            event_id=f"p-{i}", source_digest=f"p-{i}",
            target_message_id=mid)

    ctx = await _assembler(audit, store).assemble(request=_request())
    lines = [ln for ln in _prompt(ctx).splitlines()
             if ln.startswith("<source_event")]
    # The one shared target's words ride all three reaction events — the
    # SAME resolved context, one Record read, no three different quotes.
    assert sum('target_text="shared target"' in ln for ln in lines) == 3
    # `_OBSERVATION_TARGET_MAX` distinct targets resolve; the bound cuts
    # the rest, and the cut says so on each.
    assert sum('target_unresolved' in ln for ln in lines) == 2

    tools = _tools(audit, store)
    hist = await _call(
        tools, "record_query",
        object=object_handle("reaction", "msg:shared", account_key="acct"),
        history=True)
    assert all(e.get("target", {}).get("body") == "shared target"
               for e in hist["events"])


# ---------------------------------------------------------------------------
# The retained lane (blocker5, metadata-standing-continuity): an open note's
# curated `object_refs` resolve beside the bounded page, in BOTH assemblies.
# Written at the real seams; NOT RUN here — the parent owns execution.

_POLL_HANDLE = object_handle("poll", "p1", account_key="acct", thread="")


def _policy(unit: str = "u1") -> Policy:
    convo = Conversation(
        platform="signal", ids=frozenset({CID}), persons=None,
        primary_id=CID, unit=unit)
    return Policy(conversations=(convo,), _by_id={("signal", CID): convo})


async def _note_with_ref(
    notes: InMemoryNoteStore, handle: str, *, room: str = CID,
) -> Any:
    return await notes.create(
        unit="u1", title="Dinner venue still open",
        description="the poll is not settled",
        uncertainty="which venue wins", closing_condition="poll closes",
        established_in=room,
        body="## Decided\n- nothing\n## Open\n- venue",
        established_by="test", now_ms=NOW_MS, anchor_date=20261003,
        object_refs=(SourceRef(
            platform="signal", conversation_id=room, handle=handle),))


async def _newer_heads(
    store: FakeObservationStore, n: int, *, prefix: str = "unrelated",
) -> None:
    for i in range(n):
        assert await store.append(
            observation=_obs(
                kind="pin", ref=f"{prefix}-{i}", op="add",
                payload=PinPayload(message=EditTarget(message_id=f"m-{i}")),
                ago=1),
            event_id=f"{prefix}-{i}", source_digest=f"{prefix}-{i}",
        ) is not None


def _assembler_with_notes(
    audit: FakeRecordStore, observations: Any, notes: Any,
    lineage: LineagePolicy | None = None,
) -> TranscriptInjection:
    return TranscriptInjection(
        audit=audit, memory=FakeMemoryStore(), persons=PERSONS,
        context_window=40, metrics=Metrics(), observations=observations,
        notes=notes, policy=_policy(), lineage=lineage)


async def test_a_retained_poll_survives_newer_unrelated_objects_in_the_assembler():
    """blocker5's shape: the tracked poll falls off the recency page but
    stays in view through the note's ref — resolved NOW, marked `matter`,
    shown exactly once."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)  # the poll, closed at position 3
    await _newer_heads(store, _SOURCE_OBJECT_MAX + 1)
    notes = InMemoryNoteStore()
    await _note_with_ref(notes, _POLL_HANDLE)

    ctx = await _assembler_with_notes(audit, store, notes).assemble(
        request=_request())

    assert len(ctx.source_objects) == _SOURCE_OBJECT_MAX
    assert _POLL_HANDLE not in {o.handle for o in ctx.source_objects}
    retained = [r for r in ctx.retained_sources if r.ref.handle == _POLL_HANDLE]
    assert len(retained) == 1
    assert retained[0].head is not None
    assert "closed" in (retained[0].head.state or "")
    assert retained[0].matter == "Dinner venue still open"
    prompt = _prompt(ctx)
    assert sum(f'object="{_POLL_HANDLE}"' in line
               for line in prompt.splitlines() if line.startswith("<source_object")) == 1
    assert 'matter="Dinner venue still open"' in prompt


async def test_a_late_vote_refreshes_the_retained_polls_current_state():
    """A vote landing after the note was written re-reads the object NOW:
    the retained row carries the NEW tally, not the state the note saw."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await _newer_heads(store, _SOURCE_OBJECT_MAX + 1)
    notes = InMemoryNoteStore()
    await _note_with_ref(notes, _POLL_HANDLE)
    assert await store.append(
        observation=_obs(kind="poll", ref="p1", op="add",
                         payload=PollPayload(voter_ref="v2", option_ref="s"),
                         ago=1, position=4),
        event_id="late-vote", source_digest="late-vote") is not None
    await _newer_heads(store, _SOURCE_OBJECT_MAX + 1, prefix="after-vote")

    ctx = await _assembler_with_notes(audit, store, notes).assemble(
        request=_request())

    retained = next(r for r in ctx.retained_sources
                    if r.ref.handle == _POLL_HANDLE)
    assert retained.head is not None
    assert "sushi" in (retained.head.state or "")


async def test_closing_the_poll_does_not_retire_the_matter():
    """A poll `revise` → closed is source-object state only: the note stays
    open in the index and its ref still resolves the now-closed head."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await _newer_heads(store, _SOURCE_OBJECT_MAX + 1)
    notes = InMemoryNoteStore()
    note = await _note_with_ref(notes, _POLL_HANDLE)

    ctx = await _assembler_with_notes(audit, store, notes).assemble(
        request=_request())

    assert note.id in {n.id for n in ctx.notes}
    assert any(r.ref.handle == _POLL_HANDLE and r.head is not None
               and "closed" in (r.head.state or "")
               for r in ctx.retained_sources)


async def test_retiring_the_matter_retires_its_reference_but_keeps_history():
    """`note_set_status`'s supersede removes the note from `standing` — its
    ref resolves nowhere — while `read` still returns the retired row."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await _newer_heads(store, _SOURCE_OBJECT_MAX + 1)
    notes = InMemoryNoteStore()
    note = await _note_with_ref(notes, _POLL_HANDLE)
    retired = await notes.supersede(
        key="u1", target=note.id, replaced_by=None,
        because="retired-closed", source=None, now_ms=NOW_MS,
        expected_head=None)

    assert retired == 1
    ctx = await _assembler_with_notes(audit, store, notes).assemble(
        request=_request())
    assert ctx.retained_sources == ()
    assert note.id not in {n.id for n in ctx.notes}
    found = await notes.read(unit="u1", note_id=note.id)
    assert found is not None
    assert found[0].object_refs[0].handle == _POLL_HANDLE


async def test_scope_invalid_and_unresolvable_refs_are_explicit_never_dropped():
    """A ref naming ANOTHER room resolves nothing here (the boundary is the
    scope check); a well-formed handle the store does not hold comes back
    `unresolved`, not silently absent."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    notes = InMemoryNoteStore()
    await _note_with_ref(notes, _POLL_HANDLE, room="room-b")
    ghost = object_handle("poll", "p-ghost", account_key="acct", thread="")
    await _note_with_ref(notes, ghost)

    ctx = await _assembler_with_notes(audit, store, notes).assemble(
        request=_request())

    assert not [r for r in ctx.retained_sources if r.ref.conversation_id == "room-b"]
    unresolved = [r for r in ctx.retained_sources if r.head is None]
    assert [r.ref.handle for r in unresolved] == [ghost]
    prompt = _prompt(ctx)
    assert 'unresolved="yes"' in prompt


async def test_the_grounder_keeps_a_retained_poll_past_eight_newer_objects():
    """The Cognition seam: `build_grounder` resolves the note's ref beside
    the 8-head discovery page — the retained object keeps its CURRENT state
    and never doubles a head the page already shows."""
    obs_store = FakeObservationStore()
    await _seed(obs_store)
    await _newer_heads(obs_store, 9)
    notes = InMemoryNoteStore()
    await _note_with_ref(notes, _POLL_HANDLE)

    async def _index(unit: str) -> Any:
        return await notes.standing(unit=unit)

    async def _no_events(unit: str, platform: str, cid: str) -> Any:
        return ()

    class _NoRecords:
        async def by_refs(self, refs: Any, rooms: Any) -> Any:
            return ()

        async def own_sends(self, rooms: Any, *, start_ms: int,
                            end_ms: int, limit: int) -> Any:
            return ()

    grounder = build_grounder(
        records=_NoRecords(), persons=PERSONS, media_join=None,
        note_index=_index, events_in=_no_events,
        lineage=LineagePolicy(rooms=(), mode="off"),
        unit_rooms=lambda unit: {("signal", CID)},
        observations=obs_store)
    from nora.domain.cognition_session import AgendaItem
    item = AgendaItem(
        kind="message", room=Room("signal", CID), summary="",
        stimulus_ids=(), cues=(), surprise=0.0)

    grounding = await grounder("u1", [item], [])

    key = f"signal:{CID}"
    assert len(grounding.source_objects.get(key, ())) == 8
    assert _POLL_HANDLE not in {
        o.handle for o in grounding.source_objects.get(key, ())}
    retained = grounding.retained_objects.get(key, ())
    assert len(retained) == 1
    assert retained[0].head is not None
    assert "closed" in (retained[0].head.state or "")
    assert retained[0].matter == "Dinner venue still open"


async def test_note_revision_replaces_object_refs_atomically():
    """`append_revision` with a supplied `object_refs` replaces the pointer
    set under the same CAS as the body; `None` keeps the existing set —
    the semantics `note_revise` relies on."""
    notes = InMemoryNoteStore()
    note = await _note_with_ref(notes, _POLL_HANDLE)
    other = object_handle("pin", "pins", account_key="acct", thread="")

    rev = await notes.append_revision(
        unit="u1", note_id=note.id, body="## Decided\n- x\n## Open\n- y",
        parent_revision_id=note.head_revision_id,
        established_by="test", now_ms=NOW_MS,
        object_refs=(SourceRef(platform="signal", conversation_id=CID,
                               handle=other),))
    found = await notes.read(unit="u1", note_id=note.id)
    assert [r.handle for r in found[0].object_refs] == [other]

    await notes.append_revision(
        unit="u1", note_id=note.id, body="## Decided\n- x\n## Open\n- y2",
        parent_revision_id=rev.id,
        established_by="test", now_ms=NOW_MS)
    found = await notes.read(unit="u1", note_id=note.id)
    assert [r.handle for r in found[0].object_refs] == [other]


async def test_retained_ref_verbs_accept_shown_handles_and_refuse_unseen_objects():
    from nora.domain.cognition_ground import Grounding
    from nora.testing.cognition_grounding import StaticGrounder
    from tests.unit.cognition_rig import GOOD_BODY, NOTE, RECORDS, session

    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    ctx = await _assembler(audit, store).assemble(request=_request())
    grounding = Grounding(
        records=dict(RECORDS), source_objects={f"signal:{CID}": ctx.source_objects})
    answers = []
    forged = object_handle("poll", "unseen", account_key="acct")
    await store.append(
        observation=_obs(kind="poll", ref="unseen", op="create", ago=1,
                         payload=PollPayload(question="Unshown question", options=(("a", "A"),))),
        event_id="unshown", source_digest="unshown")

    async def behave(by, tools):
        # The model needs only the object handle, never internal room/account ids.
        for refs in ([{"handle": _POLL_HANDLE, "conversation_id": CID}],
                     [_POLL_HANDLE, _POLL_HANDLE], [_POLL_HANDLE] * 9):
            denied = await by["note_create"](body=GOOD_BODY, object_refs=refs, **NOTE)
            assert denied["status"] == "refused"
            assert await stores.notes.standing(unit="u") == ()
        answers.append(await by["note_create"](
            body=GOOD_BODY, object_refs=[_POLL_HANDLE], **NOTE))
        if answers[-1]["status"] != "ok":
            return
        nid = answers[-1]["note_id"]
        note, revision = await stores.notes.read(unit="u", note_id=nid)
        assert note.object_refs == (SourceRef("signal", CID, _POLL_HANDLE),)
        answers.append(await by["note_revise"](
            note_id=nid, body=GOOD_BODY, parent_revision_id=revision.id,
            object_refs=[forged]))
        assert answers[-1]["status"] == "refused"
        assert (await stores.notes.read(unit="u", note_id=nid))[1].id == revision.id
        answers.append(await by["note_revise"](
            note_id=nid, body=GOOD_BODY, parent_revision_id=revision.id,
            object_refs=[]))
        assert answers[-1]["status"] == "ok"
        assert (await stores.notes.read(unit="u", note_id=nid))[0].object_refs == ()

    from tests.unit.cognition_rig import Stores
    stores = Stores()
    await session(behave, stores=stores, runner_kwargs={
        "grounder": StaticGrounder(grounding), "observations": store})
    assert answers and answers[0]["status"] == "ok", answers


async def test_retained_sources_report_the_bound_and_never_read_another_room():
    from nora.domain.cognition_session import AgendaItem
    from nora.domain.notes import NOTE_OBJECT_REFS_MAX
    from nora.domain.observations import handle_of
    from nora.language.cognition_render import render_agenda
    audit, store, notes = FakeRecordStore(), FakeObservationStore(), InMemoryNoteStore()
    handles = []
    for i in range(14):
        rid = await store.append(
            observation=_obs(kind="poll", ref=f"tracked-{i}", op="create", ago=1,
                             payload=PollPayload(question=f"Tracked question {i}",
                                                 options=(("a", "A"),))),
            event_id=f"tracked-{i}", source_digest=f"tracked-{i}")
        assert rid
        head = await store.current(scope=SCOPE, kind="poll", object_ref=f"tracked-{i}")
        handles.append(handle_of(head))
    for i in range(0, len(handles), NOTE_OBJECT_REFS_MAX):
        note = await _note_with_ref(notes, handles[i])
        await notes.append_revision(
            unit="u1", note_id=note.id, body="tracked questions", now_ms=NOW_MS,
            parent_revision_id=note.head_revision_id, established_by="test",
            object_refs=tuple(SourceRef("signal", CID, h)
                              for h in handles[i:i+NOTE_OBJECT_REFS_MAX]))
    await _newer_heads(store, 14)
    ctx = await _assembler_with_notes(audit, store, notes).assemble(request=_request())
    assert len(ctx.retained_sources) == 12 and ctx.retained_sources_more == 2
    assert '<source_objects_more count="2"' in _prompt(ctx)
    async def index(unit):
        return await notes.standing(unit=unit)
    class NoRecords:
        async def by_refs(self, refs, rooms):
            return ()
        async def own_sends(self, rooms, *, start_ms, end_ms, limit):
            return ()
    async def no_events(unit, platform, cid):
        return ()
    grounder = build_grounder(
        records=NoRecords(), persons=PERSONS, media_join=None, note_index=index,
        events_in=no_events, lineage=LineagePolicy(rooms=(), mode="off"),
        unit_rooms=lambda unit: {("signal", CID)}, observations=store)
    item = AgendaItem(kind="message", room=Room("signal", CID), summary="",
                      stimulus_ids=(), cues=(), surprise=0.0)
    grounded = await grounder("u1", [item], [])
    assert len(grounded.retained_objects[f"signal:{CID}"]) == 8
    assert grounded.retained_objects_more[f"signal:{CID}"] == 6
    prompt = render_agenda([item], stimuli={}, grounding=grounded, now_ms=NOW_MS)
    assert '<retained_objects' in prompt and 'not_listed="6"' in prompt
    note = (await notes.standing(unit="u1"))[0]
    with pytest.raises(ValueError, match="at most"):
        dataclasses.replace(note, object_refs=tuple(SourceRef("signal", CID, h)
                            for h in handles[:NOTE_OBJECT_REFS_MAX+1]))


async def test_a_withheld_note_does_not_leak_its_title_through_retained_sources():
    audit, store, notes = FakeRecordStore(), FakeObservationStore(), InMemoryNoteStore()
    await _seed(store)
    await _newer_heads(store, 14)
    note = await _note_with_ref(notes, _POLL_HANDLE)
    notes.notes[note.id] = dataclasses.replace(note, established_in="another-room")
    ctx = await _assembler_with_notes(
        audit, store, notes,
        lineage=LineagePolicy.from_policy(_policy(), mode="on")).assemble(request=_request())
    prompt = _prompt(ctx)
    assert "Dinner Friday?" in prompt  # This room's source evidence still surfaces.
    assert "Dinner venue still open" not in prompt
    assert not ctx.retained_sources


async def test_note_read_returns_only_the_retained_handles():
    from tests.test_note_tools import _tools as note_tools
    notes = InMemoryNoteStore()
    note = await _note_with_ref(notes, _POLL_HANDLE)
    out = await note_tools(notes=notes, unit="u1", conversation_id=CID)["note_read"](
        title=note.title)
    assert out["status"] == "ok"
    assert out["object_refs"] == [_POLL_HANDLE]
    assert CID not in json.dumps(out)


async def test_forwarded_metadata_detail_projects_its_source_reference():
    audit, store = FakeRecordStore(), FakeObservationStore()
    origin = "discord:generated-guild/generated-channel/generated-message"
    await store.append(
        observation=_obs(kind="message_metadata", ref="forwarded", op="create", ago=1,
                         platform="discord", payload=MessageMetadataPayload(
                             forward_origin=origin, expires_in_ms=30_000)),
        event_id="forwarded", source_digest="forwarded")
    out = await _call(_tools(audit, store, platform="discord"), "record_query",
                      object=object_handle("message_metadata", "forwarded", account_key="acct"))
    assert out["status"] == "ok"
    assert out["detail"]["fields"]["expires_in_ms"] == 30_000
    assert origin not in json.dumps(out)
    head = await store.current(
        scope=SourceScope(platform="discord", account_key="acct", conversation_id=CID),
        kind="message_metadata", object_ref="forwarded")
    assert head.state["fields"]["forward_origin"] == origin


async def test_a_handle_shown_for_two_rooms_cannot_silently_bind_the_last_room():
    from nora.domain.cognition_ground import Grounding
    from nora.testing.cognition_grounding import StaticGrounder
    from tests.unit.cognition_rig import GOOD_BODY, NOTE, RECORDS, A, B, session
    store, audit = FakeObservationStore(), FakeRecordStore()
    # Signal really uses object_ref=pin_set under the same account in every group.
    for room in (A, B):
        await store.append(
            observation=_obs(kind="pin", ref="pin_set", op="add", ago=1,
                             room=room.conversation_id,
                             payload=PinPayload(message=EditTarget(message_id=room.conversation_id))),
            event_id=f"pins-{room.conversation_id}", source_digest=f"pins-{room.conversation_id}")
    a = await _assembler(audit, store).assemble(request=_request())
    b_request = dataclasses.replace(
        _request(), message=dataclasses.replace(
            _request().message, conversation_id=B.conversation_id))
    b = await _assembler(audit, store).assemble(request=b_request)
    assert a.source_objects[0].handle == b.source_objects[0].handle
    grounding = Grounding(records=dict(RECORDS), source_objects={
        f"signal:{A.conversation_id}": a.source_objects,
        f"signal:{B.conversation_id}": b.source_objects})
    answers=[]
    async def behave(by, tools):
        answers.append(await by["note_create"](
            body=GOOD_BODY, item=0, object_refs=[a.source_objects[0].handle], **NOTE))
    stores, _ = await session(behave, rooms=(A,B), runner_kwargs={
        "grounder":StaticGrounder(grounding), "observations":store})
    assert answers[0]["status"] == "refused", answers
    assert answers[0]["reason"] == "ambiguous_object_ref"
    assert await stores.notes.standing(unit="u") == ()
