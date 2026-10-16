"""Stimuli and the wake policy.

A **stimulus** is what ingestion, the Rollup or the clock raises to wake
cognition: `(unit, kind, ref, cues, at)`, one durable row per unit in the
`stimulus` table. A **wake** is the moment a unit's pending stimuli start a
session. This module holds the types and the one pure function that decides a
wake, so the rule is testable without a database or a clock.

`decide` starts a Cognition session, every brake included, and its answer is
recorded as a wake (`cognition_wake`) with its reason and the stimulus range it
consumes.

**The watermark is the wake log.** Each unit's durable watermark is the
`(through_stimulus_id, at_ms)` of its newest wake row in that mode, not a row
updated in place (loop state is transition rows, the current value is
the newest). A stimulus is PENDING when it is due by the read's cutoff and not
consumed, and consumed means `id <= through_stimulus_id AND pending_since <=
at_ms` of that newest wake. Both bounds only ever grow — `decide` never sets
`through_stimulus_id` below the one it read, and `at_ms` is the read's cutoff,
which moves forward with the clock — so «consumed by some earlier wake» and
«consumed by the newest» are the same test, which is what lets one row carry
the watermark. The second bound is what keeps a `self` or `time`
stimulus due at 19:40 pending across a wake at 15:00 (pending only from
its due time).

**Single-flight and cooldown are derived from durable rows.** With NO session
hook (only a test builds such an evaluator) no session runs, so a wake is
treated as a session that lasts `session_seconds` (a stated assumption): the
unit is in flight until `at + session_seconds`, then cools down for
`cooldown_seconds`. With a hook (production, `UnitView.tracked`) the assumption
is gone, and a wake's fate is ONE durable fact (DEC-242).

**The wake state machine (tracked wakes).** A wake written with a hook wired
carries `tracked = true` on its own row; its outcome is at most one
`cognition_wake_outcome` row keyed by the wake's id (first write stands):

- OPEN: no outcome row, recorded less than `session_timeout_seconds` ago. The
  unit is in flight (single-flight, the concurrency bound) and the wake's range
  is claimed: it is the watermark, so no later wake can take its stimuli.
- CLOSED: outcome `closed`. The watermark; its carry rows count. Written in
  ONE transaction with those carry rows, so a session is either closed with
  its carries or not closed at all.
- FAILED: outcome `failed`, `timeout` or `cancelled`. Skipped by the
  watermark, so its stimuli are pending again; carries none.
- EXPIRED: no outcome row, recorded `session_timeout_seconds` ago or more (a
  crash, a kill, an exit before anything was written, an outcome write that
  failed). Read exactly as FAILED.

A later wake is recorded only when the newest is not OPEN (`decide`'s
single-flight), and `record_wake` re-checks that under a per-unit lock
(`WakeGuard`), so a wake never swallows the range of one still unresolved;
a `closed` outcome is refused (written `timeout`, no carries) for a wake a
later wake already superseded, so an expired session that finishes late
cannot close over stimuli another session took. An UNTRACKED wake (no hook)
has no outcome and is a plain watermark. A restart recomputes all of
it from the rows; nothing lives only in memory. `UnitView.carry` holds the
stimuli a CLOSED wake returned to the next agenda with a strike.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from .cues import is_rearming, is_strong

#: The stimulus kinds. The evaluator handles all of them generically; each is
#: raised by its own producer (ingest for `message`, the evaluator for `time`,
#: the Rollup for `rollup`).
StimulusKind = Literal["message", "social", "play", "rollup", "time", "self"]
STIMULUS_KINDS: tuple[str, ...] = get_args(StimulusKind)

#: What a `message` stimulus's item was (including an edit,
#: reaction or delete); other kinds name their own producer's item.
MessageItem = Literal["message", "edit", "reaction", "delete"]

#: The wake reasons, in the table's order — the order `decide` tries them
#: in and the label vocabulary of `nora_cognition_wake_total{reason}`. No
#: `rollup` since DEC-279: a Rollup stimulus never starts a wake, it rides the
#: next one (`RIDES_ALONG`). `cognition_wake.reason`'s CHECK still admits
#: `rollup` for the rows recorded before.
WakeReason = Literal[
    "settled", "strong_cue", "batch", "max_age", "time", "self",
]
WAKE_REASONS: tuple[str, ...] = get_args(WakeReason)

#: Kinds that never start a wake and are claimed by EVERY wake (DEC-279):
#: a Rollup stimulus (a co-location, an arrival, a deviation) is
#: context for a session another reason started, never a reason of its own.
#: The bound on how long one waits is the daily `time` floors (07:00 and 00:00
#: by default): a `time` wake claims these too, so a unit nobody talks in still
#: sees them within a day. With `[cognition] time_floors = []` and a silent
#: unit nothing bounds it; that is the operator's configuration to choose.
RIDES_ALONG: frozenset[str] = frozenset({"rollup"})

#: Why a unit with pending stimuli did NOT wake this tick.
WakeBrake = Literal["single_flight", "cooldown", "backoff", "concurrency"]
WAKE_BRAKES: tuple[str, ...] = get_args(WakeBrake)

#: How a wake row was produced. Kept as a column because the watermark is
#: read per mode.
WakeMode = Literal["live"]
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
    #: A person's reply to her message within `reply_quiet_seconds`
    #: does not by itself wake Cognition: it waits for the next wake. Recorded
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
    """A recorded wake. `through_stimulus_id` and
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
    #: Written with a session hook wired: the wake awaits an outcome row and
    #: is EXPIRED (read as failed) when it has none past the session timeout.
    tracked: bool = False
    #: Stimuli inside `[.., through_stimulus_id]` this wake did NOT claim, so
    #: they stay pending (`Watermark.unclaimed`). Empty for a wake that claims
    #: everything pending.
    unclaimed_ids: tuple[int, ...] = ()


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
    tracked: bool = False
    unclaimed_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class WakeGuard:
    """What `record_wake` re-checks under the unit's lock before it writes: the
    unit's newest wake in the mode is still `last_wake_id` (None: none), and
    that wake's outcome is still `last_outcome` (None: no row). The view the
    decision was computed from is then still true, so the new wake's range is
    the one `decide` meant (a wake the view read as EXPIRED cannot close in
    between and have its stimuli processed twice)."""

    last_wake_id: int | None
    last_outcome: str | None


