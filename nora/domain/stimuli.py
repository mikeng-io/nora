"""Stimuli and the wake policy (design/cognition-loop/20-observe.md §3).

A **stimulus** is what ingestion, the Rollup or the clock raises to wake
cognition: `(unit, kind, ref, cues, at)`, one durable row per unit in the
`stimulus` table. A **wake** is the moment a unit's pending stimuli start a
session. This module holds the types and the one pure function that decides a
wake, so the rule is testable without a database or a clock.

**Migration step 1 runs it in SHADOW.** `decide` is called exactly as it would
be to start a Cognition session, every brake included, and its answer is
recorded as a would-wake (`cognition_wake`, `mode = "shadow"`) with its reason
and the stimulus range it would consume. Nothing is fired: there is no Cognition
adaptor yet, and the clocked Dreamer adaptors keep running as they did. The
week of would-wakes is how 20 §3's starting values get sized.

**The watermark is the wake log.** Each unit's durable watermark is the
`(through_stimulus_id, at_ms)` of its newest wake row in that mode, not a row
updated in place (10 §8: loop state is transition rows, the current value is
the newest). A stimulus is PENDING when it is due by the read's cutoff and not
consumed, and consumed means `id <= through_stimulus_id AND pending_since <=
at_ms` of that newest wake. Both bounds only ever grow — `decide` never sets
`through_stimulus_id` below the one it read, and `at_ms` is the read's cutoff,
which moves forward with the clock — so «consumed by some earlier wake» and
«consumed by the newest» are the same test, which is what lets one row carry
the watermark. The second bound is what keeps a `self` or `time`
stimulus due at 19:40 pending across a wake at 15:00 (20 §3: «pending only from
its due time»).

**Single-flight and cooldown are derived from durable rows.** With NO session
hook (`[cognition] session_mode = "off"`) no session runs, so a would-wake is
treated as a session that lasts `session_seconds` (a stated assumption): the
unit is in flight until `at + session_seconds`, then cools down for
`cooldown_seconds`. With a hook (step 3a-ii, `UnitView.tracked`) the assumption
is gone: a unit is in flight from its session's start to its END row
(`cognition_session_end`), bounded by `session_timeout_seconds` for a session
that never wrote one (a crash), and cools down from the END, not from the wake.
A restart recomputes all of it from the rows; nothing lives only in memory.

**The watermark advances only on a session that did not fail** (20 §3: «a
failed session does not advance it»). `UnitView.mark` is the newest wake whose
session, if it has one, closed or is still running; a wake whose session
failed, timed out, was cancelled, or ran out its timeout without an end row is
skipped, so its stimuli are pending again. `UnitView.carry` holds the stimuli a
CLOSED session returned to the next agenda with a strike (30 §2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from .cues import is_strong

#: 20 §3's stimulus kinds. Only `message` has a producer at step 1 (ingest), and
#: `time` (the daily floors, raised by the evaluator); `rollup` arrives with
#: step 2, `self` with step 3's pace verbs, `social`/`play` with step 5. The
#: evaluator handles all of them generically.
StimulusKind = Literal["message", "social", "play", "rollup", "time", "self"]
STIMULUS_KINDS: tuple[str, ...] = get_args(StimulusKind)

#: What a `message` stimulus's item was (20 §3: «`message` (including an edit,
#: reaction or delete)»); other kinds name their own producer's item.
MessageItem = Literal["message", "edit", "reaction", "delete"]

#: 20 §3's wake table, in the table's order — the order `decide` tries them
#: in and the label vocabulary of `nora_cognition_wake_total{reason}`.
WakeReason = Literal[
    "settled", "strong_cue", "rollup", "batch", "max_age", "time", "self",
]
WAKE_REASONS: tuple[str, ...] = get_args(WakeReason)

#: Why a unit with pending stimuli did NOT wake this tick.
WakeBrake = Literal["single_flight", "cooldown", "concurrency"]
WAKE_BRAKES: tuple[str, ...] = get_args(WakeBrake)

#: How a wake row was produced. `live` arrives with the Cognition adaptor.
WakeMode = Literal["shadow"]
WAKE_MODES: tuple[str, ...] = get_args(WakeMode)

#: The kinds raised by something a person or a source DID, as opposed to the
#: Rollup's derived changes and the clock's (and Cognition's own) due times.
_OBSERVED: frozenset[str] = frozenset({"message", "social", "play"})


@dataclass(frozen=True)
class NewStimulus:
    """One stimulus to append. `due_ms` is None for an item that is pending as
    soon as it is recorded; a `time` or `self` stimulus sets it."""

    unit: str
    kind: StimulusKind
    item: str
    ref: str
    at_ms: int
    platform: str | None = None
    conversation_id: str | None = None
    person_id: str | None = None
    cues: tuple[str, ...] = ()
    #: 20 §3: «a person's reply to her message within `reply_quiet_seconds`
    #: does not by itself wake Cognition: it waits for the next wake». Recorded
    #: and consumed like any other stimulus; it never trips a trigger.
    waits: bool = False
    due_ms: int | None = None


@dataclass(frozen=True)
class Stimulus:
    """A stimulus as stored. `pending_since_ms` is when it became pending:
    its due time if it has one, else when it was recorded."""

    id: int
    unit: str
    kind: str
    item: str
    ref: str
    at_ms: int
    recorded_at_ms: int
    platform: str | None = None
    conversation_id: str | None = None
    person_id: str | None = None
    cues: tuple[str, ...] = ()
    waits: bool = False
    due_ms: int | None = None

    @property
    def pending_since_ms(self) -> int:
        return self.due_ms if self.due_ms is not None else self.recorded_at_ms


@dataclass(frozen=True)
class WakeRow:
    """A recorded wake (in step 1, a would-wake). `through_stimulus_id` and
    `at_ms` ARE the unit's watermark once this is its newest row."""

    id: int
    unit: str
    mode: str
    reason: str
    from_stimulus_id: int
    through_stimulus_id: int
    stimulus_count: int
    eligible_count: int
    oldest_pending_ms: int | None
    cut_cooldown: bool
    at_ms: int
    recorded_at_ms: int
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NewWake:
    unit: str
    mode: WakeMode
    reason: WakeReason
    from_stimulus_id: int
    through_stimulus_id: int
    stimulus_count: int
    eligible_count: int
    oldest_pending_ms: int | None
    cut_cooldown: bool
    at_ms: int
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Watermark:
    """`(through_stimulus_id, through_ms)`: every stimulus with `id <=
    through_stimulus_id` and pending since no later than `through_ms` is
    consumed. `ZERO` is a unit that has never woken."""

    through_stimulus_id: int = 0
    through_ms: int = 0

    @classmethod
    def of(cls, wake: WakeRow | None) -> Watermark:
        return cls() if wake is None else cls(wake.through_stimulus_id, wake.at_ms)

    def consumed(self, stimulus: Stimulus) -> bool:
        return (
            stimulus.id <= self.through_stimulus_id
            and stimulus.pending_since_ms <= self.through_ms
        )


