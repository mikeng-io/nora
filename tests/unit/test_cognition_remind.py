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


def test_the_daily_cap_is_per_room_and_binds_at_the_cap_not_over_it():
    assert _check(RemindContext(acts_in_room_today=2, max_acts_per_room_per_day=3)) is None
    refusal = _check(RemindContext(acts_in_room_today=3, max_acts_per_room_per_day=3))
    assert refusal is not None and refusal.reason == "room_daily_cap"
    assert _check(RemindContext(acts_in_room_today=99, max_acts_per_room_per_day=None)) is None


def test_the_pending_ceiling_counts_every_pending_reminder_of_the_unit():
    other_room = PendingMirroredRemind(
        reminder_ref="r1", platform="signal", conversation_id="room-b",
        subject_rows=("note:9",), created_by_session_id=None,
    )
    assert _check(RemindContext(max_pending=2), pending=(other_room,)) is None
    refusal = _check(RemindContext(max_pending=1), pending=(other_room,))
    assert refusal is not None and refusal.reason == "pending_ceiling"


def test_a_duplicate_is_refused_before_a_cap_so_the_reason_is_the_specific_one():
    same = PendingMirroredRemind(
        reminder_ref="r1", platform="signal", conversation_id="room-a",
        subject_rows=("note:1",), created_by_session_id=None,
    )
    refusal = _check(
        RemindContext(max_pending=1, acts_in_room_today=9, max_acts_per_room_per_day=1),
        pending=(same,),
    )
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