#: Why a wake FAILED (`cognition_wake_outcome.cause`, DEC-244's
#: classification). `infra`: the world failed
#: (provider, network, database, a kill, a timeout, a shutdown, a reply cut at
#: max_tokens, an overflow message nobody proved the agenda caused, or anything
#: unclassified): the unit BACKS OFF and its stimuli are NEVER struck, because a
#: real human message must not be dropped for an outage. `agenda`: PROVEN to be
#: this agenda's own doing, deterministically (an `AgendaError`: a grounding
#: fault for that input, or a context overflow the size and the repetition
#: below both prove): only these count toward a strike. Unknown is `infra`:
#: fail-closed on the side that keeps the message.
FAILURE_CAUSES: tuple[str, ...] = ("infra", "agenda")
DEFAULT_FAILURE_CAUSE = "infra"
#: The cause a wake that did NOT fail records: a `closed` session, or one a
#: shutdown `cancelled` (DEC-245). Recording `infra` there would read «the
#: world failed» on a session that succeeded.
NO_CAUSE = "none"
OUTCOME_CAUSES: tuple[str, ...] = (*FAILURE_CAUSES, NO_CAUSE)


#: What a `cancelled` outcome's `detail` starts with when a PROCESS RESTART ended
#: the wake: the session was cancelled as the process stopped
#: (`CognitionSessionRunner`), or the next process found the wake open with no
#: process left to own it (`WakeEvaluator.reclaim_orphans`). A restart is not a
#: failure of the session, the agenda or the world, so it takes no cause of its
#: own: `cancelled` already neither counts toward the failure backoff nor strikes
#: (`failure_streak`), and the detail is what tells an operator why
#: (the session's `cancelled` outcome carries it).
RESTART_DETAIL = "restart"


def restart_detail(why: str) -> str:
    """The `detail` of a `cancelled` outcome a restart caused."""
    return f"{RESTART_DETAIL}: {why}"


def is_restart_detail(detail: str) -> bool:
    return detail == RESTART_DETAIL or detail.startswith(f"{RESTART_DETAIL}:")


def stored_cause(outcome: str, cause: str) -> str:
    """The cause an outcome row is written with: `none` for `closed` and
    `cancelled`, the classified one otherwise. The ONE rule both stores apply."""
    return NO_CAUSE if outcome in ("closed", "cancelled") else cause


