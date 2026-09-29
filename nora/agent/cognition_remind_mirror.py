"""`remind`/`remind_move`/`remind_cancel`, mirrored and held (impure half of
`nora.domain.cognition_remind`; design/cognition-loop/30-cognition.md §2;
migration step 3a-ii). Validate, then record — never send, never touch the
fire path or a real `reminders` row. See `tests/architecture/
test_cognition_shadow_cannot_write_or_send.py`.
"""

from __future__ import annotations

from collections.abc import Sequence

from nora.domain.cognition_remind import (
    PendingMirroredRemind,
    check_remind,
    check_remind_cancel,
    check_remind_move,
)
from nora.domain.ports import CognitionDryRunStore, NewCognitionRemindMirror

__all__ = ["dry_run_remind", "dry_run_remind_cancel", "dry_run_remind_move"]


async def _record(
    ledger: CognitionDryRunStore, *, session_id: int | None, unit: str, verb: str,
    reminder_ref: str | None, platform: str, conversation_id: str, intent: str | None,
    subject_rows: Sequence[str], at_ms: int | None, why: str | None, now_ms: int,
    outcome: str, refusal_reason: str | None = None, refusal_detail: str | None = None,
) -> int:
    return await ledger.record_remind_mirror(
        mirror=NewCognitionRemindMirror(
            session_id=session_id, unit=unit, verb=verb, reminder_ref=reminder_ref,
            platform=platform, conversation_id=conversation_id, intent=intent,
            subject_rows=tuple(subject_rows), at_ms=at_ms, why=why, outcome=outcome,
            refusal_reason=refusal_reason, refusal_detail=refusal_detail,
        ),
        recorded_at_ms=now_ms,
    )


async def dry_run_remind(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    reminder_ref: str, platform: str, conversation_id: str, intent: str,
    subject_rows: Sequence[str], at_ms: int, now_ms: int,
    pending: Sequence[PendingMirroredRemind],
) -> int:
    """`remind(at, room, brief)`. `reminder_ref` is minted by the caller
    before this call, the same "prior is a parameter" shape `wake_at`'s
    wrapper uses."""
    refusal = check_remind(
        subject_rows=subject_rows, intent=intent, platform=platform,
        conversation_id=conversation_id, pending=pending,
    )
    if refusal is not None:
        return await _record(
            ledger, session_id=session_id, unit=unit, verb="remind", reminder_ref=None,
            platform=platform, conversation_id=conversation_id, intent=intent,
            subject_rows=subject_rows, at_ms=None, why=None, now_ms=now_ms,
            outcome="refused", refusal_reason=refusal.reason,
            refusal_detail=refusal.detail,
        )
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="remind",
        reminder_ref=reminder_ref, platform=platform, conversation_id=conversation_id,
        intent=intent, subject_rows=subject_rows, at_ms=at_ms, why=None, now_ms=now_ms,
        outcome="recorded",
    )


async def dry_run_remind_move(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    reminder_ref: str, at_ms: int, why: str, platform: str, conversation_id: str,
    now_ms: int, pending: Sequence[PendingMirroredRemind],
    created_by_session_id: int | None,
) -> int:
    refusal = check_remind_move(
        reminder_ref=reminder_ref, pending=pending,
        created_by_session_id=created_by_session_id,
    )
    outcome = "refused" if refusal is not None else "recorded"
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="remind_move",
        reminder_ref=reminder_ref, platform=platform, conversation_id=conversation_id,
        intent=None, subject_rows=(), at_ms=None if refusal else at_ms, why=why,
        now_ms=now_ms, outcome=outcome,
        refusal_reason=None if refusal is None else refusal.reason,
        refusal_detail=None if refusal is None else refusal.detail,
    )


async def dry_run_remind_cancel(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    reminder_ref: str, why: str, platform: str, conversation_id: str, now_ms: int,
    pending: Sequence[PendingMirroredRemind], created_by_session_id: int | None,
) -> int:
    refusal = check_remind_cancel(
        reminder_ref=reminder_ref, pending=pending,
        created_by_session_id=created_by_session_id,
    )
    outcome = "refused" if refusal is not None else "recorded"
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="remind_cancel",
        reminder_ref=reminder_ref, platform=platform, conversation_id=conversation_id,
        intent=None, subject_rows=(), at_ms=None, why=why, now_ms=now_ms,
        outcome=outcome, refusal_reason=None if refusal is None else refusal.reason,
        refusal_detail=None if refusal is None else refusal.detail,
    )
