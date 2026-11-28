"""The platform-observation wired acceptance paths (docs/design/platform/observations.md
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

from nora.agent.dreamer import AdaptorBudget
from nora.agent.lineage import LineagePolicy
from nora.agent.prefill import engagement_floor
from nora.agent.room import Room as AgentRoom
from nora.agent.tools import build_tools
from nora.app.cognition_agent_runner import CognitionRunResult
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
from nora.engagement.context import (
    _OBSERVATION_TARGET_MAX,
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
from nora.testing import FakeRecordStore
from nora.testing.note_store import InMemoryNoteStore
from nora.testing.observations import FakeObservationStore
from tests.fakes import SinkSender

HK = ZoneInfo("Asia/Hong_Kong")
NOW_MS = int(dt.datetime(2026, 9, 30, 15, 30, tzinfo=HK).timestamp() * 1000)
NOW = dt.datetime.fromtimestamp(NOW_MS / 1000, HK)
MIN = 60_000
CID = "room-a"
SCOPE = SourceScope(platform="signal", account_key="acct", conversation_id=CID)

PERSONS = Persons(
    by_person={"ada": PersonRef(person_id="ada", name="Ada")},
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
        audit=audit, persons=PERSONS,
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


def _prompt(ctx: Any, *, display_name: str = "Nora") -> str:
    blocks = engagement_floor(
        ctx, display_name=display_name, tagger_name="Ada", context_window=40,
        now=NOW, catalogue="",
    )
    return next(block.render() for block in blocks if block.name == "transcript")


class _NoRecord:
    async def query(self, q: Any) -> tuple[tuple, bool, frozenset[int]]:
        return (), False, frozenset()


def _tools(audit: FakeRecordStore, store: Any, cid: str = CID,
           platform: str = "signal") -> dict[str, Any]:
    tools = build_tools(
        store=audit, conversation_id=cid, conversation_type="group",
        persons=PERSONS, deliverer=SinkSender(),
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
    message window, but its typed source event still reaches the transcript."""
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

    prompt = _prompt(ctx)
    assert "<source_event" in prompt and 'kind="poll"' in prompt
    assert "p1" not in prompt
    assert "room-a" not in prompt and "acct" not in prompt


async def test_vote_and_close_with_no_new_human_message_still_render():
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    await _say(audit, 3)

    ctx = await _assembler(audit, store).assemble(request=_request())

    assert len(ctx.transcript) == 3
    ops = [(e.kind, e.operation) for e in ctx.source_events]
    assert ("poll", "add") in ops and ("poll", "revise") in ops
    close = next(e for e in ctx.source_events
                 if e.operation == "revise" and e.kind == "poll")
    assert close.revision == revision_label(3, "rev")
    assert "rev" not in close.revision.split(":")[0]
    prompt = _prompt(ctx)
    assert "poll revise: state closed, 1 votes" in prompt


async def test_engagement_floor_resolves_the_poll_option_label():
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _seed(store)
    ctx = await _assembler(audit, store).assemble(request=_request())

    prompt = _prompt(ctx)
    assert "poll vote add: a member on ramen" in prompt
    assert " on option:" not in prompt


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

    rq = await _call(tools, "record_query", kind="poll", object=handle)
    assert rq["status"] == "ok" and rq["detail"]["question"] == "Dinner Friday?"

    hist = await _call(tools, "record_query", kind="poll", object=handle,
                       history=True)
    assert hist["status"] == "ok" and len(hist["events"]) == 3
    as_of = await _call(tools, "record_query", kind="poll", object=handle,
                        at_id=ids["vote"])
    assert as_of["status"] == "ok" and "closed" not in as_of["state"]

    pin_q = await _call(tools, "record_query",
                        object=object_handle("pin", "pins-0",
                                             account_key="acct"))
    assert pin_q["status"] == "ok" and pin_q["detail"]["pin_count"] == 1

    # Scope isolation: the same tools bound to another room see nothing.
    other = _tools(FakeRecordStore(), store, cid="room-b")
    assert (await _call(other, "poll_list"))["status"] == "nothing_found"