#: Strands' own `stop_reason`s for a loop cut off by its bounds (DEC-247):
#: rounds, total tokens, output tokens. The loop RETURNS normally on each (no
#: exception), so a caller that reads only «it returned» takes a session that
#: never got to finish for one that did. Duplicated nowhere: the metric's label
#: vocabulary and `nora.app.cognition_session` both read this.
LIMIT_STOP_REASONS: tuple[str, ...] = (
    "limit_turns", "limit_total_tokens", "limit_output_tokens",
)


class SessionLimitError(Exception):
    """A session's loop stopped at one of its bounds (`LIMIT_STOP_REASONS`)
    before it finished. Raised by the session runner so the wake is recorded
    `failed`, and `classify_failure` calls it `infra`: a session that ran out
    of rounds, tokens or time did not prove the agenda was at fault (a runaway
    loop is the model's, and one run says nothing about the stimuli), so
    nothing is struck and the stimuli stay pending (DEC-244, DEC-247)."""

    def __init__(self, stop_reason: str) -> None:
        super().__init__(f"the session stopped at its bound: {stop_reason}")
        self.stop_reason = stop_reason


class AgendaError(Exception):
    """A failure caused by the agenda itself, raised by code that has PROVED it
    deterministic for the input (`classify_failure` reads only this). Today
    that is one thing: a context overflow `prove_overflow_is_the_agenda`
    accepted. A grounder read that fails is a store failure, so `infra`."""


def classify_failure(exc: BaseException) -> str:
    """`infra` or `agenda` for the exception that ended a session (DEC-244).

    `agenda` ONLY for an `AgendaError`: the exception's own class or a base, so
    a subclass counts. Every SDK class is `infra`, however alarming its name:
    Strands raises `ContextWindowOverflowException` for ANY provider error whose
    message contains «context length» or «too many tokens» (a 503, a 413 tokens
    per minute, a fallback model with a smaller window), and a reply cut at
    max_tokens (`MaxTokensReachedException`, `PartialReplyError`) is a provider
    capping output or a model looping, the world failing and not the agenda.
    The one SDK failure that can be the agenda's is an overflow, and only
    `prove_overflow_is_the_agenda` may turn it into an `AgendaError`."""
    if isinstance(exc, AgendaError):
        return "agenda"
    return "infra"


_OVERFLOW_NAME = "ContextWindowOverflowException"

#: Words in an error chain that say the provider or the transport failed, not
#: the size of the request: a per-minute token budget, a throttle, an outage, a
#: gateway, a timeout. Any one of them keeps an overflow `infra`.
_TRANSPORT_MARKERS = (
    "per minute", "tokens per min", "tpm", "rate limit", "rate_limit", "too many requests",
    "throttl", "overloaded", "unavailable", "bad gateway", "gateway", "timed out",
    "timeout", "internal server error", "connection", "service", "quota", "capacity",
    "try again",
)

#: The only HTTP status a size fault comes back with. Anything else on the
#: chain (413 «too many tokens per minute», 429, 5xx) is transport.
_SIZE_FAULT_STATUS = 400

#: An HTTP status quoted in a message: only right after «error code», «status»
#: or «http» (so «8192 tokens» or «500 tokens» never matches).
_TEXT_STATUS = re.compile(r"(?:error code|status(?: code)?|http(?: status)?)\W{0,3}([1-5]\d\d)\b")


def is_context_overflow(exc: BaseException) -> bool:
    """The exception is (a subclass of) Strands' `ContextWindowOverflowException`,
    matched by name because `nora.domain` may not import the SDK."""
    return any(c.__name__ == _OVERFLOW_NAME for c in type(exc).__mro__)


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo and len(seen) < 12:
        e = todo.pop()
        if e is None or any(e is x for x in seen):
            continue
        seen.append(e)
        todo.extend((e.__cause__, e.__context__))
    return seen


