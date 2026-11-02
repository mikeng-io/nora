"""The platform-observation mixed journey, end to end over the fake Record.

design/platform-observations 04's proof, bound by construction: the SAME
`fold_observation` the SQL head update runs, the grounder the session runs,
and `render_agenda` the model reads — one generated journey (poll create,
share, room/pin/member changes, a vote and a close that arrive with NO new
human message) walked from append to agenda row to Standing block.

Rooms, people and object refs are generated placeholders; nothing here is a
real room's or a production row.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from nora.agent.lineage import LineagePolicy
from nora.app.cognition_grounding import build_grounder
from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import Room, build_situation
from nora.domain.models import EditTarget, PersonRef
from nora.domain.observations import (
    ArtifactPayload,
    Coverage,
    MembershipPayload,
    Observation,
    PinPayload,
    PollPayload,
    RoomPayload,
    SourceScope,
)
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import render_agenda
from nora.persons import Persons
from nora.testing.observations import FakeObservationStore
from tests.unit.cognition_view import items

HK = ZoneInfo("Asia/Hong_Kong")
NOW = int(dt.datetime(2026, 9, 30, 15, 30, tzinfo=HK).timestamp() * 1000)
MIN = 60_000
ROOM = Room("signal", "room-a")
SCOPE = SourceScope(platform="signal", account_key="acct",
                    conversation_id="room-a")


def _obs(*, kind: str, ref: str, op: str, payload: Any, ago: float,
         actor: str = "", actor_status: str = "not_supplied",
         position: int | None = None,
         coverage: Coverage | None = None) -> Observation:
    return Observation(
        object_kind=kind, scope=SCOPE, object_ref=ref, operation=op,
        payload=payload, actor_id=actor, actor_status=actor_status,
        occurred_ms=NOW - int(ago * MIN), received_ms=NOW - int(ago * MIN) + 5,
        time_quality="upstream", source_position=position,
        revision_scope="rev" if position is not None else "",
        coverage=coverage or Coverage(),
    )


def _stim(sid: int, obs_id: int, kind: str, ago: float) -> Stimulus:
    return Stimulus(
        id=sid, unit="u", kind="observation", item=kind,
        ref=f"platform_observation:{obs_id}", at_ms=NOW - int(ago * MIN),
        recorded_at_ms=NOW - int(ago * MIN), platform=ROOM.platform,
        conversation_id=ROOM.conversation_id,
    )


_PERSONS = Persons(
    by_person={"ada": PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")},
    index={("signal", "+852111"): "ada"},
)


def _grounder(store: FakeObservationStore):
    async def notes(unit: str) -> Any:
        return ()

    async def events(unit: str, platform: str, cid: str) -> Any:
        return ()

    class _NoRecords:
        async def by_refs(self, refs: Any, rooms: Any, **kw: Any) -> Any:
            return ()

        async def own_sends(self, rooms: Any, *, start_ms: int,
                            end_ms: int, limit: int, **kw: Any) -> Any:
            return ()

    return build_grounder(
        records=_NoRecords(), persons=_PERSONS, media_join=None,
        note_index=notes, events_in=events, lineage=LineagePolicy(
            rooms=(), mode="off"),
        unit_rooms=lambda unit: {(ROOM.platform, ROOM.conversation_id)},
        room_label=lambda platform, cid: "Room A",
        observations=store,
    )


def _grounding(store: FakeObservationStore, stimuli: Any) -> Any:
    sit = build_situation(unit="u", pending=stimuli, home_room=ROOM,
                          now_ms=NOW, last_session_ms=None)
    return sit, _grounder(store)


def _prompt(stimuli: Any, grounding: Grounding) -> str:
    sit = build_situation(unit="u", pending=stimuli, home_room=ROOM,
                          now_ms=NOW, last_session_ms=None)
    return render_agenda(
        sit.agenda.items, stimuli={s.id: s for s in stimuli},
        grounding=dataclasses.replace(grounding, room_labels={
            "signal:room-a": "Room A"}),
        now_ms=NOW, last_session_ms=None, tz=HK)


async def _append_journey(store: FakeObservationStore) -> dict[str, int]:
    """Poll create → share → room/pin/member → vote+close — every event an
    append through the one store, in arrival order. Returns row ids."""
    ids: dict[str, int] = {}

    async def put(name: str, obs: Observation) -> None:
        rid = await store.append(
            observation=obs, event_id=f"evt-{name}",
            source_digest=f"digest-{name}")
        assert rid is not None
        ids[name] = rid

    await put("poll_create", _obs(
        kind="poll", ref="p1", op="create",
        payload=PollPayload(
            question="Dinner Friday?",
            options=(("r", "ramen"), ("s", "sushi"))),
        ago=120, actor="+852111", actor_status="known", position=1))
    await put("share", _obs(
        kind="artifact", ref="art-1", op="create",
        payload=ArtifactPayload(artifact_kind="image", custody="declared"),
        ago=119, actor="+852111", actor_status="known", position=2))
    await put("room_topic", _obs(
        kind="room", ref="room", op="revise",
        payload=RoomPayload(fields={"topic": "planning"}),
        ago=110, actor="+852111", actor_status="known", position=3))
    await put("pin", _obs(
        kind="pin", ref="pins", op="add",
        payload=PinPayload(message=EditTarget(message_id="m-100")),
        ago=100, actor="+852111", actor_status="known", position=4))
    await put("member_join", Observation(
        object_kind="membership", scope=SCOPE, object_ref="members",
        operation="add",
        payload=MembershipPayload(role="member", state="member"),
        subject_ref="+852999",
        actor_id="+852999", actor_status="known",
        occurred_ms=NOW - 95 * MIN, received_ms=NOW - 95 * MIN + 5,
        time_quality="upstream", source_position=5, revision_scope="rev",
    ))
    await put("vote", _obs(
        kind="poll", ref="p1", op="add",
        payload=PollPayload(voter_ref="v1", option_ref="ramen"),
        ago=60, position=6))
    await put("close", _obs(
        kind="poll", ref="p1", op="revise",
        payload=PollPayload(state="closed", voter_count=1),
        ago=30, position=7))
    return ids


async def test_the_mixed_journey_reads_back_from_append_to_agenda():
    store = FakeObservationStore()
    ids = await _append_journey(store)

    # The folded head: the poll is recorded CLOSED (a source state, never a
    # settled activity — nothing here decides the plan happened).
    head = await store.current(scope=SCOPE, kind="poll", object_ref="p1")
    assert head is not None and head.state.get("state") == "closed"
    assert head.ordering == "ordered"

    # The stimulus family: one `observation` stimulus per row, grouped by
    # OBJECT KIND in one room-bound item — a vote/close needs no message.
    stimuli = [
        _stim(1, ids["poll_create"], "poll", 120),
        _stim(2, ids["vote"], "poll", 60),
        _stim(3, ids["close"], "poll", 30),
        _stim(4, ids["room_topic"], "room", 110),
        _stim(5, ids["pin"], "pin", 100),
        _stim(6, ids["member_join"], "membership", 95),
        _stim(7, ids["share"], "artifact", 119),
    ]
    sit, ground = _grounding(store, stimuli)
    g = await ground("u", sit.agenda.items, stimuli)

    assert set(g.observations) == {s.ref for s in stimuli}
    vote = g.observations[_stim(0, ids["vote"], "poll", 0).ref]
    assert vote.kind == "poll" and "vote" in vote.summary
    assert "p1" not in vote.handle  # the platform ref never reaches a prompt
    # The grounder names only actors the source named AND the roster knows.
    creator = g.observations[f"platform_observation:{ids['poll_create']}"]
    assert creator.actor == "Ada"
    # The unknown actor stays unnamed, never a guessed name.
    join = g.observations[f"platform_observation:{ids['member_join']}"]
    assert join.actor is None and join.actor_status == "known"

    # The Standing input: the room's folded objects beside the position.
    objects = g.source_objects["signal:room-a"]
    kinds = {o.kind for o in objects}
    assert {"poll", "room", "pin", "membership", "artifact"} <= kinds

    # What the model actually reads (the proof is the rendered prompt).
    prompt = _prompt(stimuli, g)
    assert "<observation" in prompt
    assert "source_object" in prompt and "closed" in prompt
    assert "3 poll source events in this room" in prompt
    assert "room-a" not in prompt and "acct" not in prompt  # R-18


async def test_replay_and_window_and_bound_stay_honest():
    store = FakeObservationStore()
    ids = await _append_journey(store)

    # REPLAY: the same source event appended again dedupes to no effect —
    # no doubled stimulus, no folded-twice head.
    again = await store.append(
        observation=_obs(kind="poll", ref="p1", op="create",
                         payload=PollPayload(question="Dinner Friday?",
                                             options=(("a", "ramen"),)),
                         ago=125, position=1),
        event_id="evt-poll_create", source_digest="digest-poll_create")
    assert again is None

    # THE WINDOW: `recent` is bounded — 40-entry eviction is the READ's own
    # limit, and the oldest row is what drops, never an arbitrary one.
    for i in range(45):
        await store.append(
            observation=_obs(kind="pin", ref="pins", op="add",
                             payload=PinPayload(message=EditTarget(message_id=f"m-{i}")),
                             ago=50 - i * 0.1),
            event_id=f"flood-{i}", source_digest=f"flood-{i}")
    window = await store.recent(scope=SCOPE, limit=40)
    assert len(window) == 40
    assert all(r.id != ids["poll_create"] for r in window)

    # THE BOUND: `as_of` at a limit says `more`, never a silent earlier point
    # dressed as the requested one.
    folded, more = await store.as_of(
        scope=SCOPE, kind="poll", object_ref="p1",
        through_id=ids["close"], limit=1)
    assert more is True and folded is not None and folded.applied == 1
    folded, more = await store.as_of(
        scope=SCOPE, kind="poll", object_ref="p1",
        through_id=ids["close"], limit=100)
    assert more is False and folded is not None
    assert folded.state.get("state") == "closed"

    # CROSS-SCOPE: an id of another scope never resolves through this read.
    other = SourceScope(platform="signal", account_key="acct",
                        conversation_id="room-b")
    assert await store.by_ids(scope=other, ids=(ids["close"],)) == []
    assert await store.heads(scope=other) == []


async def test_ordering_gap_and_anonymous_actor_are_stated_not_smoothed():
    store = FakeObservationStore()
    base = await store.append(
        observation=_obs(kind="poll", ref="p1", op="create",
                         payload=PollPayload(question="Q?", options=(("a", "A"),)), ago=60,
                         position=5),
        event_id="e1", source_digest="d1")
    # An OLDER revision arriving after a newer one is retained and marked —
    # a gap, never silently applied over the newer state.
    stale = await store.append(
        observation=_obs(kind="poll", ref="p1", op="revise",
                         payload=PollPayload(state="open"), ago=10,
                         position=2),
        event_id="e2", source_digest="d2")
    assert base is not None and stale is not None
    head = await store.current(scope=SCOPE, kind="poll", object_ref="p1")
    assert head is not None and head.ordering == "gap"

    # An ANONYMOUS actor: the row carries the status, the grounder names
    # nobody, the prompt says anonymous — never an invented member.
    vote = await store.append(
        observation=_obs(kind="poll", ref="p1", op="add",
                         payload=PollPayload(voter_ref="v2", option_ref="a",
                                             anonymous=True),
                         ago=5, actor_status="anonymous", position=6),
        event_id="e3", source_digest="d3")
    assert vote is not None
    s = _stim(1, vote, "poll", 5)
    sit, ground = _grounding(store, [s])
    g = await ground("u", sit.agenda.items, [s])
    event = g.observations[s.ref]
    assert event.actor is None and event.actor_status == "anonymous"
    prompt = _prompt([s], g)
    assert 'actor="anonymous"' in prompt


async def test_a_stimulus_whose_row_is_gone_is_unreadable_not_guessed():
    store = FakeObservationStore()
    s = _stim(1, 999, "poll", 5)  # no such observation row
    sit = build_situation(unit="u", pending=[s], home_room=ROOM,
                          now_ms=NOW, last_session_ms=None)
    g = await _grounder(store)("u", sit.agenda.items, [s])
    assert g.observations == {}
    prompt = _prompt([s], g)
    assert "<observation" not in prompt
    (item,) = items(prompt)
    assert item.attrib["unreadable"] == "1"