@dataclass(frozen=True)
class WakePolicy:
    """20 §3's numbers. Every value is a starting guess the shadow week sizes.

    `session_seconds` is not in 20 §3: it is how long a shadow would-wake is
    treated as running, so single-flight and cooldown can be exercised while
    nothing runs."""

    quiet_seconds: float = 1800.0  # operator 2026-09-29, was 600 ([cognition])
    cue_quiet_seconds: float = 120.0
    rollup_delay_seconds: float = 60.0
    batch_max: int = 80
    max_age_seconds: float = 3600.0
    cooldown_seconds: float = 300.0
    max_concurrent_sessions: int = 2
    session_seconds: float = 60.0
    #: The hard wall clock of a REAL session (30 §5: `AdaptorBudget`'s one
    #: timeout). A session row with no end row older than this is dead (a
    #: crash) and stops holding its unit.
    session_timeout_seconds: float = 900.0


SessionState = Literal["running", "closed", "failed"]


@dataclass(frozen=True)
class LastSession:
    """The session that belongs to a unit's NEWEST wake, as its rows say:
    `running` (started, no end row, not past the timeout), `closed`, or
    `failed` (an end row that is not `closed`, or no end row past the timeout)."""

    state: SessionState
    started_at_ms: int
    ended_at_ms: int | None = None