def transport_evidence(exc: BaseException) -> bool:
    """Something on the exception chain says the provider or the network failed:
    a status other than 400, or a message naming a rate, a quota, an outage or a
    timeout. Deterministic, and biased to `True`: a false positive keeps a
    message, a false negative retracts one."""
    for e in _chain(exc):
        for attr in ("status_code", "status", "http_status"):
            code = getattr(e, attr, None)
            if isinstance(code, int) and not isinstance(code, bool) and code != _SIZE_FAULT_STATUS:
                return True
        response = getattr(e, "response", None)
        response_code = getattr(response, "status_code", None)
        if response_code is None and isinstance(response, dict):
            # botocore's ClientError: the status is in the response metadata.
            meta = response.get("ResponseMetadata")
            response_code = meta.get("HTTPStatusCode") if isinstance(meta, dict) else None
        if (
            isinstance(response_code, int)
            and not isinstance(response_code, bool)
            and response_code != _SIZE_FAULT_STATUS
        ):
            return True
        if isinstance(e, TimeoutError | ConnectionError | OSError):
            return True
        text = str(e).lower()
        if any(m in text for m in _TRANSPORT_MARKERS):
            return True
        # A status a wrapper flattened into the message ("Error code: 500 - ...").
        # Only after a status-style prefix, so a token count never matches.
        if any(int(m) != _SIZE_FAULT_STATUS for m in _TEXT_STATUS.findall(text)):
            return True
    return False


#: A lower bound on tokens: characters over this. English runs about 4 chars a
#: token and CJK about 1, so the estimate never overstates the prompt, and a
#: prompt it calls large IS large.
CHARS_PER_TOKEN_LOWER_BOUND = 4


def estimate_tokens_lower_bound(*texts: str) -> int:
    return sum(len(t) for t in texts) // CHARS_PER_TOKEN_LOWER_BOUND


def prove_overflow_is_the_agenda(
    exc: BaseException, *, estimated_tokens: int, window_tokens: int | None,
    window_fraction: float, previously_overflowed: bool,
) -> bool:
    """True only when a context overflow is PROVEN to come from the agenda's size:
    (1) it IS an overflow; (2) nothing on its chain says the provider or the
    transport failed; (3) the prompt's token estimate (a lower bound) is at
    least `window_fraction` of the configured model's window, which must be
    KNOWN (`window_tokens` None proves nothing); and (4) the same agenda, or a
    smaller one it contains, overflowed on an earlier attempt
    (`previously_overflowed`). A single overflow, however loud, is `infra`."""
    return (
        is_context_overflow(exc)
        and not transport_evidence(exc)
        and window_tokens is not None
        and window_tokens > 0
        and estimated_tokens >= window_fraction * window_tokens
        and previously_overflowed
    )


#: How a tracked wake ended (`cognition_wake_outcome`). Only `closed` advances
#: the watermark; the other three return the wake's stimuli to pending.
WAKE_OUTCOMES: tuple[str, ...] = ("closed", "failed", "timeout", "cancelled")


def inert_kinds_for(*, tracked: bool, floors_ready: bool) -> frozenset[str]:
    """Stimulus kinds that stay pending but neither wake the unit nor are claimed
    by any wake: `time`, while sessions are tracked and no skill ships for the
    `time` agenda kind (DEC-245). Every reader of «what could wake this unit»
    (`decide`, the age gauge, the health command) takes the set from here."""
    return frozenset({"time"}) if tracked and not floors_ready else frozenset()


@dataclass(frozen=True)
class Watermark:
    """`(through_stimulus_id, through_ms)`: every stimulus with `id <=
    through_stimulus_id` and pending since no later than `through_ms` is
    consumed, EXCEPT the ids in `unclaimed`: a wake that claims only the
    stimuli of its own kind (DEC-245) leaves the rest pending, and a
    range cannot say that, so the wake lists them. `ZERO` is a unit that has
    never woken."""

    through_stimulus_id: int = 0
    through_ms: int = 0
    unclaimed: frozenset[int] = frozenset()

    @classmethod
    def of(cls, wake: WakeRow | None) -> Watermark:
        if wake is None:
            return cls()
        return cls(wake.through_stimulus_id, wake.at_ms, frozenset(wake.unclaimed_ids))

    def consumed(self, stimulus: Stimulus) -> bool:
        return (
            stimulus.id <= self.through_stimulus_id
            and stimulus.pending_since_ms <= self.through_ms
            and stimulus.id not in self.unclaimed
        )


