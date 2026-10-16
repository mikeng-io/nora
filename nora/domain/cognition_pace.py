"""The pace verbs, pure. `wake_at`/`wake_move`/`wake_cancel` write a `self`
signal (a due time for the unit's own next look; nothing leaves for a room),
not a tier, so this module checks the guards below and nothing more: no
schema, no mode, no lineage.

**Guards, in code:**

- at most `max_self_wakes` LIVE pending per unit (default 5): live means not
  fired, not cancelled and not superseded (`fold_self_wakes`);
- at most `max_self_wakes_per_day` `wake_at` calls per unit in a rolling 24 h
  (default 12, DEC-242: a session that re-arms itself every pass is a wedge
  the pending cap alone does not bound, because a fired wake frees its slot);
- one per subject: a new self-wake sharing any subject row with a pending
  one SUPERSEDES it (never refused; a normal `created` transition that
  carries `supersedes`);
- a horizon no shorter than the unit's cooldown and no longer than 7 days;
- `wake_move`/`wake_cancel` name a wake_ref this adaptor itself created and
  that is still pending (own misreads only, the rule `remind_move`/
  `remind_cancel` already follow).

This module makes no store call: every fact it needs (how many self-wakes
are pending, which existing one shares a subject, whether a wake_ref is
known and still pending) is a caller-supplied argument, the same "prior is a
parameter" shape `nora.domain.standing_revise` uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Default cap on live pending self-wakes per unit.
DEFAULT_MAX_SELF_WAKES = 5
#: Default longest horizon a self-wake may be set or moved to.
DEFAULT_MAX_HORIZON_SECONDS = 7 * 24 * 3600.0

#: DEC-242: default for `[cognition] max_self_wakes_per_day`.
DEFAULT_MAX_SELF_WAKES_PER_DAY = 12

#: `wake_fire` is CODE's verb, never a model's: the session that picked a due
#: self-wake up onto its agenda records that the wake fired (counted as
#: `nora_self_wake_total{outcome="fired"}`), which frees its pending slot.
PACE_VERBS: tuple[str, ...] = ("wake_at", "wake_move", "wake_cancel", "wake_fire")
PACE_TRANSITIONS: tuple[str, ...] = ("created", "moved", "cancelled", "fired")


@dataclass(frozen=True, slots=True)
class PendingSelfWake:
    """What the caller already knows about one pending self-wake, read from
    `cognition_self_wake`'s newest transition per `wake_ref` for the unit —
    this module does not read it."""

    wake_ref: str
    subject_rows: tuple[str, ...]
    created_by_session_id: int | None
    #: The `self` stimulus currently standing for it (retracted when the wake
    #: is cancelled, moved or superseded), and when it is due.
    stimulus_id: int | None = None
    at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class PaceRow:
    """One recorded pace transition, as `fold_self_wakes` reads it. The
    ledger's own row shape, restated here so this module imports nothing."""

    wake_ref: str | None
    transition: str
    outcome: str
    subject_rows: tuple[str, ...] = ()
    session_id: int | None = None
    stimulus_id: int | None = None
    at_ms: int | None = None
    supersedes: str | None = None
    #: The row's `stimulus_id` has been retracted (the store reads it with its
    #: one retraction predicate; this module does not read).
    retracted: bool = False


#: How far back `pending_self_wakes` reads a unit's transitions: the horizon a
#: self-wake may be set or moved to, plus a day. A ref with no transition in
#: that window is a day past its latest due time: fired, or stranded by a unit
#: that could not wake, and in neither case still worth a pending slot.
PENDING_WINDOW_SECONDS = DEFAULT_MAX_HORIZON_SECONDS + 24 * 3600.0


