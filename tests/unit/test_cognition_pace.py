"""`nora.domain.cognition_pace` (pure) and `nora.agent.cognition_pace`
(impure wrapper) — the self-wake pace verbs.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.cognition_pace import (
    PartialPaceWriteError,
    wake_at,
    wake_cancel,
    wake_fire,
    wake_move,
)
from nora.domain.cognition_pace import (
    PaceRow,
    PendingSelfWake,
    check_wake_at,
    check_wake_cancel,
    check_wake_move,
    fold_self_wakes,
)
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000
COOLDOWN = 300.0


# --------------------------------------------------------------------------
# pure checks
# --------------------------------------------------------------------------

def test_wake_at_refuses_with_no_subject_rows():
    refusal, supersedes = check_wake_at(
        subject_rows=(), at_ms=NOW + 3_600_000, now_ms=NOW, cooldown_seconds=COOLDOWN,
        pending=(),
    )
    assert refusal is not None and refusal.reason == "no_subject"
    assert supersedes is None


def test_wake_at_refuses_a_horizon_inside_the_cooldown():
    refusal, _ = check_wake_at(
        subject_rows=("event:1",), at_ms=NOW + 60_000, now_ms=NOW,
        cooldown_seconds=COOLDOWN, pending=(),
    )
    assert refusal is not None and refusal.reason == "horizon_too_soon"


def test_wake_at_refuses_a_horizon_past_seven_days():
    refusal, _ = check_wake_at(
        subject_rows=("event:1",), at_ms=NOW + 8 * 24 * 3600 * 1000, now_ms=NOW,
        cooldown_seconds=COOLDOWN, pending=(),
    )
    assert refusal is not None and refusal.reason == "horizon_too_far"


def test_wake_at_refuses_past_max_self_wakes():
    pending = tuple(
        PendingSelfWake(wake_ref=f"w{i}", subject_rows=(f"event:{i}",), created_by_session_id=1)
        for i in range(5)
    )
    refusal, _ = check_wake_at(
        subject_rows=("event:99",), at_ms=NOW + 3_600_000, now_ms=NOW,
        cooldown_seconds=COOLDOWN, pending=pending, max_self_wakes=5,
    )
    assert refusal is not None and refusal.reason == "too_many_pending"


def test_wake_at_with_five_pending_still_admits_a_supersede():
    """A new self-wake sharing a subject with a pending one SUPERSEDES it
    rather than counting as a sixth."""
    pending = tuple(
        PendingSelfWake(wake_ref=f"w{i}", subject_rows=(f"event:{i}",), created_by_session_id=1)
        for i in range(5)
    )
    refusal, supersedes = check_wake_at(
        subject_rows=("event:2",), at_ms=NOW + 3_600_000, now_ms=NOW,
        cooldown_seconds=COOLDOWN, pending=pending, max_self_wakes=5,
    )
    assert refusal is None
    assert supersedes == "w2"


def test_wake_move_refuses_an_unknown_ref():
    refusal = check_wake_move(
        wake_ref="ghost", at_ms=NOW + 3_600_000, now_ms=NOW, cooldown_seconds=COOLDOWN,
        pending=(), created_by_session_id=1,
    )
    assert refusal is not None and refusal.reason == "not_found"


def test_wake_move_refuses_a_self_wake_created_by_another_session():
    pending = (PendingSelfWake(wake_ref="w1", subject_rows=("e:1",), created_by_session_id=7),)
    refusal = check_wake_move(
        wake_ref="w1", at_ms=NOW + 3_600_000, now_ms=NOW, cooldown_seconds=COOLDOWN,
        pending=pending, created_by_session_id=9,
    )
    assert refusal is not None and refusal.reason == "not_own"


def test_wake_cancel_refuses_a_self_wake_created_by_another_session():
    pending = (PendingSelfWake(wake_ref="w1", subject_rows=("e:1",), created_by_session_id=7),)
    refusal = check_wake_cancel(wake_ref="w1", pending=pending, created_by_session_id=9)
    assert refusal is not None and refusal.reason == "not_own"


def test_wake_cancel_admits_its_own_pending_self_wake():
    pending = (PendingSelfWake(wake_ref="w1", subject_rows=("e:1",), created_by_session_id=7),)
    assert check_wake_cancel(wake_ref="w1", pending=pending, created_by_session_id=7) is None


# --------------------------------------------------------------------------
# impure wrapper: records exactly one row, and appends a `self` stimulus
# the (already-live) wake evaluator can pick up
# --------------------------------------------------------------------------

@pytest.fixture
def ledger() -> InMemoryCognitionStore:
    return InMemoryCognitionStore()


@pytest.fixture
def stimuli(ledger: InMemoryCognitionStore) -> InMemoryStimulusStore:
    return InMemoryStimulusStore(ledger=ledger)


async def test_wake_at_records_recorded_and_appends_a_self_stimulus(ledger, stimuli):
    await wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
        at_ms=NOW + 3_600_000, why="check the venue", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    assert len(ledger.paces) == 1
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "recorded"
    assert pace.wake_ref == "w1"
    assert pace.stimulus_id is not None
    view = await stimuli.view(unit="u", mode="live", now_ms=NOW + 3_600_000, limit=10)
    assert any(s.kind == "self" and s.item == "w1" for s in view.pending)


async def test_wake_at_refused_appends_no_stimulus(ledger, stimuli):
    await wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=(),
        at_ms=NOW + 3_600_000, why="", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "refused"
    assert pace.stimulus_id is None
    view = await stimuli.view(unit="u", mode="live", now_ms=NOW + 3_600_000, limit=10)
    assert view.pending == ()


async def test_wake_cancel_appends_no_new_stimulus(ledger, stimuli):
    pending = (PendingSelfWake(wake_ref="w1", subject_rows=("e:1",), created_by_session_id=1),)
    await wake_cancel(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", wake_ref="w1", why="done",
        now_ms=NOW, pending=pending, created_by_session_id=1,
    )
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "recorded"
    assert pace.transition == "cancelled"
    view = await stimuli.view(unit="u", mode="live", now_ms=NOW, limit=10)
    assert view.pending == ()


async def test_wake_move_records_and_reappends_a_self_stimulus(ledger, stimuli):
    pending = (PendingSelfWake(wake_ref="w1", subject_rows=("e:1",), created_by_session_id=1),)
    await wake_move(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", wake_ref="w1",
        at_ms=NOW + 7_200_000, why="pushed back", now_ms=NOW, cooldown_seconds=COOLDOWN,
        pending=pending, created_by_session_id=1,
    )
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "recorded"
    assert pace.transition == "moved"
    assert pace.stimulus_id is not None


# --------------------------------------------------------------------------
# retraction, live pending, the fired transition, the daily cap
# --------------------------------------------------------------------------

def _created(ref: str, *, subjects=("e:1",), stim=1, at=NOW + 3_600_000, supersedes=None):
    return PaceRow(
        wake_ref=ref, transition="created", outcome="recorded", subject_rows=subjects,
        session_id=1, stimulus_id=stim, at_ms=at, supersedes=supersedes,
    )


def test_a_fired_or_cancelled_self_wake_is_not_pending():
    rows = [
        _created("w1", stim=1), _created("w2", subjects=("e:2",), stim=2),
        _created("w3", subjects=("e:3",), stim=3),
        PaceRow(wake_ref="w1", transition="fired", outcome="recorded"),
        PaceRow(wake_ref="w2", transition="cancelled", outcome="recorded"),
    ]
    assert [p.wake_ref for p in fold_self_wakes(rows)] == ["w3"]


def test_a_move_keeps_it_pending_at_its_new_stimulus_and_time():
    rows = [
        _created("w1", stim=1),
        PaceRow(wake_ref="w1", transition="moved", outcome="recorded", stimulus_id=9,
                at_ms=NOW + 9_000_000),
    ]
    (p,) = fold_self_wakes(rows)
    assert (p.stimulus_id, p.at_ms, p.subject_rows) == (9, NOW + 9_000_000, ("e:1",))


def test_a_superseding_create_ends_the_superseded_ref_and_a_refusal_changes_nothing():
    rows = [
        _created("w1", stim=1), _created("w2", stim=2, supersedes="w1"),
        PaceRow(wake_ref=None, transition="created", outcome="refused"),
    ]
    assert [p.wake_ref for p in fold_self_wakes(rows)] == ["w2"]


def test_the_pending_cap_counts_only_live_pending_wakes():
    """Five wakes that fired or were cancelled do not fill the cap."""
    dead = [
        row for n in range(5) for row in (
            _created(f"d{n}", subjects=(f"e:{n}",), stim=n + 1),
            PaceRow(wake_ref=f"d{n}", transition="fired", outcome="recorded"),
        )
    ]
    live = fold_self_wakes(dead)
    assert live == ()
    refusal, _ = check_wake_at(
        subject_rows=("event:new",), at_ms=NOW + 3_600_000, now_ms=NOW,
        cooldown_seconds=COOLDOWN, pending=live, max_self_wakes=5,
    )
    assert refusal is None


def test_wake_at_refuses_past_the_per_day_cap_and_admits_below_it():
    kw = {"subject_rows": ("event:1",), "at_ms": NOW + 3_600_000, "now_ms": NOW,
          "cooldown_seconds": COOLDOWN, "pending": (), "max_self_wakes_per_day": 3}
    refusal, _ = check_wake_at(created_today=3, **kw)
    assert refusal is not None and refusal.reason == "daily_cap"
    refusal, _ = check_wake_at(created_today=2, **kw)
    assert refusal is None


async def test_wake_cancel_retracts_the_standing_stimulus_so_nothing_wakes(ledger, stimuli):
    await wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
        at_ms=NOW + 3_600_000, why="w", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    (pending,) = await ledger.pending_self_wakes(unit="u", since_ms=0)
    assert pending.stimulus_id == 1
    later = NOW + 4_000_000
    view = await stimuli.view(unit="u", mode="live", now_ms=later, limit=10)
    assert [s.id for s in view.pending] == [1]

    answer = await wake_cancel(
        ledger=ledger, stimuli=stimuli, session_id=2, unit="u", wake_ref="w1", why="done",
        now_ms=NOW, pending=(pending,), created_by_session_id=None,
    )
    assert not answer.refused
    view = await stimuli.view(unit="u", mode="live", now_ms=later, limit=10)
    assert view.pending == ()
    assert await ledger.pending_self_wakes(unit="u", since_ms=0) == ()


async def test_wake_move_retracts_the_old_stimulus_and_supersede_retracts_the_displaced(
    ledger, stimuli,
):
    await wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
        at_ms=NOW + 3_600_000, why="w", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    pending = await ledger.pending_self_wakes(unit="u", since_ms=0)
    await wake_move(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", wake_ref="w1",
        at_ms=NOW + 7_200_000, why="later", now_ms=NOW, cooldown_seconds=COOLDOWN,
        pending=pending, created_by_session_id=None,
    )
    view = await stimuli.view(unit="u", mode="live", now_ms=NOW + 9_000_000, limit=10)
    assert [(s.id, s.due_ms) for s in view.pending] == [(2, NOW + 7_200_000)]

    pending = await ledger.pending_self_wakes(unit="u", since_ms=0)
    await wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
        at_ms=NOW + 5_000_000, why="again", now_ms=NOW, wake_ref="w2",
        cooldown_seconds=COOLDOWN, pending=pending,
    )
    view = await stimuli.view(unit="u", mode="live", now_ms=NOW + 9_000_000, limit=10)
    assert [s.item for s in view.pending] == ["w2"]
    assert [p.wake_ref for p in await ledger.pending_self_wakes(unit="u", since_ms=0)] == ["w2"]


async def test_the_fired_transition_frees_the_pending_slot(ledger, stimuli):
    await wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
        at_ms=NOW + 3_600_000, why="w", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    assert len(await ledger.pending_self_wakes(unit="u", since_ms=0)) == 1
    await wake_fire(ledger=ledger, session_id=2, unit="u", wake_ref="w1", now_ms=NOW)
    assert await ledger.pending_self_wakes(unit="u", since_ms=0) == ()
    (_, fired, _) = ledger.paces[-1]
    assert (fired.verb, fired.transition, fired.outcome) == ("wake_fire", "fired", "recorded")


# --------------------------------------------------------------------------
# write order: no failure loses a self-wake or leaves one firing that was called off
# --------------------------------------------------------------------------

async def _set_w1(ledger: InMemoryCognitionStore, stimuli: InMemoryStimulusStore) -> Any:
    await wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
        at_ms=NOW + 3_600_000, why="w", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    return await ledger.pending_self_wakes(unit="u", since_ms=0)


async def _due(stimuli: InMemoryStimulusStore) -> list[tuple[str, int | None]]:
    view = await stimuli.view(unit="u", mode="live", now_ms=NOW + 9_000_000, limit=10)
    return [(s.item, s.due_ms) for s in view.pending]


async def test_a_move_whose_append_fails_leaves_the_old_time_live(ledger, stimuli):
    """The move APPENDS before it retracts. An append that fails
    leaves the self-wake live at its old time; retract-first would lose it."""
    pending = await _set_w1(ledger, stimuli)
    stimuli.fail_on.add("append")
    with pytest.raises(RuntimeError):
        await wake_move(
            ledger=ledger, stimuli=stimuli, session_id=2, unit="u", wake_ref="w1",
            at_ms=NOW + 7_200_000, why="later", now_ms=NOW, cooldown_seconds=COOLDOWN,
            pending=pending, created_by_session_id=None,
        )
    assert await _due(stimuli) == [("w1", NOW + 3_600_000)]
    assert [p.stimulus_id for p in await ledger.pending_self_wakes(unit="u", since_ms=0)] == [1]


async def test_a_move_whose_retract_fails_takes_the_new_time_back(ledger, stimuli):
    """…and a retract that fails after the append takes the NEW stimulus back,
    so the self-wake is live once, at its old time, not twice."""
    pending = await _set_w1(ledger, stimuli)
    stimuli.fail_on.add("retract_self")
    with pytest.raises(RuntimeError):
        await wake_move(
            ledger=ledger, stimuli=stimuli, session_id=2, unit="u", wake_ref="w1",
            at_ms=NOW + 7_200_000, why="later", now_ms=NOW, cooldown_seconds=COOLDOWN,
            pending=pending, created_by_session_id=None,
        )
    assert await _due(stimuli) == [("w1", NOW + 3_600_000)]


async def test_a_move_whose_record_fails_still_fires_once_at_the_new_time(ledger, stimuli):
    pending = await _set_w1(ledger, stimuli)
    ledger.fail_on.add("record_pace")
    with pytest.raises(PartialPaceWriteError) as caught:
        await wake_move(
            ledger=ledger, stimuli=stimuli, session_id=2, unit="u", wake_ref="w1",
            at_ms=NOW + 7_200_000, why="later", now_ms=NOW, cooldown_seconds=COOLDOWN,
            pending=pending, created_by_session_id=None,
        )
    assert isinstance(caught.value.__cause__, RuntimeError)   # the store's own failure
    assert await _due(stimuli) == [("w1", NOW + 7_200_000)]


async def test_a_wake_at_whose_record_fails_leaves_no_orphan_stimulus(ledger, stimuli):
    """The stimulus is appended before the ledger row; a record that
    fails takes the stimulus back, so nothing fires that the ledger never
    knew of."""
    ledger.fail_on.add("record_pace")
    with pytest.raises(RuntimeError):
        await wake_at(
            ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
            at_ms=NOW + 3_600_000, why="w", now_ms=NOW, wake_ref="w1",
            cooldown_seconds=COOLDOWN, pending=(),
        )
    assert [s.kind for s in stimuli.stimuli] == ["self"]      # it was appended …
    assert await _due(stimuli) == []                         # … and taken back


async def test_a_superseding_wake_at_whose_record_fails_keeps_the_displaced_one(ledger, stimuli):
    pending = await _set_w1(ledger, stimuli)
    ledger.fail_on.add("record_pace")
    with pytest.raises(RuntimeError):
        await wake_at(
            ledger=ledger, stimuli=stimuli, session_id=2, unit="u", subject_rows=("event:1",),
            at_ms=NOW + 5_000_000, why="again", now_ms=NOW, wake_ref="w2",
            cooldown_seconds=COOLDOWN, pending=pending,
        )
    assert await _due(stimuli) == [("w1", NOW + 3_600_000)]


async def test_a_cancel_whose_record_fails_still_never_fires_and_reads_not_pending(
    ledger, stimuli,
):
    pending = await _set_w1(ledger, stimuli)
    ledger.fail_on.add("record_pace")
    with pytest.raises(PartialPaceWriteError) as caught:
        await wake_cancel(
            ledger=ledger, stimuli=stimuli, session_id=2, unit="u", wake_ref="w1",
            why="done", now_ms=NOW, pending=pending, created_by_session_id=None,
        )
    assert isinstance(caught.value.__cause__, RuntimeError)   # the store's own failure
    ledger.fail_on.clear()
    assert await _due(stimuli) == []
    assert await ledger.pending_self_wakes(unit="u", since_ms=0) == ()
