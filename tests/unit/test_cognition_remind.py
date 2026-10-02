"""`nora.domain.cognition_remind` (pure) and `nora.agent.
cognition_remind_mirror` (impure wrapper) — `remind`/`remind_move`/
`remind_cancel`, mirrored and held, migration step 3a-ii
(design/cognition-loop/30-cognition.md §2).
"""

from __future__ import annotations

import pytest

from nora.agent.cognition_remind_mirror import (
    dry_run_remind,
    dry_run_remind_cancel,
    dry_run_remind_move,
)
from nora.domain.cognition_remind import (
    PendingMirroredRemind,
    RemindContext,
    check_remind,
    check_remind_cancel,
    check_remind_move,
)
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore

NOW = 1_800_000_000_000


def test_remind_refuses_with_no_subject_rows():
    refusal = check_remind(
        subject_rows=(), intent="tell", platform="signal", conversation_id="room-a",
        pending=(),
    )
    assert refusal is not None and refusal.reason == "no_citations"


def test_remind_refuses_a_bad_intent():
    refusal = check_remind(
        subject_rows=("note:1",), intent="shout", platform="signal",
        conversation_id="room-a", pending=(),
    )
    assert refusal is not None and refusal.reason == "bad_intent"


def test_remind_refuses_an_exact_duplicate_pending():
    pending = (
        PendingMirroredRemind(
            reminder_ref="r1", platform="signal", conversation_id="room-a",
            subject_rows=("note:1",), created_by_session_id=1,
        ),
    )
    refusal = check_remind(
        subject_rows=("note:1",), intent="tell", platform="signal",
        conversation_id="room-a", pending=pending,
    )
    assert refusal is not None and refusal.reason == "duplicate_pending"


def test_remind_admits_the_same_room_with_different_subject_rows():
    """The dedupe key is (room, subject rows) TOGETHER — a room match alone
    must not refuse a genuinely different reminder in the same room."""
    pending = (
        PendingMirroredRemind(
            reminder_ref="r1", platform="signal", conversation_id="room-a",
            subject_rows=("note:1",), created_by_session_id=1,
        ),
    )
    refusal = check_remind(
        subject_rows=("note:2",), intent="tell", platform="signal",
        conversation_id="room-a", pending=pending,
    )
    assert refusal is None


def test_remind_admits_the_same_subject_in_a_different_room():
    pending = (
        PendingMirroredRemind(
            reminder_ref="r1", platform="signal", conversation_id="room-a",
            subject_rows=("note:1",), created_by_session_id=1,
        ),
    )
    refusal = check_remind(
        subject_rows=("note:1",), intent="tell", platform="signal",
        conversation_id="room-b", pending=pending,
    )
    assert refusal is None


def test_remind_move_refuses_an_unknown_ref():
    refusal = check_remind_move(reminder_ref="ghost", pending=(), created_by_session_id=1)
    assert refusal is not None and refusal.reason == "not_found"


def test_remind_move_refuses_a_reminder_this_session_did_not_write():
    pending = (
        PendingMirroredRemind(
            reminder_ref="r1", platform="signal", conversation_id="room-a",
            subject_rows=("note:1",), created_by_session_id=7,
        ),
    )
    refusal = check_remind_move(reminder_ref="r1", pending=pending, created_by_session_id=9)
    assert refusal is not None and refusal.reason == "not_own"


def test_remind_cancel_admits_its_own_pending_reminder():
    pending = (
        PendingMirroredRemind(
            reminder_ref="r1", platform="signal", conversation_id="room-a",
            subject_rows=("note:1",), created_by_session_id=7,
        ),
    )
    assert check_remind_cancel(reminder_ref="r1", pending=pending, created_by_session_id=7) is None


@pytest.fixture
def ledger() -> InMemoryCognitionDryRunStore:
    return InMemoryCognitionDryRunStore()


async def test_dry_run_remind_records_one_row(ledger):
    await dry_run_remind(
        ledger=ledger, session_id=1, unit="u", reminder_ref="r1", platform="signal",
        conversation_id="room-a", intent="tell", subject_rows=("note:1",),
        at_ms=NOW + 60_000, now_ms=NOW, pending=(),
    )
    assert len(ledger.remind_mirrors) == 1
    _, mirror, _ = ledger.remind_mirrors[0]
    assert mirror.outcome == "recorded"
    assert mirror.reminder_ref == "r1"


async def test_dry_run_remind_refused_carries_no_ref(ledger):
    await dry_run_remind(
        ledger=ledger, session_id=1, unit="u", reminder_ref="r1", platform="signal",
        conversation_id="room-a", intent="tell", subject_rows=(),
        at_ms=NOW + 60_000, now_ms=NOW, pending=(),
    )
    _, mirror, _ = ledger.remind_mirrors[0]
    assert mirror.outcome == "refused"
    assert mirror.reminder_ref is None


async def test_dry_run_remind_move_refuses_a_reminder_not_its_own(ledger):
    pending = (
        PendingMirroredRemind(
            reminder_ref="r1", platform="signal", conversation_id="room-a",
            subject_rows=("note:1",), created_by_session_id=7,
        ),
    )
    await dry_run_remind_move(
        ledger=ledger, session_id=9, unit="u", reminder_ref="r1", at_ms=NOW + 60_000,
        why="pushed back", platform="signal", conversation_id="room-a", now_ms=NOW,
        pending=pending, created_by_session_id=9,
    )
    _, mirror, _ = ledger.remind_mirrors[0]
    assert mirror.outcome == "refused"
    assert mirror.refusal_reason == "not_own"


