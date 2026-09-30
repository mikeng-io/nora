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
    DEFAULT_MAX_SELF_WAKES_PER_DAY,
    PaceAnswer,
    PendingSelfWake,
    check_wake_at,
    check_wake_cancel,
    check_wake_move,
)
from nora.domain.ports import CognitionDryRunStore, NewCognitionPace, StimulusStore
from nora.domain.stimuli import NewStimulus

__all__ = ["dry_run_wake_at", "dry_run_wake_cancel", "dry_run_wake_fire", "dry_run_wake_move"]


async def _retract(
    stimuli: StimulusStore, *, unit: str, stimulus_id: int | None, reason: str, now_ms: int,
) -> None:
    """Retract the `self` stimulus a cancelled, moved or superseded self-wake
    left behind, so the evaluator's pending read never counts it (20 §3: a
    cancelled self-wake must not fire). Nothing to retract when the row never
    had a stimulus."""
    if stimulus_id is None:
        return
    await stimuli.retract(
        unit=unit, stimulus_id=stimulus_id, reason=reason, recorded_at_ms=now_ms,
    )


async def _record(
    ledger: CognitionDryRunStore, *, session_id: int | None, unit: str, verb: str,
    wake_ref: str | None, transition: str, subject_rows: Sequence[str], why: str,
    at_ms: int | None, now_ms: int, outcome: str, refusal_reason: str | None = None,
    refusal_detail: str | None = None, stimulus_id: int | None = None,
    supersedes: str | None = None,
) -> PaceAnswer:
    row_id = await ledger.record_pace(
        pace=NewCognitionPace(
            session_id=session_id, unit=unit, verb=verb, wake_ref=wake_ref,
            transition=transition, subject_rows=tuple(subject_rows), why=why, at_ms=at_ms,
            outcome=outcome, refusal_reason=refusal_reason, refusal_detail=refusal_detail,
            stimulus_id=stimulus_id, supersedes=supersedes,
        ),
        recorded_at_ms=now_ms,
    )
    return PaceAnswer(row_id, refusal_reason, refusal_detail)


async def dry_run_wake_at(
    *, ledger: CognitionDryRunStore, stimuli: StimulusStore, session_id: int | None,
    unit: str, subject_rows: Sequence[str], at_ms: int, why: str, now_ms: int,
    wake_ref: str, cooldown_seconds: float, pending: Sequence[PendingSelfWake],
    max_self_wakes: int = 5, max_horizon_seconds: float = 7 * 24 * 3600.0,
    created_today: int = 0, max_self_wakes_per_day: int = DEFAULT_MAX_SELF_WAKES_PER_DAY,
) -> PaceAnswer:
    """`wake_at(at, subject_rows, why)`. `wake_ref` is minted by the caller
    (a session wiring, never guessed here) BEFORE this call — this module
    only validates and records against the id it is given, the same
    "prior is a parameter" shape every dry-run wrapper in this tree uses."""
    refusal, supersedes = check_wake_at(
        subject_rows=subject_rows, at_ms=at_ms, now_ms=now_ms,
        cooldown_seconds=cooldown_seconds, pending=pending,
        max_self_wakes=max_self_wakes, max_horizon_seconds=max_horizon_seconds,
        created_today=created_today, max_self_wakes_per_day=max_self_wakes_per_day,
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
    if supersedes is not None:
        # The displaced self-wake's stimulus is retracted (20 §3: a new
        # self-wake sharing a subject row SUPERSEDES the pending one), or it
        # would still fire beside its replacement.
        displaced = next((p for p in pending if p.wake_ref == supersedes), None)
        await _retract(
            stimuli, unit=unit, reason="superseded", now_ms=now_ms,
            stimulus_id=None if displaced is None else displaced.stimulus_id,
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
) -> PaceAnswer:
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
    # The OLD stimulus is retracted: a move replaces the due time, it does not
    # add a second wake at the old one (20 §3: «versioned transitions»).
    old = next((p for p in pending if p.wake_ref == wake_ref), None)
    await _retract(
        stimuli, unit=unit, reason="moved", now_ms=now_ms,
        stimulus_id=None if old is None else old.stimulus_id,
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
    *, ledger: CognitionDryRunStore, stimuli: StimulusStore, session_id: int | None,
    unit: str, wake_ref: str, why: str, now_ms: int, pending: Sequence[PendingSelfWake],
    created_by_session_id: int | None, subject_rows: Sequence[str] = (),
) -> PaceAnswer:
    """`wake_cancel(wake_id, why)`. No new stimulus: cancelling asks for
    nothing further. It RETRACTS the standing `self` stimulus, so the
    evaluator never wakes on a self-wake that was called off."""
    refusal = check_wake_cancel(
        wake_ref=wake_ref, pending=pending, created_by_session_id=created_by_session_id,
    )
    outcome = "refused" if refusal is not None else "recorded"
    if refusal is None:
        row = next((p for p in pending if p.wake_ref == wake_ref), None)
        await _retract(
            stimuli, unit=unit, reason="cancelled", now_ms=now_ms,
            stimulus_id=None if row is None else row.stimulus_id,
        )
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="wake_cancel", wake_ref=wake_ref,
        transition="cancelled", subject_rows=subject_rows, why=why, at_ms=None,
        now_ms=now_ms, outcome=outcome,
        refusal_reason=None if refusal is None else refusal.reason,
        refusal_detail=None if refusal is None else refusal.detail,
    )


async def dry_run_wake_fire(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str, wake_ref: str,
    now_ms: int,
) -> PaceAnswer:
    """The `fired` transition: a session's agenda picked this self-wake up.
    Code's record, never a model's verb; it frees the pending slot."""
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="wake_fire", wake_ref=wake_ref,
        transition="fired", subject_rows=(), why="due", at_ms=None, now_ms=now_ms,
        outcome="recorded",
    )