@dataclass(frozen=True)
class UnitView:
    """What one tick reads for one unit: its pending stimuli (due, not
    consumed, ascending id), when its rooms last saw a person's item, and its
    newest wake in this mode.

    `mark` is the watermark: the newest wake that did not fail (None: derive
    it from `last_wake`, the pre-session behaviour). `last_session`,
    `tracked` and `live` are the REAL session facts (see the module
    docstring); `carry` maps a stimulus id a closed session returned to the
    next agenda onto its strike."""

    unit: str
    pending: tuple[Stimulus, ...]
    last_activity_ms: int | None
    last_wake: WakeRow | None
    mark: Watermark | None = None
    last_session: LastSession | None = None
    tracked: bool = False
    live: bool = False
    carry: Mapping[int, int] = field(default_factory=dict)

    @property
    def watermark(self) -> Watermark:
        return self.mark if self.mark is not None else Watermark.of(self.last_wake)


class WakeHook(Protocol):
    """The ONE typed shape of the evaluator's `on_wake` extension point
    (`nora.app.wake.WakeEvaluator`) and of what fills it
    (`nora.app.cognition_session.CognitionSessionRunner.wake_hook`). Keyword
    only, so neither side can drift into a positional order the other does
    not share. `now_ms` is the tick's clock, the same instant the wake row
    was decided at."""

    def __call__(
        self, *, unit: str, view: UnitView, wake_id: int, now_ms: int,
    ) -> Awaitable[object]: ...


@dataclass(frozen=True)
class Verdict:
    """`reason` set: wake now, consuming `pending` up to `through_stimulus_id`.
    `reason` None and `brake` set: a trigger held but a brake stopped it.
    Both None: nothing holds."""

    reason: WakeReason | None = None
    brake: WakeBrake | None = None
    cut_cooldown: bool = False
    through_stimulus_id: int = 0
    stimulus_count: int = 0
    eligible_count: int = 0
    oldest_pending_ms: int | None = None


def in_flight(last_wake: WakeRow | None, policy: WakePolicy, now_ms: int) -> bool:
    """Whether the unit's last (would-)session is still running at `now_ms`,
    by the SHADOW assumption (`session_seconds`). Only meaningful with no
    session hook; `unit_in_flight` is the one the evaluator asks."""
    return (
        last_wake is not None
        and now_ms < last_wake.at_ms + int(policy.session_seconds * 1000)
    )


def unit_in_flight(view: UnitView, policy: WakePolicy, now_ms: int) -> bool:
    """Single-flight's question. Untracked (no hook): the shadow assumption.
    Tracked: a REAL session is in flight from its start to its end row, or
    (`live`) while its task is alive in this process, and never on wake age."""
    if not view.tracked:
        return in_flight(view.last_wake, policy, now_ms)
    if view.live:
        return True
    session = view.last_session
    return (
        session is not None
        and session.state == "running"
        and now_ms < session.started_at_ms + int(policy.session_timeout_seconds * 1000)
    )


def _cooling(view: UnitView, policy: WakePolicy, now_ms: int) -> bool:
    wake = view.last_wake
    if wake is None:
        return False
    cooldown_ms = int(policy.cooldown_seconds * 1000)
    if not view.tracked:
        return now_ms < wake.at_ms + int(policy.session_seconds * 1000) + cooldown_ms
    session = view.last_session
    if session is None:
        base = wake.at_ms
    elif session.ended_at_ms is not None:
        base = session.ended_at_ms
    else:
        base = session.started_at_ms + int(policy.session_timeout_seconds * 1000)
    return now_ms < base + cooldown_ms