@dataclass(frozen=True)
class WakePolicy:
    """The wake policy's numbers. Every value is a starting guess.

    `session_seconds` is how long a wake with no session
    hook (a test's evaluator) is treated as running."""

    quiet_seconds: float = 1800.0  # ([cognition])
    cue_quiet_seconds: float = 120.0
    batch_max: int = 80
    max_age_seconds: float = 3600.0
    cooldown_seconds: float = 300.0
    max_concurrent_sessions: int = 2
    session_seconds: float = 60.0
    #: How long a TRACKED wake may stay OPEN (no outcome row) before it is
    #: EXPIRED, read as failed: it stops holding its unit and its stimuli
    #: return. A session's own wall clock ends a margin before it.
    session_timeout_seconds: float = 900.0
    #: A unit whose sessions FAIL is not woken again at the cooldown, forever.
    #: After `n` consecutive failed or timed-out wakes the
    #: next one waits `cooldown_seconds * 2 ** (n - 1)`, capped at this; a
    #: `closed` wake resets it (`failure_streak`): per-unit backoff
    #: after a failed session.
    backoff_max_seconds: float = 21600.0
    #: After this many consecutive failures the stimuli the failing wakes
    #: claimed are struck (retracted, reason `failed`): an always-failing agenda
    #: stops instead of retrying the same input for ever (`max_strikes`
    #: is the item-level twin for a session that ran).
    max_consecutive_failures: int = 5


SessionState = Literal["running", "closed", "failed"]


@dataclass(frozen=True)
class LastSession:
    """A unit's NEWEST wake's state, as its rows say (the module docstring's
    state machine): `running` (OPEN), `closed`, or `failed` (FAILED or
    EXPIRED). `started_at_ms` is the wake row's own `recorded_at` (the clock
    the timeout runs on, whether or not a session row was ever written);
    `outcome` is the outcome row's value, None when there is none. None as a
    whole: the newest wake is untracked."""

    state: SessionState
    started_at_ms: int
    ended_at_ms: int | None = None
    outcome: str | None = None
    #: Why a `failed` state failed (`FAILURE_CAUSES`). An EXPIRED wake (no
    #: outcome row: a kill, a crash) is `infra`.
    cause: str = DEFAULT_FAILURE_CAUSE


def wake_state(
    *, tracked: bool, recorded_at_ms: int, outcome: str | None,
    outcome_at_ms: int | None, stale_before_ms: int | None, cause: str | None = None,
) -> LastSession | None:
    """ONE wake's state from its rows: the rule both `StimulusStore`
    implementations apply (the SQL store spells it as a predicate in
    `nora.infra.sql.stimuli._failed_wake`, bound to this by the contract
    suite). `stale_before_ms` None: the reader tracks no sessions, so a wake
    with no outcome row is a plain watermark."""
    if outcome is not None:
        return LastSession(
            state="closed" if outcome == "closed" else "failed",
            started_at_ms=recorded_at_ms, ended_at_ms=outcome_at_ms, outcome=outcome,
            # Normalised on READ: an older outcome row carries
            # `infra` for a closed or cancelled wake, and the append-only table
            # is never rewritten to say otherwise.
            cause=stored_cause(outcome, cause or DEFAULT_FAILURE_CAUSE),
        )
    if not tracked or stale_before_ms is None:
        return None
    if recorded_at_ms < stale_before_ms:
        return LastSession(state="failed", started_at_ms=recorded_at_ms)
    return LastSession(state="running", started_at_ms=recorded_at_ms)


