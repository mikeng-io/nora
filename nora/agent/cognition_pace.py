"""Cognition's pace verbs, in DRY RUN/shadow (design/cognition-loop/
20-observe.md §3; migration step 3a-ii). The impure half of
`nora.domain.cognition_pace`: validate, then record — and, on a successful
`wake_at`/`wake_move`, append the `self` stimulus the ALREADY-LIVE wake
evaluator (`nora.app.wake.WakeEvaluator`, step 1) picks up with no change of
its own, because `nora.domain.stimuli._trigger` already fires on any
pending `self`-kind stimulus (20 §3's wake table, row "self").

**A self-wake is not an act.** Nothing here ever reaches a room: `wake_at`
writes a `self` signal, `wake_move`/`wake_cancel` only ever touch that
signal's own bookkeeping row. See `tests/architecture/
test_cognition_shadow_cannot_write_or_send.py` for what importing this
module is and is not allowed to reach.
"""

from __future__ import annotations

from collections.abc import Sequence

from nora.domain.cognition_pace import (
    PendingSelfWake,
    check_wake_at,
    check_wake_cancel,
    check_wake_move,
)
from nora.domain.ports import CognitionDryRunStore, NewCognitionPace, StimulusStore
from nora.domain.stimuli import NewStimulus

__all__ = ["dry_run_wake_at", "dry_run_wake_cancel", "dry_run_wake_move"]


async def _record(
    ledger: CognitionDryRunStore, *, session_id: int | None, unit: str, verb: str,
    wake_ref: str | None, transition: str, subject_rows: Sequence[str], why: str,
    at_ms: int | None, now_ms: int, outcome: str, refusal_reason: str | None = None,
    refusal_detail: str | None = None, stimulus_id: int | None = None,
    supersedes: str | None = None,
) -> int:
    return await ledger.record_pace(
        pace=NewCognitionPace(
            session_id=session_id, unit=unit, verb=verb, wake_ref=wake_ref,
            transition=transition, subject_rows=tuple(subject_rows), why=why, at_ms=at_ms,
            outcome=outcome, refusal_reason=refusal_reason, refusal_detail=refusal_detail,
            stimulus_id=stimulus_id, supersedes=supersedes,
        ),
        recorded_at_ms=now_ms,
    )


async def dry_run_wake_at(
    *, ledger: CognitionDryRunStore, stimuli: StimulusStore, session_id: int | None,
    unit: str, subject_rows: Sequence[str], at_ms: int, why: str, now_ms: int,
    wake_ref: str, cooldown_seconds: float, pending: Sequence[PendingSelfWake],
    max_self_wakes: int = 5, max_horizon_seconds: float = 7 * 24 * 3600.0,
) -> int:
    """`wake_at(at, subject_rows, why)`. `wake_ref` is minted by the caller
    (a session wiring, never guessed here) BEFORE this call — this module
    only validates and records against the id it is given, the same
    "prior is a parameter" shape every dry-run wrapper in this tree uses."""
    refusal, supersedes = check_wake_at(
        subject_rows=subject_rows, at_ms=at_ms, now_ms=now_ms,
        cooldown_seconds=cooldown_seconds, pending=pending,
        max_self_wakes=max_self_wakes, max_horizon_seconds=max_horizon_seconds,
    )
    if refusal is not None:
        return await _record(
            ledger, session_id=session_id, unit=unit, verb="wake_at", wake_ref=None,
            transition="created", subject_rows=subject_rows, why=why, at_ms=None,
            now_ms=now_ms, outcome="refused", refusal_reason=refusal.reason,
            refusal_detail=refusal.detail,
        )
    stimulus_id = await stimuli.append(
        stimulus=NewStimulus(
            unit=unit, kind="self", item=wake_ref, ref=f"self:{wake_ref}",
            at_ms=now_ms, due_ms=at_ms,
        ),
        recorded_at_ms=now_ms,
    )
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="wake_at", wake_ref=wake_ref,
        transition="created", subject_rows=subject_rows, why=why, at_ms=at_ms,
        now_ms=now_ms, outcome="recorded", stimulus_id=stimulus_id, supersedes=supersedes,
    )


async def dry_run_wake_move(
    *, ledger: CognitionDryRunStore, stimuli: StimulusStore, session_id: int | None,
    unit: str, wake_ref: str, at_ms: int, why: str, now_ms: int, cooldown_seconds: float,
    pending: Sequence[PendingSelfWake], created_by_session_id: int | None,
    subject_rows: Sequence[str] = (),
) -> int:
    """`wake_move(wake_id, at, why)`."""
    refusal = check_wake_move(
        wake_ref=wake_ref, at_ms=at_ms, now_ms=now_ms, cooldown_seconds=cooldown_seconds,
        pending=pending, created_by_session_id=created_by_session_id,
    )
    if refusal is not None:
        return await _record(
            ledger, session_id=session_id, unit=unit, verb="wake_move", wake_ref=wake_ref,
            transition="moved", subject_rows=subject_rows, why=why, at_ms=None,
            now_ms=now_ms, outcome="refused", refusal_reason=refusal.reason,
            refusal_detail=refusal.detail,
        )
    stimulus_id = await stimuli.append(
        stimulus=NewStimulus(
            unit=unit, kind="self", item=wake_ref, ref=f"self:{wake_ref}:{at_ms}",
            at_ms=now_ms, due_ms=at_ms,
        ),
        recorded_at_ms=now_ms,
    )
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="wake_move", wake_ref=wake_ref,
        transition="moved", subject_rows=subject_rows, why=why, at_ms=at_ms,
        now_ms=now_ms, outcome="recorded", stimulus_id=stimulus_id,
    )


async def dry_run_wake_cancel(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str, wake_ref: str,
    why: str, now_ms: int, pending: Sequence[PendingSelfWake],
    created_by_session_id: int | None, subject_rows: Sequence[str] = (),
) -> int:
    """`wake_cancel(wake_id, why)`. No new stimulus: cancelling asks for
    nothing further, so nothing is appended for the (already-live) evaluator
    to pick up."""
    refusal = check_wake_cancel(
        wake_ref=wake_ref, pending=pending, created_by_session_id=created_by_session_id,
    )
    outcome = "refused" if refusal is not None else "recorded"
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="wake_cancel", wake_ref=wake_ref,
        transition="cancelled", subject_rows=subject_rows, why=why, at_ms=None,
        now_ms=now_ms, outcome=outcome,
        refusal_reason=None if refusal is None else refusal.reason,
        refusal_detail=None if refusal is None else refusal.detail,
    )
