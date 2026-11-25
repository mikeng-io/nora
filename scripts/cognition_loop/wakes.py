"""Replay legacy wakes with the sandbox's own view and guarded row writer.

`decide` computes the claim a tick would from the sandbox's `UnitView`.
When it does not fire — a no-change wake, or a brake the candidate's own
divergence caused — the recorded case still says the unit woke: the eval
synthesises a full-claim verdict over what the view shows, marks the
wake's evidence `forced` so the divergence is in the record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nora.app.wake import WakeEvaluator
from nora.config import CognitionConfig
from nora.domain.ports import StimulusStore
from nora.domain.stimuli import UnitView, Verdict, WakePolicy, WakeReason, decide
from nora.obs.metrics import Metrics


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
    stimuli: StimulusStore, view: UnitView, reason: WakeReason, at_ms: int, *,
    config: CognitionConfig, metrics: Metrics,
) -> EvalWake:
    """Write the legacy wake row with its claim and guard."""
    policy = _replay_policy(config)
    verdict, forced = eval_verdict(view, decide(view, policy, at_ms), reason=reason)
    evaluator = WakeEvaluator(
        store=stimuli, units=lambda: (view.unit,), policy=policy,
        metrics=metrics, clock=lambda: at_ms / 1000,
        store_timeout_seconds=config.store_timeout_seconds,
    )
    wake_id = await evaluator.record(view, verdict, verdict.reason or reason, at_ms)
    if wake_id is None:
        raise RuntimeError(
            "the sandbox's own write refused its wake: the view changed "
            "between read and write inside one eval pass"
        )
    return EvalWake(wake_id=wake_id, verdict=verdict, forced=forced)


def _replay_policy(config: CognitionConfig) -> WakePolicy:
    return WakePolicy(
        quiet_seconds=config.quiet_seconds,
        cue_quiet_seconds=config.cue_quiet_seconds,
        batch_max=config.batch_max,
        max_age_seconds=config.max_age_seconds,
        cooldown_seconds=config.cooldown_seconds,
        max_concurrent_sessions=config.max_concurrent_sessions,
        session_timeout_seconds=config.session_timeout_seconds,
        backoff_max_seconds=config.backoff_max_seconds,
        max_consecutive_failures=config.max_consecutive_failures,
    )


def verdict_for(view: UnitView, policy: Any, now_ms: int) -> Verdict:
    return decide(view, policy, now_ms)