@dataclass(frozen=True)
class UnitView:
    """What one tick reads for one unit: its pending stimuli (due, not
    consumed, ascending id), when its rooms last saw a person's item, and its
    newest wake in this mode.

    `mark` is the watermark: the newest wake that did not fail or expire
    (None: derive it from `last_wake`, the pre-session behaviour).
    `last_session` is the newest wake's state, `tracked` and `live` the
    reader's (see the module docstring); `carry` maps a stimulus id a closed
    wake returned to the next agenda onto its strike."""

    unit: str
    pending: tuple[Stimulus, ...]
    last_activity_ms: int | None
    last_wake: WakeRow | None
    mark: Watermark | None = None
    last_session: LastSession | None = None
    tracked: bool = False
    live: bool = False
    carry: Mapping[int, int] = field(default_factory=dict)
    #: The unit's consecutive failed or timed-out wakes, newest first, ended by
    #: a `closed` one (`failure_streak`); a `cancelled` or still-open wake
    #: neither counts nor ends it. 0 for an untracked reader.
    failures: int = 0
    #: How many of that streak's failures were `agenda` failures (an `infra`
    #: one, a `cancelled` or an open wake neither counts nor ends this run; a
    #: `closed` wake ends it). ONLY this feeds the strike (DEC-244).
    agenda_failures: int = 0
    #: The watermark the newest AGENDA-failed wake of the run would have set:
    #: what those wakes claimed, so what is struck at `max_consecutive_failures`
    #: is exactly what they read and never a stimulus that arrived since, nor
    #: one only an infrastructure-failed wake claimed.
    failed_mark: Watermark | None = None
    #: Stimulus kinds that stay pending but neither wake the unit nor are
    #: claimed by any wake (DEC-245): a `time` stimulus, while no skill
    #: ships for the `time` agenda kind, is a floor the session would be handed
    #: and could do nothing with. Pending ones (raised before the gate, or by an
    #: earlier deploy) are listed as unclaimed by every wake, so they are never
    #: consumed unseen and act again the moment the kind is ready.
    inert_kinds: frozenset[str] = frozenset()

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
    #: The stimuli the wake takes (`claim`), and the ones inside its range it
    #: leaves pending.
    claimed_ids: frozenset[int] = frozenset()
    unclaimed_ids: tuple[int, ...] = ()


def failure_streak(newest_first: Iterable[LastSession | None]) -> int:
    """Consecutive FAILED wakes, from the newest back: the ONE rule both
    `StimulusStore` implementations apply to their wake states (the contract
    suite holds them together). A `failed` state (outcome `failed`/`timeout`,
    or expired with none) counts; a `cancelled` outcome is a shutdown, not a
    session that went wrong, and a `running` wake has not ended: neither counts
    and neither ends the run. A `closed` wake, or a wake with no state (an
    untracked one), ends it."""
    n = 0
    for state in newest_first:
        if state is None or state.state == "closed":
            break
        if state.state == "running" or state.outcome == "cancelled":
            continue
        n += 1
    return n


def agenda_failure_streak(newest_first: Iterable[LastSession | None]) -> int:
    """Consecutive `agenda`-caused failures, from the newest back: `failure_streak`
    with one more transparency, an `infra` failure (an outage says nothing about
    the agenda). Only this run strikes (DEC-244)."""
    n = 0
    for state in newest_first:
        if state is None or state.state == "closed":
            break
        if state.state == "running" or state.outcome == "cancelled" or state.cause != "agenda":
            continue
        n += 1
    return n


def failure_run(
    newest_first: Iterable[tuple[WakeRow, LastSession | None]],
) -> tuple[int, int, Watermark | None]:
    """`(failure_streak, agenda_failure_streak, the watermark of the newest
    AGENDA-failed wake in that run)` from a unit's wakes with their states,
    newest first: what both stores put on `UnitView.failures`,
    `.agenda_failures` and `.failed_mark`."""
    pairs = list(newest_first)
    n = failure_streak(state for _, state in pairs)
    agenda = agenda_failure_streak(state for _, state in pairs)
    if agenda == 0:
        return n, 0, None
    for wake, state in pairs:
        if state is None or state.state == "closed":
            break
        if state.state == "failed" and state.cause == "agenda" and state.outcome != "cancelled":
            return n, agenda, Watermark.of(wake)
    return n, agenda, None


def backoff_ms(failures: int, policy: WakePolicy) -> int:
    """How long after a wake ended the unit's next wake waits: the cooldown,
    doubled for each failure past the first, capped."""
    cooldown = int(policy.cooldown_seconds * 1000)
    if failures <= 1:
        return cooldown
    cap = int(policy.backoff_max_seconds * 1000)
    return int(max(cooldown, min(cap, cooldown * 2 ** min(failures - 1, 40))))


def in_flight(last_wake: WakeRow | None, policy: WakePolicy, now_ms: int) -> bool:
    """Whether the unit's last (would-)session is still running at `now_ms`,
    by the SHADOW assumption (`session_seconds`). Only meaningful with no
    session hook; `unit_in_flight` is the one the evaluator asks."""
    return (
        last_wake is not None
        and now_ms < last_wake.at_ms + int(policy.session_seconds * 1000)
    )


