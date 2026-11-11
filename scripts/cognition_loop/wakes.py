"""The eval's wake: the sandbox's own view, recorded the production way.

`decide` computes the claim a tick would from the sandbox's `UnitView`.
When it does not fire — a no-change wake, or a brake the candidate's own
divergence caused — the recorded case still says the unit woke: the eval
synthesises a full-claim verdict over what the view shows, marks the
wake's evidence `forced`, and the divergence is in the record, not
hidden. The wake row is written by `WakeEvaluator.record`, production's
own write path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nora.domain.stimuli import UnitView, Verdict, WakeReason, decide


@dataclass(frozen=True, slots=True)
class EvalWake:
    wake_id: int
    verdict: Verdict
    #: True when `decide` would not have fired and the eval recorded the
    #: recorded wake's claim anyway — a declared divergence.
    forced: bool


def eval_verdict(
    view: UnitView, decided: Verdict, *, reason: WakeReason,
) -> tuple[Verdict, bool]:
    """`decide`'s claim when it fires; otherwise every pending stimulus the
    view shows, so the wake the case recorded still sees what it saw."""
    if decided.reason is not None:
        return decided, False
    pending = tuple(s.id for s in view.pending)
    through = max(
        [view.watermark.through_stimulus_id, *pending], default=0,
    )
    return Verdict(
        reason=reason,
        through_stimulus_id=through,
        stimulus_count=len(pending),
        eligible_count=len(pending),
        oldest_pending_ms=min(
            (s.pending_since_ms for s in view.pending), default=None,
        ),
        claimed_ids=frozenset(pending),
        unclaimed_ids=tuple(
            s.id for s in view.pending if s.kind in view.inert_kinds
        ),
    ), True


async def record_eval_wake(
    evaluator: Any, view: UnitView, verdict: Verdict,
    reason: WakeReason, at_ms: int, *, forced: bool,
) -> EvalWake:
    """The wake row, written by `WakeEvaluator.record` — the write `tick`
    uses, claims and guard included."""
    wake_id = await evaluator.record(view, verdict, reason, at_ms)
    if wake_id is None:
        raise RuntimeError(
            "the sandbox's own write refused its wake: the view changed "
            "between read and write inside one eval pass"
        )
    return EvalWake(wake_id=wake_id, verdict=verdict, forced=forced)


def verdict_for(view: UnitView, policy: Any, now_ms: int) -> Verdict:
    return decide(view, policy, now_ms)
