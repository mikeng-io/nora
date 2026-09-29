"""`nora.domain.cognition_pace` (pure) and `nora.agent.cognition_pace`
(impure wrapper) — the self-wake pace verbs, migration step 3a-ii
(design/cognition-loop/20-observe.md §3).
"""

from __future__ import annotations

import pytest

from nora.agent.cognition_pace import dry_run_wake_at, dry_run_wake_cancel, dry_run_wake_move
from nora.domain.cognition_pace import (
    PendingSelfWake,
    check_wake_at,
    check_wake_cancel,
    check_wake_move,
)
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
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
    rather than counting as a sixth (20 §3)."""
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
def ledger() -> InMemoryCognitionDryRunStore:
    return InMemoryCognitionDryRunStore()


@pytest.fixture
def stimuli() -> InMemoryStimulusStore:
    return InMemoryStimulusStore()


async def test_dry_run_wake_at_records_recorded_and_appends_a_self_stimulus(ledger, stimuli):
    await dry_run_wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=("event:1",),
        at_ms=NOW + 3_600_000, why="check the venue", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    assert len(ledger.paces) == 1
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "recorded"
    assert pace.wake_ref == "w1"
    assert pace.stimulus_id is not None
    view = await stimuli.view(unit="u", mode="shadow", now_ms=NOW + 3_600_000, limit=10)
    assert any(s.kind == "self" and s.item == "w1" for s in view.pending)


async def test_dry_run_wake_at_refused_appends_no_stimulus(ledger, stimuli):
    await dry_run_wake_at(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", subject_rows=(),
        at_ms=NOW + 3_600_000, why="", now_ms=NOW, wake_ref="w1",
        cooldown_seconds=COOLDOWN, pending=(),
    )
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "refused"
    assert pace.stimulus_id is None
    view = await stimuli.view(unit="u", mode="shadow", now_ms=NOW + 3_600_000, limit=10)
    assert view.pending == ()


async def test_dry_run_wake_cancel_appends_no_new_stimulus(ledger, stimuli):
    pending = (PendingSelfWake(wake_ref="w1", subject_rows=("e:1",), created_by_session_id=1),)
    await dry_run_wake_cancel(
        ledger=ledger, session_id=1, unit="u", wake_ref="w1", why="done", now_ms=NOW,
        pending=pending, created_by_session_id=1,
    )
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "recorded"
    assert pace.transition == "cancelled"
    view = await stimuli.view(unit="u", mode="shadow", now_ms=NOW, limit=10)
    assert view.pending == ()


async def test_dry_run_wake_move_records_and_reappends_a_self_stimulus(ledger, stimuli):
    pending = (PendingSelfWake(wake_ref="w1", subject_rows=("e:1",), created_by_session_id=1),)
    await dry_run_wake_move(
        ledger=ledger, stimuli=stimuli, session_id=1, unit="u", wake_ref="w1",
        at_ms=NOW + 7_200_000, why="pushed back", now_ms=NOW, cooldown_seconds=COOLDOWN,
        pending=pending, created_by_session_id=1,
    )
    _, pace, _ = ledger.paces[0]
    assert pace.outcome == "recorded"
    assert pace.transition == "moved"
    assert pace.stimulus_id is not None