def unit_in_flight(view: UnitView, policy: WakePolicy, now_ms: int) -> bool:
    """Single-flight's question. Untracked (no hook, a test's evaluator): the
    `session_seconds` assumption.
    Tracked: the newest wake is OPEN (no outcome row, younger than
    `session_timeout_seconds`), or (`live`) its task is alive in this
    process."""
    if not view.tracked:
        return in_flight(view.last_wake, policy, now_ms)
    if view.live:
        return True
    # The view's state already applied the timeout (`wake_state`, with the
    # reader's `stale_before_ms`). A second clock comparison here would open
    # a gap at its boundary: a wake the view still claims (its range under
    # the watermark) read as not in flight, so a new wake could be decided
    # over a range that expires a moment later.
    session = view.last_session
    return session is not None and session.state == "running"


def _cool_base(view: UnitView, policy: WakePolicy) -> int | None:
    """When the unit's last (would-)session ended, or is presumed to have."""
    wake = view.last_wake
    if wake is None:
        return None
    if not view.tracked:
        return wake.at_ms + int(policy.session_seconds * 1000)
    session = view.last_session
    if session is None:
        return wake.at_ms
    if session.ended_at_ms is not None:
        return session.ended_at_ms
    return session.started_at_ms + int(policy.session_timeout_seconds * 1000)


def _cooling(view: UnitView, policy: WakePolicy, now_ms: int) -> bool:
    """The plain cooldown after the last session."""
    base = _cool_base(view, policy)
    return base is not None and now_ms < base + int(policy.cooldown_seconds * 1000)


def _backing_off(view: UnitView, policy: WakePolicy, now_ms: int) -> bool:
    """The extra wait a failing unit owes past the cooldown (`backoff_ms`)."""
    if view.failures <= 1:
        return False
    base = _cool_base(view, policy)
    return base is not None and now_ms < base + backoff_ms(view.failures, policy)


def _strong_pending(view: UnitView, stimuli: list[Stimulus]) -> bool:
    """Whether any of `stimuli` carries a cue that trips `strong cue` NOW.

    Right after a `strong_cue` wake whose session ran (the unit's newest wake, a
    durable row, so this survives a restart; a failed one re-pended its
    stimuli and does not count) only `REARMING_CUES` count: a date or time said
    again in the conversation that just woke a session waits for `settled`,
    `max age` or `batch`, and never cuts a cooldown. Any other newest reason
    lifts it."""
    last = view.last_wake
    session = view.last_session
    # A strong-cue wake whose session FAILED, timed out or was cancelled (a
    # restart included) put its stimuli back: that datetime was never read, so
    # it still re-arms. Only a session that ran (`closed`, or untracked) did.
    read = session is None or session.state != "failed"
    if last is not None and last.reason == "strong_cue" and read:
        return any(is_rearming(s.cues) for s in stimuli)
    return any(is_strong(s.cues) for s in stimuli)