async def test_the_artifact_delta_reads_the_same_in_every_surface():
    """RSVP deltas through the REAL `build_tools`: the history line names
    the responder and the answer (extra guests too), the as-of read holds
    the bounded event state AT that row, and a later unreadable response
    clears the standing attendee — one projection, no bare count, no
    stale fact, no platform handle."""
    store = FakeObservationStore()
    rid_going = None
    async def put(name: str, obs: Observation) -> int:
        rid = await store.append(
            observation=obs, event_id=f"evt-{name}", source_digest=f"d-{name}")
        assert rid is not None
        return rid

    await put("create", _obs(
        kind="artifact", ref="e9", op="create",
        payload=ArtifactPayload(
            artifact_kind="event", custody="declared",
            metadata={"name": "Breakfast", "status": "scheduled",
                      "start_time_s": NOW_MS // 1000 + 3600}),
        ago=30, actor="+852111", actor_status="known", position=1))
    rid_going = await put("going", _obs(
        kind="artifact", ref="e9", op="revise",
        payload=ArtifactPayload(
            artifact_kind="event", custody="declared",
            metadata={"response": "going", "extra_guest_count": 2}),
        ago=20, actor="+852111", actor_status="known", position=2))
    await put("unknown", _obs(
        kind="artifact", ref="e9", op="revise",
        payload=ArtifactPayload(
            artifact_kind="event", custody="declared",
            metadata={"response": ""}),
        ago=10, actor="+852111", actor_status="known", position=3,
        coverage=Coverage(complete=False, covered="response",
                          gaps=("response",), reason="unknown")))

    tools = _tools(FakeRecordStore(), store)
    handle = object_handle("artifact", "e9", account_key="acct")

    hist = await _call(tools, "record_query", kind="artifact",
                       object=handle, history=True)
    assert hist["status"] == "ok", hist
    text = json.dumps(hist)
    assert "going" in text and "2 extra" in text
    # An answer the source marked unreadable is never a "withdrew".
    assert "withdrew" not in text
    assert "e9" not in text  # the platform ref never renders

    # As-of at the response row: the answer was still readable then, and
    # the read carries the event's own bounded fields.
    as_of = await _call(tools, "record_query", kind="artifact",
                        object=handle, at_id=rid_going)
    assert as_of["status"] == "ok", as_of
    body = json.dumps(as_of)
    assert "going" in body and "Ada" in body and "Breakfast" in body
    assert "e9" not in body and "acct" not in body

    detail = await _call(tools, "record_query", kind="artifact", object=handle)
    assert detail["status"] == "ok", detail
    assert "going" not in json.dumps(detail)


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
    """The sentinel counts an omitted event when the transcript window fills."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    await _say(audit, 3)
    for i in range(50):  # one pin SET, fifty pin events
        await store.append(
            observation=_obs(
                kind="pin", ref="pins", op="add",
                payload=PinPayload(message=EditTarget(message_id=f"m-{i}")),
                ago=200 - i, actor="+852111", actor_status="known"),
            event_id=f"cap-{i}", source_digest=f"cap-{i}")
    ctx = await _assembler(audit, store).assemble(request=_request())

    assert len(ctx.source_events) == 40 and ctx.source_events_more == 1
    prompt = _prompt(ctx)
    assert 'source_more="1"' in prompt


async def test_revision_and_event_ref_agree_across_prompt_and_tools():
    """The transcript event and tool detail name the same applied revision."""
    audit, store = FakeRecordStore(), FakeObservationStore()
    ids = await _seed(store)
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
    assert close.ref == f"platform_observation:{ids['close']}"

    listed = await _call(tools, "poll_list")
    handle = listed["polls"][0]["object"]
    detail = await _call(tools, "record_query", kind="poll", object=handle)
    assert detail["detail"]["revision"] == close.revision

    prompt = _prompt(ctx)
    assert f'rev="{close.revision}"' in prompt
    assert f'rev="{revision_label(12, "discord-sess-9f8c")}"' in prompt
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

    prompt = _prompt(ctx)
    ev_line = next(ln for ln in prompt.splitlines()
                   if ln.startswith("<source_event"))
    assert 'time="unknown"' in ev_line and "received=" in ev_line
    assert "at=" not in ev_line

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
    handle = object_handle("pin", "pins", account_key=SCOPE.account_key)
    detail = await _call(_tools(audit, store), "record_query", object=handle)
    assert detail["status"] == "ok"
    assert "observed_ms" not in detail["detail"]
    assert detail["detail"]["time_quality"] == "unknown"
    assert detail["detail"]["received_ms"] == NOW_MS - MIN + 5


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
    prompt = _prompt(ctx)
    assert 'kind="artifact"' in prompt and 'kind="membership"' in prompt
    assert "private-artifact-origin" not in prompt and "+999000" not in prompt
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
    transcript_part = prompt.split("<transcript")[1]
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
    rq = await _call(ig_tools, "record_query", kind="pin")
    assert rq["status"] == "ok" and len(rq["objects"]) == 1
    sig_tools = _tools(FakeRecordStore(), store)
    rq = await _call(sig_tools, "record_query", kind="poll")
    assert rq["status"] == "nothing_found"
    listed = await _call(sig_tools, "poll_list")
    assert listed["status"] == "nothing_found"


@pytest.mark.parametrize("outside_window", [False, True])
async def test_old_target_reaction_reaches_the_transcript_and_tools(outside_window):
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
    ev = next(line for line in prompt.splitlines()
              if line.startswith("<source_event") and 'kind="reaction"' in line)
    # The pointer is the typed `on` attribute — the SAME row numbering
    # `replying_to="n"` uses — present only while the target is visible, so no
    # pointer invents a target the transcript does not show.
    assert (' on="' in ev) != outside_window
    if outside_window:
        assert 'target_author="Ada"' in ev
        assert 'target_text="an old target"' in ev
    else:
        assert "target_author" not in ev
        assert prompt.count("an old target") == 1
    handle = object_handle("reaction", "msg:old-target", account_key=SCOPE.account_key)
    tools = _tools(audit, store)
    detail = await _call(tools, "record_query", object=handle)
    assert detail["status"] == "ok" and detail["detail"]["counts"] == {"👍": 1}
    # The SAME affected message through the detail read — its current
    # words and its author's roster name, never the reactor's.
    target = detail["detail"]["target"]
    assert target["sender"] == "Ada" and target["body"] == "an old target"
    hist = await _call(tools, "record_query", object=handle, history=True)
    assert any(e.get("target", {}).get("body") == "an old target"
               for e in hist["events"])
    # `at_id` is source history, not a historical message read: no target
    # text attaches, and the note says so when the object names one.
    asof = await _call(tools, "record_query", object=handle,
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


async def test_head_target_budget_preserves_store_recency_order():
    audit, store = FakeRecordStore(), FakeObservationStore()
    message = _request().message
    older = await audit.append(message=dataclasses.replace(
        message, timestamp=NOW_MS - 240 * MIN, body="head-only target"))
    targets = [
        await audit.append(message=dataclasses.replace(
            message, timestamp=NOW_MS - (200 - i) * MIN, body=f"event target {i}"))
        for i in range(_OBSERVATION_TARGET_MAX)
    ]
    await _say(audit, 41)

    # The store last wrote this head first. Its receipt clock sorts it first
    # only if assembly reorders the bounded head page by source clocks.
    older_pin = _obs(kind="pin", ref="head-only", op="add",
                     payload=PinPayload(message=EditTarget(message_id="legacy")),
                     ago=120)
    await store.append(
        observation=dataclasses.replace(older_pin, received_ms=NOW_MS + MIN),
        event_id="head-only", source_digest="head-only", target_message_id=older,
    )
    for i in range(41):
        await store.append(
            observation=_obs(kind="pin", ref=f"recent-{i}", op="add",
                             payload=PinPayload(message=EditTarget(message_id=f"p-{i}")),
                             ago=45 - i),
            event_id=f"recent-{i}", source_digest=f"recent-{i}",
            target_message_id=targets[i % len(targets)],
        )

    ctx = await _assembler(audit, store).assemble(request=_request())
    prompt = _prompt(ctx)
    assert f'target_text="event target {len(targets) - 1}"' in prompt
    assert "target_unresolved" not in prompt


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


@pytest.mark.parametrize("own", [True, False], ids=["own", "other"])
async def test_an_off_window_target_uses_the_markdown_self_name(example_config_path, own):
    from nora.app.assembler import build_assembler
    from nora.config import load

    identity = example_config_path.parent / "nora/doctrine/core/identity.md"
    identity.write_text(
        identity.read_text().replace("display_name: Nora", "display_name: AgentAlpha"))
    cfg = load(example_config_path, require_secrets=False)
    audit = FakeRecordStore()
    observations = FakeObservationStore(records=audit)
    audit.observations = observations
    mid = await audit.append(message=NormalizedMessage(
        platform="signal", conversation_id=CID, conversation_type="group",
        sender_id="generated-account", sender_name="PreviousLabel",
        timestamp=NOW_MS - 180 * MIN, body="an older message", raw={},
        message_id="older-target"), is_nora=own)
    await _say(audit, 41)
    await observations.append(
        observation=_obs(kind="reaction", ref="msg:older-target", op="clear",
                         payload=ReactionPayload(), ago=1, actor="+852111",
                         actor_status="known", position=1,
                         target=EditTarget(message_id="older-target")),
        event_id="generated-reaction", source_digest="generated-digest", target_message_id=mid)
    ctx = await build_assembler(
        cfg, audit=audit, persons=PERSONS,
        metrics=Metrics(), policy=_policy(), observations=observations,
        clock=lambda: NOW,
    ).assemble(request=_request())
    assert mid not in {row.id for row in ctx.transcript}
    prompt = _prompt(ctx, display_name=cfg.doctrine.display_name)
    expected = "AgentAlpha" if own else "PreviousLabel"
    assert f'target_author="{expected}"' in prompt
    assert 'target_text="an older message"' in prompt
    if own:
        assert 'target_author="PreviousLabel"' not in prompt
