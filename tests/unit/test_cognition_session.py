"""`nora.domain.cognition_session` — the Cognition adaptor's session and
agenda, built by code."""

from __future__ import annotations

from nora.domain.cognition_session import (
    CLOSE_REASONS,
    AgendaItem,
    Room,
    build_agenda,
    build_situation,
    close_or_strike,
)
from nora.domain.stimuli import Stimulus

HOME = Room("signal", "home-room")


def _stimulus(
    id: int, *, kind: str = "message", item: str = "", ref: str = "",
    platform: str | None = "signal", conversation_id: str | None = "room-a",
    cues: tuple[str, ...] = (), recorded_at_ms: int = 1000,
) -> Stimulus:
    return Stimulus(
        id=id, unit="unit-1", kind=kind, item=item or f"item-{id}", ref=ref or f"ref-{id}",
        at_ms=recorded_at_ms, recorded_at_ms=recorded_at_ms,
        platform=platform, conversation_id=conversation_id, cues=cues,
    )


def test_an_empty_pending_list_is_an_empty_agenda():
    agenda = build_agenda([], home_room=HOME)
    assert agenda.is_empty
    assert agenda.items == ()
    assert agenda.deferred_stimulus_ids == ()


def test_message_stimuli_in_one_room_become_one_item_bound_to_that_room():
    pending = [_stimulus(1), _stimulus(2), _stimulus(3)]
    agenda = build_agenda(pending, home_room=HOME)
    assert len(agenda.items) == 1
    item = agenda.items[0]
    assert item.kind == "message"
    assert item.room == Room("signal", "room-a")
    assert item.stimulus_ids == (1, 2, 3)
    assert "3" in item.summary
    # A summary is prompt text: the room is named by the item's label, never
    # its conversation id (R-18, m5).
    assert "room-a" not in item.summary


def test_message_stimuli_in_different_rooms_become_separate_items():
    pending = [
        _stimulus(1, conversation_id="room-a"),
        _stimulus(2, conversation_id="room-b"),
    ]
    agenda = build_agenda(pending, home_room=HOME)
    rooms = {item.room.conversation_id for item in agenda.items}
    assert rooms == {"room-a", "room-b"}


def test_play_rollup_time_and_self_bind_the_home_room_not_a_stimulus_room():
    for kind in ("play", "rollup", "time", "self"):
        pending = [_stimulus(1, kind=kind, conversation_id="room-a", platform="signal")]
        agenda = build_agenda(pending, home_room=HOME)
        assert agenda.items[0].room == HOME, kind


def test_a_social_read_binds_the_room_it_names_and_home_when_it_names_none():
    """A social stimulus names a room of the unit that lists its author
    (`composition.social_person_rooms`): its item reads through that room, the
    only kind of room that may be shown the author's items."""
    named = build_agenda(
        [_stimulus(1, kind="social", conversation_id="room-b", platform="signal")],
        home_room=HOME,
    )
    assert named.items[0].room == Room("signal", "room-b")
    unnamed = build_agenda(
        [_stimulus(1, kind="social", conversation_id=None, platform=None)], home_room=HOME,
    )
    assert unnamed.items[0].room == HOME


def test_a_summary_states_a_fact_never_a_conclusion():
    """An item states what happened, never what to conclude from it: the summary text
    carries no cue CONTENT, only a count, so it can never accidentally read as a
    judgement."""
    pending = [_stimulus(1, cues=("date", "keep_instruction"))]
    agenda = build_agenda(pending, home_room=HOME)
    item = agenda.items[0]
    assert "date" not in item.summary
    assert "keep_instruction" not in item.summary
    assert item.cues == ("date", "keep_instruction")


def test_a_cued_item_outranks_an_uncued_item_of_greater_volume():
    quiet_but_cued = [_stimulus(1, conversation_id="room-a", cues=("date",))]
    loud_but_uncued = [
        _stimulus(i, conversation_id="room-b") for i in range(2, 12)
    ]
    agenda = build_agenda(quiet_but_cued + loud_but_uncued, home_room=HOME)
    assert agenda.items[0].room.conversation_id == "room-a"


def test_max_items_caps_the_agenda_and_defers_the_rest():
    pending = [
        _stimulus(i, conversation_id=f"room-{i}") for i in range(1, 6)
    ]
    agenda = build_agenda(pending, home_room=HOME, max_items=2)
    assert len(agenda.items) == 2
    kept_ids = {sid for item in agenda.items for sid in item.stimulus_ids}
    deferred_ids = set(agenda.deferred_stimulus_ids)
    assert kept_ids | deferred_ids == {1, 2, 3, 4, 5}
    assert kept_ids.isdisjoint(deferred_ids)


def test_ordering_is_deterministic_across_repeated_calls():
    pending = [_stimulus(i, conversation_id=f"room-{i % 3}") for i in range(1, 9)]
    first = build_agenda(pending, home_room=HOME)
    second = build_agenda(list(reversed(pending)), home_room=HOME)
    assert [item.room for item in first.items] == [item.room for item in second.items]