def _trigger(view: UnitView, policy: WakePolicy, now_ms: int) -> WakeReason | None:
    """The FIRST of the triggers that holds, in the table's order.

    Only stimuli that may wake count (`waits` False): a reply to Nora inside
    `reply_quiet_seconds` rides the next wake and never starts one. Quiet is
    measured from the unit's newest person-authored item, `waits` ones
    included: a room still replying to her is not a quiet room."""
    eligible = [s for s in view.pending if can_wake(s, view)]
    if not eligible:
        return None
    quiet_ms = (
        None if view.last_activity_ms is None else now_ms - view.last_activity_ms
    )

    def quiet_for(seconds: float) -> bool:
        return quiet_ms is None or quiet_ms >= seconds * 1000

    # `settled` and `strong cue` are about a ROOM going quiet after people
    # said something, so they count observed items only. A pending `time` or
    # `self` stimulus has its own row in the table; letting it read as
    # `settled` in a quiet unit would record every 07:00 floor under the
    # wrong reason. A `rollup` stimulus is not `eligible` at all (DEC-279).
    observed = [s for s in eligible if s.kind in _OBSERVED]
    if observed and quiet_for(policy.quiet_seconds):
        return "settled"
    if _strong_pending(view, observed) and quiet_for(policy.cue_quiet_seconds):
        return "strong_cue"
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

    The brakes, in code: single-flight per unit; a cooldown after
    each session that ONE strong cue may cut — «once» read as: a wake that
    cut a cooldown leaves a cooldown nothing can cut, so two strong cues in a
    row cannot chain past it. After a `strong_cue` wake a bare date or time
    is no strong cue at all (`_strong_pending`), so it cuts nothing."""
    reason = _trigger(view, policy, now_ms)
    if reason is None:
        return Verdict()
    if unit_in_flight(view, policy, now_ms):
        return Verdict(brake="single_flight")
    cut = False
    cooling = _cooling(view, policy, now_ms)
    if cooling or _backing_off(view, policy, now_ms):
        strong = _strong_pending(view, [s for s in view.pending if can_wake(s, view)])
        last_cut = view.last_wake is not None and view.last_wake.cut_cooldown
        quiet_ms = (
            None if view.last_activity_ms is None else now_ms - view.last_activity_ms
        )
        cue_quiet = quiet_ms is None or quiet_ms >= policy.cue_quiet_seconds * 1000
        # A strong cue cuts a cooldown ONCE, and never a backoff: a unit whose
        # sessions keep failing must not be woken every `cue_quiet_seconds` by
        # a room that keeps saying a time.
        if not (cooling and view.failures == 0 and strong and cue_quiet and not last_cut):
            return Verdict(brake="cooldown" if cooling else "backoff")
        reason, cut = "strong_cue", True
    claimed = claim(view, reason)
    eligible = [s for s in claimed if can_wake(s, view)]
    through = max(view.watermark.through_stimulus_id, max(s.id for s in claimed))
    taken = {s.id for s in claimed}
    return Verdict(
        reason=reason,
        cut_cooldown=cut,
        # NEVER BELOW the watermark it read. A `self`/`time` stimulus can be
        # pending with an id older than stimuli an earlier wake consumed
        # (appended at 15:00, due at 19:40); a wake that consumed only it and
        # set the watermark to its id would re-open every stimulus between,
        # resurrecting them as pending.
        through_stimulus_id=through,
        stimulus_count=len(claimed),
        eligible_count=len(eligible),
        oldest_pending_ms=min(s.pending_since_ms for s in eligible),
        claimed_ids=frozenset(taken),
        # What the range covers and the wake did NOT take: listed, so the
        # watermark leaves them pending. Above `through` they are pending
        # anyway.
        unclaimed_ids=tuple(sorted(
            {s.id for s in view.pending if s.id not in taken and s.id <= through}
            # Carried forward from the watermark's own list, WHETHER OR NOT this
            # read shows them: a truncated read (`limit`) cuts the newest, and a
            # stimulus the view did not show cannot be claimed here, so dropping
            # it from the list would consume it unseen under this wake's range.
            | {u for u in view.watermark.unclaimed if u not in taken and u <= through}
        )),
    )


#: The wake reasons that are about ONE kind of stimulus (DEC-245):
#: a `time` or `self` wake claims only stimuli of that kind, and the kinds that
#: ride along (DEC-279).
_OWN_KIND_REASONS: frozenset[str] = frozenset({"time", "self"})


def can_wake(stimulus: Stimulus, view: UnitView) -> bool:
    """Whether `stimulus` may start a wake: not a reply that `waits`, not of an
    inert kind and not of a kind that only rides along (DEC-279). Every
    reader of «what could wake this unit» (`decide`, the age gauge) asks this."""
    return (
        not stimulus.waits
        and stimulus.kind not in view.inert_kinds
        and stimulus.kind not in RIDES_ALONG
    )


def claim(view: UnitView, reason: WakeReason) -> list[Stimulus]:
    """The pending stimuli a wake for `reason` takes (DEC-245, DEC-279).

    `settled`, `strong_cue`, `batch` and `max_age` are about the pile or the
    room, so they take every pending stimulus. `time` and `self` are about one
    kind of stimulus: they take THAT kind, and a room's messages stay pending
    until they satisfy `settled` or `strong_cue` themselves. Every wake also
    takes the pending stimuli that ride along (`RIDES_ALONG`, a Rollup's), so
    a `time` floor is what bounds how long one waits. The stimuli left over are
    recorded on the wake (`NewWake.unclaimed_ids`) so the watermark never passes
    them: the claim is a SET, and no stimulus is lost or processed twice."""
    live = [s for s in view.pending if s.kind not in view.inert_kinds]
    if reason in _OWN_KIND_REASONS:
        return [
            s for s in live
            if (s.kind == reason and not s.waits) or s.kind in RIDES_ALONG
        ]
    return live
