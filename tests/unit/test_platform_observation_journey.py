"""The platform-observation mixed journey, end to end over the fake Record.

docs/design/platform/observations.md's proof, bound by construction: the SAME
`fold_observation` the SQL head update runs, the grounder the session runs,
and `render_agenda` the model reads — one generated journey (poll create,
share, room/pin/member changes, a vote and a close that arrive with NO new
human message) walked from append to agenda row to Standing block.

Rooms, people and object refs are generated placeholders; nothing here is a
real room's or a production row.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

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
from nora.persons import Persons
from nora.testing.observations import FakeObservationStore

HK = ZoneInfo("Asia/Hong_Kong")
NOW = int(dt.datetime(2026, 9, 30, 15, 30, tzinfo=HK).timestamp() * 1000)
MIN = 60_000
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




_PERSONS = Persons(
    by_person={"ada": PersonRef(person_id="ada", name="Ada")},
    index={("signal", "+852111"): "ada"},
)








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




