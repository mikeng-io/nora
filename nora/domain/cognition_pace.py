"""The pace verbs, pure (design/cognition-loop/20-observe.md §3's
self-wakes; migration step 3a-ii). `wake_at`/`wake_move`/`wake_cancel` write
a `self` signal, not a tier — 30-cognition.md §2: "a `self` signal due at
`at` ([20] §3); nothing leaves for a room" — so this module checks the
guards 20 §3 states and nothing more: no schema, no mode, no lineage
(30-cognition.md §3: "the pace verbs ... carry no schema, mode or lineage:
they write a signal, not a tier").

**Guards, in code** (20 §3):

- at most `max_self_wakes` pending per unit (starting at 5);
- one per subject: a new self-wake sharing any subject row with a pending
  one SUPERSEDES it (never refused — this is a normal `created` transition
  that carries `supersedes`);
- a horizon no shorter than the unit's cooldown and no longer than 7 days;
- `wake_move`/`wake_cancel` name a wake_ref this adaptor itself created and
  that is still pending (30-cognition.md §3 rule 12, "own misreads only",
  generalised to the pace verbs the same rule already names for `remind_
  move`/`remind_cancel`).

This module makes no store call: every fact it needs (how many self-wakes
are pending, which existing one shares a subject, whether a wake_ref is
known and still pending) is a caller-supplied argument, the same "prior is a
parameter" shape `nora.domain.standing_revise` and `nora.agent.
cognition_dry_run` already use.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: 20 §3: "starting at 5".
DEFAULT_MAX_SELF_WAKES = 5
#: 20 §3: "no longer than 7 days".
DEFAULT_MAX_HORIZON_SECONDS = 7 * 24 * 3600.0

PACE_VERBS: tuple[str, ...] = ("wake_at", "wake_move", "wake_cancel")
PACE_TRANSITIONS: tuple[str, ...] = ("created", "moved", "cancelled")


@dataclass(frozen=True, slots=True)
class PendingSelfWake:
    """What the caller already knows about one pending self-wake, read from
    `cognition_self_wake`'s newest transition per `wake_ref` for the unit —
    this module does not read it."""

    wake_ref: str
    subject_rows: tuple[str, ...]
    created_by_session_id: int | None


@dataclass(frozen=True, slots=True)
class PaceRefusal:
    reason: str
    detail: str = ""


def _overlap(a: Sequence[str], b: Sequence[str]) -> bool:
    return bool(set(a) & set(b))


def check_wake_at(
    *, subject_rows: Sequence[str], at_ms: int, now_ms: int,
    cooldown_seconds: float, pending: Sequence[PendingSelfWake],
    max_self_wakes: int = DEFAULT_MAX_SELF_WAKES,
    max_horizon_seconds: float = DEFAULT_MAX_HORIZON_SECONDS,
) -> tuple[PaceRefusal | None, str | None]:
    """Validate a `wake_at(at, subject_rows, why)` call.

    Returns `(refusal, supersedes)`: `refusal` set means refused; else
    `supersedes` names the pending wake_ref this call displaces (None when
    it creates a fresh one). `subject_rows` empty is refused outright — a
    self-wake with nothing to look at again is not what 20 §3 describes."""
    if not subject_rows:
        return PaceRefusal("no_subject", "wake_at needs at least one subject row"), None
    horizon_seconds = (at_ms - now_ms) / 1000.0
    if horizon_seconds < cooldown_seconds:
        return (
            PaceRefusal(
                "horizon_too_soon",
                f"{horizon_seconds:.0f}s is inside the cooldown ({cooldown_seconds:.0f}s)",
            ),
            None,
        )
    if horizon_seconds > max_horizon_seconds:
        return (
            PaceRefusal(
                "horizon_too_far",
                f"{horizon_seconds:.0f}s exceeds the {max_horizon_seconds:.0f}s horizon",
            ),
            None,
        )
    supersedes = next(
        (p.wake_ref for p in pending if _overlap(p.subject_rows, subject_rows)), None,
    )
    # A superseded wake frees its own slot, so the count that matters is
    # every OTHER pending self-wake plus this one.
    effective_count = len(pending) - (1 if supersedes is not None else 0) + 1
    if effective_count > max_self_wakes:
        return (
            PaceRefusal(
                "too_many_pending",
                f"{max_self_wakes} self-wakes are already pending for this unit",
            ),
            None,
        )
    return None, supersedes


def check_wake_move(
    *, wake_ref: str, at_ms: int, now_ms: int, cooldown_seconds: float,
    pending: Sequence[PendingSelfWake], created_by_session_id: int | None,
    max_horizon_seconds: float = DEFAULT_MAX_HORIZON_SECONDS,
) -> PaceRefusal | None:
    """`wake_move(wake_id, at, why)`. Refused when `wake_ref` names no
    pending self-wake, or one this SESSION's own adaptor did not create
    (rule 12's "own misreads only", generalised) — `created_by_session_id`
    is the row's own recorded creator; `None` on either side means "not
    tracked" and is never treated as a match by construction (only an
    explicit equal session id passes)."""
    row = next((p for p in pending if p.wake_ref == wake_ref), None)
    if row is None:
        return PaceRefusal("not_found", f"no pending self-wake {wake_ref!r}")
    if (
        created_by_session_id is not None
        and row.created_by_session_id is not None
        and row.created_by_session_id != created_by_session_id
    ):
        return PaceRefusal("not_own", "this self-wake was not created by this session")
    horizon_seconds = (at_ms - now_ms) / 1000.0
    if horizon_seconds < cooldown_seconds:
        return PaceRefusal(
            "horizon_too_soon",
            f"{horizon_seconds:.0f}s is inside the cooldown ({cooldown_seconds:.0f}s)",
        )
    if horizon_seconds > max_horizon_seconds:
        return PaceRefusal(
            "horizon_too_far",
            f"{horizon_seconds:.0f}s exceeds the {max_horizon_seconds:.0f}s horizon",
        )
    return None


def check_wake_cancel(
    *, wake_ref: str, pending: Sequence[PendingSelfWake],
    created_by_session_id: int | None,
) -> PaceRefusal | None:
    """`wake_cancel(wake_id, why)`. Same ownership rule as `wake_move`."""
    row = next((p for p in pending if p.wake_ref == wake_ref), None)
    if row is None:
        return PaceRefusal("not_found", f"no pending self-wake {wake_ref!r}")
    if (
        created_by_session_id is not None
        and row.created_by_session_id is not None
        and row.created_by_session_id != created_by_session_id
    ):
        return PaceRefusal("not_own", "this self-wake was not created by this session")
    return None