def test_build_situation_wraps_the_agenda_with_the_units_clock():
    situation = build_situation(
        unit="unit-1", pending=[_stimulus(1)], home_room=HOME,
        now_ms=5000, last_session_ms=1000,
    )
    assert situation.unit == "unit-1"
    assert situation.now_ms == 5000
    assert situation.last_session_ms == 1000
    assert len(situation.agenda.items) == 1
    as_dict = situation.as_dict()
    assert as_dict["unit"] == "unit-1"
    assert len(as_dict["agenda"]) == 1


def test_an_agenda_item_serialises_to_json_shapes_only():
    import json

    pending = [_stimulus(1, cues=("date",))]
    item = build_agenda(pending, home_room=HOME).items[0]
    json.dumps(item.as_dict())  # raises on anything not JSON-native


# -- closing --------------------------------------------------------------

def _item(strike: int = 0) -> AgendaItem:
    return AgendaItem(
        kind="message", room=HOME, summary="s", stimulus_ids=(1,), cues=(),
        surprise=1.0, strike=strike,
    )


def test_written_no_change_and_covered_never_strike():
    for reason in ("written", "no_change", "covered"):
        outcome = close_or_strike(_item(strike=0), item_index=0, reason=reason)
        assert outcome.reason == reason
        assert outcome.strike == 0


def test_waiting_strikes_and_escalates_to_struck_at_max_strikes():
    item = _item(strike=0)
    first = close_or_strike(item, item_index=0, reason="waiting", max_strikes=3)
    assert first.reason == "waiting"
    assert first.strike == 1

    second = close_or_strike(_item(strike=1), item_index=0, reason="waiting", max_strikes=3)
    assert second.reason == "waiting"
    assert second.strike == 2

    third = close_or_strike(_item(strike=2), item_index=0, reason="waiting", max_strikes=3)
    assert third.reason == "struck"
    assert third.strike == 3


def test_close_or_strike_refuses_an_unknown_reason():
    import pytest

    with pytest.raises(ValueError):
        close_or_strike(_item(), item_index=0, reason="nonsense")


def test_close_or_strike_refuses_struck_as_a_caller_supplied_reason():
    """`struck` is code's OWN close at `max_strikes`, never a session's
    argument (`CLOSE_REASONS`'s docstring) — a caller asking to close
    `struck` directly is refused even though `CloseOutcome` itself would
    accept it as a value."""
    import pytest

    with pytest.raises(ValueError):
        close_or_strike(_item(), item_index=0, reason="struck")


def test_close_reasons_names_every_reason_the_schema_allows():
    assert set(CLOSE_REASONS) == {"written", "no_change", "waiting", "covered", "struck"}


def test_agenda_is_a_dataclass_not_a_mutable_list():
    # Agenda/AgendaItem are frozen — a session's row is a fixed replay
    # input, never mutated after it is built.
    import dataclasses

    import pytest

    item = _item()
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.strike = 5  # type: ignore[misc]


def test_deferred_ids_are_sorted():
    # Groups sort by their OWN oldest id (the surprise tie-break), not by
    # every id they hold — so two deferred groups, each internally sorted,
    # can still FLATTEN out of order: group "room-p" holds ids (5, 50),
    # group "room-q" holds ids (6, 7); ordered by oldest id, "room-p" (5)
    # comes before "room-q" (6), so the unsorted concatenation would read
    # (5, 50, 6, 7) — only an explicit final sort produces (5, 6, 7, 50).
    # Equal surprise across all three groups (two stimuli each), so the
    # ordering tie-break — oldest id — is the only thing that decides rank.
    kept = [_stimulus(1, conversation_id="room-a"), _stimulus(2, conversation_id="room-a")]
    deferred_p = [_stimulus(5, conversation_id="room-p"), _stimulus(50, conversation_id="room-p")]
    deferred_q = [_stimulus(6, conversation_id="room-q"), _stimulus(7, conversation_id="room-q")]
    agenda = build_agenda(kept + deferred_p + deferred_q, home_room=HOME, max_items=1)
    assert agenda.deferred_stimulus_ids == (5, 6, 7, 50)


def test_the_look_ahead_binds_to_its_events_room_and_the_floor_to_home():
    """DEC-303: an `upcoming` time stimulus names the room its event is in and
    is bound there (the session reads and reminds in that room); the floor,
    which names none, stays on the home room."""
    pending = [
        _stimulus(1, kind="time", item="floor", platform=None, conversation_id=None),
        _stimulus(2, kind="time", item="upcoming", conversation_id="room-b"),
    ]
    items = {item.room: item for item in build_agenda(pending, home_room=HOME).items}
    assert set(items) == {HOME, Room("signal", "room-b")}
    ahead = items[Room("signal", "room-b")]
    assert ahead.kind == "time" and ahead.stimulus_ids == (2,)
    assert "coming up" in ahead.summary and "room-b" not in ahead.summary
    # A time stimulus that is not the look ahead never binds to a room it names.
    other = build_agenda(
        [_stimulus(3, kind="time", item="floor", conversation_id="room-c")], home_room=HOME,
    )
    assert other.items[0].room == HOME
