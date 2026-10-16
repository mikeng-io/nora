"""Cognition's pace verbs. The impure half of `nora.domain.cognition_pace`:
validate, then record — and, on a successful `wake_at`/`wake_move`, append the
`self` stimulus the wake evaluator (`nora.app.wake.WakeEvaluator`) picks up,
because `nora.domain.stimuli._trigger` fires on any pending `self`-kind
stimulus.

**Write order, so no failure loses a self-wake or leaves one that fires
after it was called off** (DEC-242). A self-wake is
live while a `self` stimulus stands for its `wake_ref` (`item`) unretracted;
the ledger row is its bookkeeping. `wake_at` appends, then records; if the
record fails the new stimulus is retracted before the error is raised, so no
stimulus fires that the ledger never knew of. `wake_move` APPENDS the new
stimulus, then retracts every other one standing for the ref (by ref, so a
straggler an earlier failure left is taken too), then records: a failure
before the retract leaves the old time live (and the new one is taken back), a
failure after it leaves the new time live, and never neither. `wake_cancel`
retracts, then records: a failed record leaves a ledger row that reads
pending while its stimulus is retracted, which `fold_self_wakes` reads as not
pending. A superseding `wake_at` retracts the displaced ref only after its own
row is recorded.

**A self-wake is not an act.** Nothing here ever reaches a room: `wake_at`
writes a `self` signal, `wake_move`/`wake_cancel` only ever touch that
signal's own bookkeeping row.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from nora.domain.cognition_pace import (
    DEFAULT_MAX_SELF_WAKES_PER_DAY,
    PaceAnswer,
    PendingSelfWake,
    check_wake_at,
    check_wake_cancel,
    check_wake_move,
)
from nora.domain.ports import CognitionStore, NewCognitionPace, StimulusStore
from nora.domain.stimuli import NewStimulus

log = logging.getLogger("nora.agent.cognition_pace")

__all__ = [
    "PartialPaceWriteError", "wake_at", "wake_cancel", "wake_fire", "wake_move",
]


class PartialPaceWriteError(Exception):
    """A pace verb changed the `self` stimulus and could not record that in the
    ledger (twice). The change STANDS (a cancelled wake no longer fires, a moved one
    fires at its new time), so the caller must not tell the model that nothing was
    written, and a retry must not be invited to do it again. `fold_self_wakes` reads a
    stimulus retracted without its ledger row as not pending, which is the state a
    retry then finds (`not_found`), so a retry cannot duplicate it."""


async def _record(
    ledger: CognitionStore, *, session_id: int | None, unit: str, verb: str,
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


async def _record_after_effect(ledger: CognitionStore, **record: Any) -> PaceAnswer:
    """`_record` for a verb whose `self` stimulus has ALREADY been changed. One more
    try on a failure, then `PartialPaceWriteError`: the change stands whether or not the
    ledger knows, and what the model is told must not claim otherwise."""
    try:
        return await _record(ledger, **record)
    except Exception:
        log.exception("could not record %s of %s; trying once more", record["verb"],
                      record["wake_ref"])
    try:
        return await _record(ledger, **record)
    except Exception as exc:
        raise PartialPaceWriteError(f"{record['verb']} of {record['wake_ref']}") from exc


async def _take_back(
    stimuli: StimulusStore, *, unit: str, stimulus_id: int | None, now_ms: int,
) -> None:
    """Retract a stimulus this call appended and could not account for. A
    failure here is swallowed: the caller raises the error that brought it
    here, which is the one the model and the log should see."""
    if stimulus_id is None:
        return
    try:
        await stimuli.retract(
            unit=unit, stimulus_id=stimulus_id, reason="unrecorded", recorded_at_ms=now_ms,
        )
    except Exception:
        log.exception("could not take back self stimulus %s", stimulus_id)


async def _record_or_take_back(
    ledger: CognitionStore, stimuli: StimulusStore, *, new_stimulus_id: int | None,
    **record: Any,
) -> PaceAnswer:
    """`_record` a transition that just appended `new_stimulus_id`; if the
    record fails, the stimulus is retracted before the error is raised, so a
    self-wake the ledger never knew of cannot fire."""
    try:
        return await _record(
            ledger, outcome="recorded", stimulus_id=new_stimulus_id, **record,
        )
    except Exception:
        await _take_back(
            stimuli, unit=record["unit"], stimulus_id=new_stimulus_id,
            now_ms=record["now_ms"],
        )
        raise


async def wake_at(
    *, ledger: CognitionStore, stimuli: StimulusStore, session_id: int | None,
    unit: str, subject_rows: Sequence[str], at_ms: int, why: str, now_ms: int,
    wake_ref: str, cooldown_seconds: float, pending: Sequence[PendingSelfWake],
    max_self_wakes: int = 5, max_horizon_seconds: float = 7 * 24 * 3600.0,
    created_today: int = 0, max_self_wakes_per_day: int = DEFAULT_MAX_SELF_WAKES_PER_DAY,
) -> PaceAnswer:
    """`wake_at(at, subject_rows, why)`. `wake_ref` is minted by the caller
    (a session wiring, never guessed here) BEFORE this call — this module
    only validates and records against the id it is given."""
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
    answer = await _record_or_take_back(
        ledger, stimuli, new_stimulus_id=stimulus_id, session_id=session_id, unit=unit,
        verb="wake_at", wake_ref=wake_ref, transition="created", subject_rows=subject_rows,
        why=why, at_ms=at_ms, now_ms=now_ms, supersedes=supersedes,
    )
    if supersedes is not None:
        # A new self-wake sharing a subject row SUPERSEDES the pending one, so
        # the displaced stimulus is retracted or it would fire beside its
        # replacement. After the record: a failed record must not take the old
        # one away with the new. And once recorded the new wake is a fact: a
        # failure here must not turn a created wake into an error (the model
        # would retry it and a second one would stand), so it is logged, and the
        # worst it leaves is the superseded wake firing once more.
        try:
            await stimuli.retract_self(
                unit=unit, wake_ref=supersedes, keep=None, reason="superseded",
                recorded_at_ms=now_ms,
            )
        except Exception:
            log.exception(
                "could not retract superseded self-wake %s; it may fire beside %s",
                supersedes, wake_ref,
            )
    return answer


async def wake_move(
    *, ledger: CognitionStore, stimuli: StimulusStore, session_id: int | None,
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
    # APPEND the new due time first, then retract the old one: a move replaces
    # the due time rather than adding a second wake, and a failure between the
    # two must leave one of them live, never neither.
    stimulus_id = await stimuli.append(
        stimulus=NewStimulus(
            unit=unit, kind="self", item=wake_ref, ref=f"self:{wake_ref}:{at_ms}",
            at_ms=now_ms, due_ms=at_ms,
        ),
        recorded_at_ms=now_ms,
    )
    try:
        await stimuli.retract_self(
            unit=unit, wake_ref=wake_ref, keep=stimulus_id, reason="moved",
            recorded_at_ms=now_ms,
        )
    except Exception:
        await _take_back(stimuli, unit=unit, stimulus_id=stimulus_id, now_ms=now_ms)
        raise
    return await _record_after_effect(
        ledger, session_id=session_id, unit=unit, verb="wake_move", wake_ref=wake_ref,
        transition="moved", subject_rows=subject_rows, why=why, at_ms=at_ms,
        now_ms=now_ms, outcome="recorded", stimulus_id=stimulus_id,
    )


async def wake_cancel(
    *, ledger: CognitionStore, stimuli: StimulusStore, session_id: int | None,
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
        await stimuli.retract_self(
            unit=unit, wake_ref=wake_ref, keep=None, reason="cancelled",
            recorded_at_ms=now_ms,
        )
    record = _record if refusal is not None else _record_after_effect
    return await record(
        ledger, session_id=session_id, unit=unit, verb="wake_cancel", wake_ref=wake_ref,
        transition="cancelled", subject_rows=subject_rows, why=why, at_ms=None,
        now_ms=now_ms, outcome=outcome,
        refusal_reason=None if refusal is None else refusal.reason,
        refusal_detail=None if refusal is None else refusal.detail,
    )


async def wake_fire(
    *, ledger: CognitionStore, session_id: int | None, unit: str, wake_ref: str,
    now_ms: int,
) -> PaceAnswer:
    """The `fired` transition: a session's agenda picked this self-wake up.
    Code's record, never a model's verb; it frees the pending slot."""
    return await _record(
        ledger, session_id=session_id, unit=unit, verb="wake_fire", wake_ref=wake_ref,
        transition="fired", subject_rows=(), why="due", at_ms=None, now_ms=now_ms,
        outcome="recorded",
    )