async def test_dry_run_remind_cancel_records_recorded_for_its_own(ledger):
    pending = (
        PendingMirroredRemind(
            reminder_ref="r1", platform="signal", conversation_id="room-a",
            subject_rows=("note:1",), created_by_session_id=7,
        ),
    )
    await dry_run_remind_cancel(
        ledger=ledger, session_id=7, unit="u", reminder_ref="r1", why="no longer needed",
        platform="signal", conversation_id="room-a", now_ms=NOW, pending=pending,
        created_by_session_id=7,
    )
    _, mirror, _ = ledger.remind_mirrors[0]
    assert mirror.outcome == "recorded"
    assert mirror.verb == "remind_cancel"


# -- the code-side act guards (30 §3): refused with a named reason ------------------

def _check(context=None, *, intent="tell", rows=("note:1",), pending=()):
    return check_remind(
        subject_rows=rows, intent=intent, platform="signal", conversation_id="room-a",
        pending=pending, context=context,
    )


def test_a_remind_with_no_context_is_unchanged():
    assert _check() is None and _check(RemindContext()) is None


def test_the_rooms_proactive_flag_refuses_with_its_own_reason():
    refusal = _check(RemindContext(room_proactive=False))
    assert refusal is not None and refusal.reason == "room_not_proactive"


def test_a_tell_on_an_abduced_row_is_refused_and_an_ask_is_not():
    abduced = RemindContext(abduced_subject=True)
    assert _check(abduced, intent="tell").reason == "abduced_must_ask"       # type: ignore[union-attr]
    assert _check(abduced, intent="ask") is None


def test_the_pending_ceiling_is_the_live_one_and_counts_the_live_rows_and_the_mirrored_ones():
    mirrored = PendingMirroredRemind(
        reminder_ref="r1", platform="signal", conversation_id="room-b",
        subject_rows=("note:9",), created_by_session_id=None,
    )
    # live rows 3 + mirrored 1 = 4 held; the ceiling is `>=`, as the live tool's is
    assert _check(RemindContext(max_pending=5, live_pending=3), pending=(mirrored,)) is None
    refusal = _check(RemindContext(max_pending=4, live_pending=3), pending=(mirrored,))
    assert refusal is not None and refusal.reason == "pending_ceiling"
    assert _check(RemindContext(max_pending=None, live_pending=99), pending=(mirrored,)) is None
    only_live = _check(RemindContext(max_pending=3, live_pending=3))
    assert only_live is not None and only_live.reason == "pending_ceiling"


def test_there_is_no_daily_cap_because_the_live_remind_has_none():
    assert "acts_in_room_today" not in RemindContext.__dataclass_fields__
    assert "max_acts_per_room_per_day" not in RemindContext.__dataclass_fields__


MIN_LEAD, HORIZON = 120, 365


def _timed(delta_ms, **over):
    return _check(RemindContext(
        now_ms=NOW, at_ms=NOW + delta_ms, min_lead_seconds=MIN_LEAD, max_horizon_days=HORIZON,
        **over,
    ))


def test_a_moment_in_the_past_or_now_is_refused_past_as_the_live_tool_does():
    for delta in (-3_600_000, 0):
        refusal = _timed(delta)
        assert refusal is not None and refusal.reason == "past"


def test_a_moment_nearer_than_the_live_lead_is_too_soon_and_the_lead_itself_is_not():
    refusal = _timed(1_000)
    assert refusal is not None and refusal.reason == "too_soon"
    assert _timed(MIN_LEAD * 1000 - 1).reason == "too_soon"     # type: ignore[union-attr]
    assert _timed(MIN_LEAD * 1000) is None


def test_a_moment_further_than_the_live_horizon_is_too_far_and_the_horizon_itself_is_not():
    day = 86_400_000
    assert _timed(HORIZON * day) is None
    refusal = _timed(HORIZON * day + 1)
    assert refusal is not None and refusal.reason == "too_far"
    ten_years = _timed(3650 * day)
    assert ten_years is not None and ten_years.reason == "too_far"


def test_no_time_refusal_states_a_number():
    for delta in (-1, 1_000, 3650 * 86_400_000):
        refusal = _timed(delta)
        assert refusal is not None and not any(ch.isdigit() for ch in refusal.detail)


def test_a_missing_clock_leaves_the_time_checks_out_and_a_missing_lead_only_its_own():
    assert _check(RemindContext(at_ms=NOW, min_lead_seconds=MIN_LEAD)) is None
    assert _check(RemindContext(now_ms=NOW, at_ms=NOW + 1)) is None          # no lead, no horizon
    assert _check(RemindContext(now_ms=NOW, at_ms=NOW - 9)).reason == "past"  # type: ignore[union-attr]


def test_a_duplicate_is_refused_before_the_ceiling_so_the_reason_is_the_specific_one():
    same = PendingMirroredRemind(
        reminder_ref="r1", platform="signal", conversation_id="room-a",
        subject_rows=("note:1",), created_by_session_id=None,
    )
    refusal = _check(RemindContext(max_pending=1, live_pending=9), pending=(same,))
    assert refusal is not None and refusal.reason == "duplicate_pending"


async def test_the_wrapper_records_a_context_refusal_as_a_refused_row_with_its_reason():
    ledger = InMemoryCognitionDryRunStore()
    answer = await dry_run_remind(
        ledger=ledger, session_id=1, unit="u", reminder_ref="r", platform="signal",
        conversation_id="room-a", intent="tell", subject_rows=("note:1",),
        at_ms=NOW + 1000, now_ms=NOW, pending=(), context=RemindContext(room_proactive=False),
    )
    assert answer.refusal_reason == "room_not_proactive"
    [(_, row, _)] = ledger.remind_mirrors
    assert (row.outcome, row.refusal_reason) == ("refused", "room_not_proactive")