def fold_self_wakes(rows: Sequence[PaceRow]) -> tuple[PendingSelfWake, ...]:
    """The LIVE pending self-wakes from a unit's transition rows, oldest
    first. Per `wake_ref` the newest transition decides: `created` and `moved`
    are pending; `cancelled` and `fired` are not; a `created` row that names a
    `supersedes` ends the ref it displaces. A refused row changes nothing. A
    ref whose standing stimulus was retracted is not pending (a cancel whose
    ledger row failed to write after its retraction did)."""
    live: dict[str, PendingSelfWake] = {}
    retracted: dict[str, bool] = {}
    for row in rows:
        if row.outcome != "recorded" or row.wake_ref is None:
            continue
        if row.transition == "created":
            live[row.wake_ref] = PendingSelfWake(
                wake_ref=row.wake_ref, subject_rows=row.subject_rows,
                created_by_session_id=row.session_id, stimulus_id=row.stimulus_id,
                at_ms=row.at_ms,
            )
            retracted[row.wake_ref] = row.retracted
            if row.supersedes is not None:
                live.pop(row.supersedes, None)
        elif row.transition == "moved":
            was = live.get(row.wake_ref)
            if was is not None:
                live[row.wake_ref] = PendingSelfWake(
                    wake_ref=was.wake_ref, subject_rows=was.subject_rows,
                    created_by_session_id=was.created_by_session_id,
                    stimulus_id=row.stimulus_id, at_ms=row.at_ms,
                )
                retracted[row.wake_ref] = row.retracted
        elif row.transition in ("cancelled", "fired"):
            live.pop(row.wake_ref, None)
    return tuple(w for ref, w in live.items() if not retracted.get(ref, False))


@dataclass(frozen=True, slots=True)
class PaceRefusal:
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PaceAnswer:
    """What a recorded pace call answers: the ledger row, and the refusal when
    the call was refused (so the tool can tell the model WHY, not just record
    it)."""

    row_id: int
    refusal_reason: str | None = None
    refusal_detail: str | None = None

    @property
    def refused(self) -> bool:
        return self.refusal_reason is not None


def _overlap(a: Sequence[str], b: Sequence[str]) -> bool:
    return bool(set(a) & set(b))


def check_wake_at(
    *, subject_rows: Sequence[str], at_ms: int, now_ms: int,
    cooldown_seconds: float, pending: Sequence[PendingSelfWake],
    max_self_wakes: int = DEFAULT_MAX_SELF_WAKES,
    max_horizon_seconds: float = DEFAULT_MAX_HORIZON_SECONDS,
    created_today: int = 0,
    max_self_wakes_per_day: int = DEFAULT_MAX_SELF_WAKES_PER_DAY,
) -> tuple[PaceRefusal | None, str | None]:
    """Validate a `wake_at(at, subject_rows, why)` call.

    Returns `(refusal, supersedes)`: `refusal` set means refused; else
    `supersedes` names the pending wake_ref this call displaces (None when
    it creates a fresh one). `subject_rows` empty is refused outright — a
    self-wake with nothing to look at again is refused."""
    if not subject_rows:
        return PaceRefusal("no_subject", "wake_at needs at least one subject row"), None
    if created_today >= max_self_wakes_per_day:
        return (
            PaceRefusal(
                "daily_cap", "you have already asked for enough next looks lately",
            ),
            None,
        )
    horizon_seconds = (at_ms - now_ms) / 1000.0
    if horizon_seconds < cooldown_seconds:
        return (
            PaceRefusal(
                "horizon_too_soon", "that time is too soon after now to wake you at",
            ),
            None,
        )
    if horizon_seconds > max_horizon_seconds:
        return (
            PaceRefusal(
                "horizon_too_far", "that time is too far ahead to wake you at",
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
                "too_many_pending", "you already have enough next looks waiting",
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
    (own misreads only): `created_by_session_id`
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
        return PaceRefusal("not_own", "you may only move or cancel a next look you set yourself")
    horizon_seconds = (at_ms - now_ms) / 1000.0
    if horizon_seconds < cooldown_seconds:
        return PaceRefusal("horizon_too_soon", "that time is too soon after now to wake you at")
    if horizon_seconds > max_horizon_seconds:
        return PaceRefusal("horizon_too_far", "that time is too far ahead to wake you at")
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
        return PaceRefusal("not_own", "you may only move or cancel a next look you set yourself")
    return None