def _trigger(view: UnitView, policy: WakePolicy, now_ms: int) -> WakeReason | None:
    """The FIRST of 20 §3's triggers that holds, in the table's order.

    Only stimuli that may wake count (`waits` False): a reply to Nora inside
    `reply_quiet_seconds` rides the next wake and never starts one. Quiet is
    measured from the unit's newest person-authored item, `waits` ones
    included: a room still replying to her is not a quiet room."""
    eligible = [s for s in view.pending if not s.waits]
    if not eligible:
        return None
    quiet_ms = (
        None if view.last_activity_ms is None else now_ms - view.last_activity_ms
    )

    def quiet_for(seconds: float) -> bool:
        return quiet_ms is None or quiet_ms >= seconds * 1000

    # `settled` and `strong cue` are about a ROOM going quiet after people
    # said something, so they count observed items only. A pending `time`,
    # `self` or `rollup` stimulus has its own row in the table; letting it read
    # as `settled` in a quiet unit would record every 07:00 floor under the
    # wrong reason and make the shadow week unreadable.
    observed = [s for s in eligible if s.kind in _OBSERVED]
    if observed and quiet_for(policy.quiet_seconds):
        return "settled"
    if any(is_strong(s.cues) for s in observed) and quiet_for(policy.cue_quiet_seconds):
        return "strong_cue"
    if any(
        s.kind == "rollup"
        and now_ms - s.pending_since_ms >= policy.rollup_delay_seconds * 1000
        for s in eligible
    ):
        return "rollup"
    if len(eligible) >= policy.batch_max:
        return "batch"
    oldest = min(s.pending_since_ms for s in eligible)
    if now_ms - oldest >= policy.max_age_seconds * 1000:
        return "max_age"
    if any(s.kind == "time" for s in eligible):
        return "time"
    if any(s.kind == "self" for s in eligible):
        return "self"
    return None


def decide(view: UnitView, policy: WakePolicy, now_ms: int) -> Verdict:
    """Whether `view`'s unit wakes at `now_ms`, and why, before the
    cross-unit concurrency bound (the evaluator applies that one).

    The brakes, in code (20 §3): single-flight per unit; a cooldown after
    each session that ONE strong cue may cut — «once» read as: a wake that
    cut a cooldown leaves a cooldown nothing can cut, so two strong cues in a
    row cannot chain past it."""
    reason = _trigger(view, policy, now_ms)
    if reason is None:
        return Verdict()
    if unit_in_flight(view, policy, now_ms):
        return Verdict(brake="single_flight")
    cut = False
    if _cooling(view, policy, now_ms):
        strong = any(is_strong(s.cues) for s in view.pending if not s.waits)
        last_cut = view.last_wake is not None and view.last_wake.cut_cooldown
        quiet_ms = (
            None if view.last_activity_ms is None else now_ms - view.last_activity_ms
        )
        cue_quiet = quiet_ms is None or quiet_ms >= policy.cue_quiet_seconds * 1000
        if not (strong and cue_quiet and not last_cut):
            return Verdict(brake="cooldown")
        reason, cut = "strong_cue", True
    eligible = [s for s in view.pending if not s.waits]
    return Verdict(
        reason=reason,
        cut_cooldown=cut,
        # NEVER BELOW the watermark it read. A `self`/`time` stimulus can be
        # pending with an id older than stimuli an earlier wake consumed
        # (appended at 15:00, due at 19:40); a wake that consumed only it and
        # set the watermark to its id would re-open every stimulus between,
        # resurrecting them as pending.
        through_stimulus_id=max(
            view.watermark.through_stimulus_id,
            max(s.id for s in view.pending),
        ),
        stimulus_count=len(view.pending),
        eligible_count=len(eligible),
        oldest_pending_ms=min(s.pending_since_ms for s in eligible),
    )
