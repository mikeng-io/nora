"""The reminders port. A reminder is a DEFERRED REQUEST: the row stores the
requester's own sentence, and at fire time it is put to Nora as a request.

EVERY DATETIME CROSSING THIS PORT IS UTC-AWARE
----------------------------------------------
Both directions, every field, every argument. This is a CONTRACT, not a
convention, because the two implementations cannot agree by accident:

- A `timestamptz` column stores an instant and asyncpg hands it back with
  `tzinfo=UTC`, whatever offset went in. An in-memory dict hands back the exact
  object it was given. Two aware values for the same instant compare EQUAL, so
  `==` cannot see this — only `utcoffset()` can, and `utcoffset()` is what
  downstream formatting reads. Nora's own clock is `+08:00`
  (`nora.agent.tools._HKT`), so the same reminder printed "17:00" from one store
  and "09:00" from the other.
- A NAIVE datetime has no instant at all. asyncpg would resolve it in the CLIENT
  HOST's zone — a deployment fact, not a domain one — and the fake would keep it
  naive and then raise `TypeError` comparing it to an aware `now`. Neither is an
  answer, so `ensure_utc` REFUSES it: `NaiveDatetimeError`, loudly, at the port.

`ensure_utc`/`normalise_reminder` below are the single implementation of that
rule; both stores call them on the way in and on the way out.

RECURRENCE IS A CHAIN OF ONE-SHOT ROWS, NOT A ROW THAT REPEATS (DEC-84)
-----------------------------------------------------------------------
A recurring reminder is N rows, one per occurrence, linked by a `Series` — and
only ONE of them is ever `pending`. The successor is minted inside `claim`,
in the same statement-pair that takes the row, which is what makes R-37 ("a row
fires once, ever") survive recurrence unchanged: no row is ever re-armed, reset
or re-queued, so nothing in the at-most-once argument has to be re-litigated.
The arithmetic that computes the successor — `occurrence_at`/`plan_successor`
— lives HERE, in the domain, for the same reason `ensure_utc` does: two stores
computing a calendar independently is two calendars.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nora.domain.errors import NoraError


class NaiveDatetimeError(ValueError):
    """A datetime reached the store without a timezone.

    A `ValueError` like `UnmatchableIdentifierError`, and for the same reason:
    the argument itself is unusable, and the only honest alternative — picking a
    zone for the caller — silently invents an instant that was never specified.
    """


class DuplicateReminderError(NoraError):
    """`create()` was handed an id that already exists.

    A `NoraError` so it carries the failure taxonomy (INFRASTRUCTURE by default
    — silent, per R-5). It exists so both stores can be held to ONE observable
    behaviour: Postgres raised `UniqueViolationError` off the primary key while
    the in-memory store silently OVERWROTE the row, which is a lost reminder
    reported as success.
    """

    def __init__(self, reminder_id: UUID) -> None:
        super().__init__(f"reminder {reminder_id} already exists")
        self.reminder_id = reminder_id


class UnknownRecurrenceZoneError(ValueError):
    """A recurrence names a timezone this host's tz database does not have.

    A `ValueError` like `NaiveDatetimeError`, and for the same reason: the
    argument itself is unusable, and every alternative — falling back to UTC, to
    the host zone, to a fixed +08:00 — invents a wall clock the requester never
    named. A recurrence's whole promise is «08:00 LOCAL, whatever the offset does
    that week», so an unresolvable zone is the one input it cannot approximate.
    """


class ReminderState(StrEnum):
    PENDING = "pending"
    FIRING = "firing"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Recurrence(StrEnum):
    """How often a series comes round. `NONE` is the whole of v1's behaviour and
    stays the default everywhere, so a one-shot reminder is byte-identical to
    what shipped before DEC-84.

    Deliberately NOT an RRULE (RFC 5545) and deliberately no dependency. These
    five values cover every ask the feature was built from — an annual birthday,
    a daily countdown into a date, a single offset from one — while an RRULE
    brings BYSETPOS/BYDAY/EXDATE, none of which any surface here can produce and
    all of which would have to round-trip through the schema, the tool arguments
    and the model's head. The cost of being wrong is one enum member and one
    migration; the cost of the dependency is a parser in the fire path.
    """

    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


@dataclass(frozen=True, slots=True)
class RecurrenceRule:
    """WHAT repeats, in WHICH wall clock, and WHERE it stops.

    `count` is REQUIRED and >= 1, which is the type system carrying a rule the
    caps would otherwise have to remember: an unbounded series is not
    representable, so «runs forever» cannot be reached by forgetting a check.
    `until` narrows further and may be None; the series ends at whichever bound
    comes first.

    `tz` is an IANA zone NAME («Asia/Hong_Kong»), never a fixed offset. The
    difference is the whole point of storing it: a fixed +08:00 cannot say what
    08:00 local means on the far side of a DST boundary, and a recurrence that
    silently walks an hour twice a year is the defect this field exists to
    prevent. It is resolved once, here, so an unusable zone fails at
    construction rather than at fire time (`UnknownRecurrenceZoneError`).
    """

    every: Recurrence
    tz: str
    count: int
    until: datetime | None = None

    def __post_init__(self) -> None:
        if self.every is Recurrence.NONE:
            raise ValueError(
                "a RecurrenceRule is never NONE — a one-shot reminder carries "
                "`series=None` instead, so the two states cannot both exist"
            )
        if self.count < 1:
            raise ValueError(f"count must be >= 1; got {self.count}")
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise UnknownRecurrenceZoneError(
                f"unknown recurrence timezone {self.tz!r}; a recurrence keeps a "
                "LOCAL wall clock and cannot guess one"
            ) from exc
        if self.until is not None and self.until.utcoffset() is None:
            raise NaiveDatetimeError(
                f"until must be timezone-aware; got naive {self.until!r}."
            )


@dataclass(frozen=True, slots=True)
class Series:
    """Where one row sits in a recurring series — `None` on a one-shot row.

    `anchor_at` is the FIRST occurrence's `due_at`, kept for the life of the
    series, and it is not redundant with `due_at`: every occurrence is computed
    from the anchor rather than from its predecessor, so monthly and yearly
    steps cannot drift. Stepping predecessor-to-predecessor, a 31 Jan monthly
    reminder clamps to 28 Feb and then stays on the 28th forever; anchored, it
    is 31 Jan → 28 Feb → 31 Mar, and a 29 Feb yearly one returns to the 29th on
    the next leap year instead of being rounded away on the first non-leap one.

    `occurrence` is 0-based and indexes off the anchor, so
    `occurrence_at(anchor, rule, occurrence) == due_at` for every row in a
    series. `id` is shared by every occurrence — the join key an operator asks
    "did this series ever actually go out?" with.
    """

    id: UUID
    anchor_at: datetime
    occurrence: int
    rule: RecurrenceRule

    def __post_init__(self) -> None:
        if self.occurrence < 0:
            raise ValueError(f"occurrence must be >= 0; got {self.occurrence}")
        if self.occurrence >= self.rule.count:
            raise ValueError(
                f"occurrence {self.occurrence} is past the series' own bound of "
                f"{self.rule.count} — a row outside its series cannot exist"
            )


@dataclass(frozen=True, slots=True)
class ReminderCaps:
    """The anti-runaway caps and the zone a recurrence keeps, as a DOMAIN value.

    Not `RemindersConfig` from `nora/config.py`: import-linter's "only app and
    __main__ read config" contract (`pyproject.toml`) lists `nora.agent` as a
    source module and `nora.config` as forbidden, so a config type on
    `ToolRegistry.__init__` fails `uv run lint-imports` outright. A
    `TYPE_CHECKING` guard does not rescue it — grimp's
    `exclude_type_checking_imports` defaults to False and this repo does not set
    it. `nora.app.composition` builds one from `cfg.reminders`.
    """

    max_pending_per_requester: int = 20
    min_lead_seconds: int = 120
    max_horizon_days: int = 30
    # A SERIES counts as ONE against `max_pending_per_requester`, because the
    # chain only ever has one `pending` row (see the module header). That is
    # what makes this cap necessary rather than merely tidy: without it a
    # recurring reminder would be the one row shape no cap bounds — 20 daily
    # series is 20 pending rows forever. 60 is the number of occurrences a
    # series may hold in total, after which it ends and must be asked for
    # again; it is long enough for every ask the feature was built from (14
    # daily, 60 annual) and finite for the ones it was not.
    max_occurrences_per_series: int = 60
    # The wall clock a recurrence keeps — an IANA zone NAME, never an offset
    # (see `RecurrenceRule.tz`). A deployment fact, so it lives beside the caps
    # rather than in the tool: `nora.agent` may not import `nora.config`, and
    # this is the only domain value the tool layer already receives.
    timezone: str = "Asia/Hong_Kong"


@dataclass(frozen=True, slots=True)
class Reminder:
    id: UUID
    requester_person_id: str
    platform: str
    conversation_id: str
    conversation_type: str
    text: str
    created_at: datetime
    due_at: datetime
    state: ReminderState
    fired_at: datetime | None
    outcome: str | None
    # `None` IS a one-shot, and the default keeps every pre-DEC-84 construction
    # site — production, tests and both stores — meaning exactly what it meant
    # before. A recurring row's recurrence fields are all-or-nothing by
    # construction here and by `ck_reminders_recurrence` in the schema; there is
    # no half-set state either side can represent.
    series: Series | None = None


def ensure_utc(value: datetime, *, field: str) -> datetime:
    """The port's datetime rule, in one place. Raises on a naive input."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDatetimeError(
            f"{field} must be timezone-aware; got naive {value!r}. "
            "Reminders store instants, and a naive datetime names none."
        )
    return value.astimezone(UTC)


def normalise_reminder(reminder: Reminder) -> Reminder:
    """`ensure_utc` over EVERY one of a reminder's datetimes.

    Five now, not three: `Series.anchor_at` and `RecurrenceRule.until` cross the
    port exactly like `due_at` does, and a naive one there is worse rather than
    milder — the anchor is what every future occurrence is computed FROM, so a
    zone guessed once is a zone wrong for the whole series.
    """
    series = reminder.series
    if series is not None:
        rule = series.rule
        series = replace(
            series,
            anchor_at=ensure_utc(series.anchor_at, field="series.anchor_at"),
            rule=(
                rule if rule.until is None
                else replace(rule, until=ensure_utc(rule.until, field="until"))
            ),
        )
    return replace(
        reminder,
        created_at=ensure_utc(reminder.created_at, field="created_at"),
        due_at=ensure_utc(reminder.due_at, field="due_at"),
        fired_at=(
            None if reminder.fired_at is None
            else ensure_utc(reminder.fired_at, field="fired_at")
        ),
        series=series,
    )


def _add_months(day: date, months: int) -> date:
    """Calendar-month arithmetic with END-OF-MONTH CLAMPING.

    31 Jan + 1 month is 28 Feb (29 in a leap year) and 29 Feb + 12 months is
    28 Feb, because those are the only days those months have. Clamping is the
    only choice that keeps a monthly reminder monthly — rolling over to 1 March
    moves it into the wrong month, and skipping the month entirely drops an
    occurrence the requester asked for.

    It is safe here ONLY because callers step from the series ANCHOR rather than
    from the previous occurrence (see `Series`): clamping compounds when it is
    chained, and does not when every step is computed from the same origin.
    """
    total = (day.year * 12 + day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    first_of_next = (
        date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    )
    last = (first_of_next - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def occurrence_at(*, anchor: datetime, rule: RecurrenceRule, index: int) -> datetime:
    """The instant of the `index`-th occurrence, counting the anchor as 0.

    THE RULE: a recurrence keeps its LOCAL WALL CLOCK, not its UTC offset and
    not its elapsed seconds. The step is taken on the local CALENDAR in
    `rule.tz`, the local time-of-day is carried over unchanged, and only then is
    the result converted back to UTC. So a daily 08:00 reminder is 08:00 local
    on both sides of a DST boundary — 13:00 UTC one day and 12:00 UTC the next —
    where `due_at + timedelta(days=1)` would have said 07:00 or 09:00 local and
    kept saying it for the rest of the series.

    The two wall clocks a zone can fail to provide are resolved by
    `datetime.replace(tzinfo=...)`'s `fold=0`, and both answers are deliberate:

    * a time that does not exist that day (the spring-forward gap: 02:30 where
      the clocks jump 02:00 → 03:00) resolves through the offset in force
      BEFORE the transition, so it fires at 03:30 local — an hour late, never
      skipped. A skipped occurrence is a reminder that silently did not happen;
      a late one is a reminder.
    * a time that exists twice (the autumn-back overlap) resolves to the FIRST
      of the two, so it fires once, earlier, never twice.
    """
    zone = ZoneInfo(rule.tz)
    local = anchor.astimezone(zone)
    if rule.every is Recurrence.DAILY:
        target = local.date() + timedelta(days=index)
    elif rule.every is Recurrence.WEEKLY:
        target = local.date() + timedelta(weeks=index)
    elif rule.every is Recurrence.MONTHLY:
        target = _add_months(local.date(), index)
    elif rule.every is Recurrence.YEARLY:
        target = _add_months(local.date(), 12 * index)
    else:  # pragma: no cover - RecurrenceRule refuses NONE at construction
        raise ValueError(f"unsupported recurrence {rule.every!r}")
    return datetime.combine(target, local.time()).replace(tzinfo=zone).astimezone(UTC)


def _floor_index(*, anchor: datetime, rule: RecurrenceRule, now: datetime) -> int:
    """A LOWER BOUND on the index of the last occurrence at or before `now`.

    Exists so catch-up after a long outage is arithmetic rather than a loop: a
    daily series that has been down for a year is one subtraction here plus at
    most a step or two of correction in `plan_successor`, instead of 365
    iterations of calendar maths. It must never OVERSHOOT — an index too high
    would silently drop occurrences from the series' own count — so every branch
    floors, and the caller walks forward from it.
    """
    zone = ZoneInfo(rule.tz)
    a = anchor.astimezone(zone)
    n = now.astimezone(zone)
    if rule.every is Recurrence.DAILY:
        return (n.date() - a.date()).days
    if rule.every is Recurrence.WEEKLY:
        return (n.date() - a.date()).days // 7
    if rule.every is Recurrence.MONTHLY:
        return (n.year - a.year) * 12 + (n.month - a.month)
    return n.year - a.year


# How far `plan_successor` will walk past `_floor_index`'s estimate. The floor
# is exact to within one step for every rule, so 2 would do; 8 is slack for a
# future rule whose floor is coarser, and a bound at all is what keeps a bad
# floor from spinning the tick forever.
_MAX_CATCHUP_WALK = 8


def plan_successor(reminder: Reminder, *, now: datetime) -> Reminder | None:
    """The row that replaces this one when it fires — or None when the series
    ends here (and always None for a one-shot).

    THE CATCH-UP RULE: the successor is the first occurrence STRICTLY AFTER
    `now`, not the one after this row's `due_at`. A process that was down for
    three days therefore fires the one overdue row once and then resumes on the
    next future occurrence — the three it slept through are consumed, never
    replayed. A burst of missed reminders arriving at once is worse than the
    silence it is trying to repair: it is unreadable in a group chat, it can
    exceed the model budget, and every one of the messages is about a moment
    that has passed.

    Skipped occurrences still SPEND their slot in `rule.count`, so an outage
    cannot lengthen a series' calendar footprint — «every day for the next two
    weeks» stays two weeks whether or not the process stayed up for them.

    The successor's id is `uuid5(series.id, index)` and not a fresh `uuid4`, so
    minting the same occurrence twice is a PRIMARY KEY COLLISION rather than a
    duplicate reminder: `create` raises `DuplicateReminderError`, the claim
    transaction that would have written it rolls back, and nobody is told the
    same thing twice. At-most-once (R-37b) survives recurrence because the
    only new write recurrence adds is one the database itself can refuse.
    """
    series = reminder.series
    if series is None:
        return None
    rule = series.rule
    now = ensure_utc(now, field="now")
    anchor = ensure_utc(series.anchor_at, field="series.anchor_at")
    index = max(
        series.occurrence + 1,
        _floor_index(anchor=anchor, rule=rule, now=now),
    )
    for _ in range(_MAX_CATCHUP_WALK):
        if index >= rule.count:
            return None
        due_at = occurrence_at(anchor=anchor, rule=rule, index=index)
        if rule.until is not None and due_at > rule.until:
            return None
        if due_at > now:
            break
        index += 1
    else:
        # Unreachable with a floor that floors. Ending the series is the
        # fail-closed answer: a series that stops is visible in
        # `list_reminders` and in the absence of a pending row, where a tick
        # that never returns is visible only as a heartbeat that stopped.
        return None
    return replace(
        reminder,
        id=uuid.uuid5(series.id, str(index)),
        due_at=due_at,
        state=ReminderState.PENDING,
        fired_at=None,
        outcome=None,
        series=replace(series, occurrence=index),
    )


def remaining_occurrences(series: Series | None) -> int:
    """How many occurrences follow this row, by the series' COUNT bound alone.

    An UPPER bound, not a promise: `until` or a cancel can end the series
    sooner, and this deliberately does not pretend to know which — it is what
    `list_reminders` says out loud, where over-claiming («11 more») is a smaller
    error than the alternative of saying nothing at all about how long a
    recurring reminder will go on.
    """
    if series is None:
        return 0
    return max(0, series.rule.count - series.occurrence - 1)


def check_limit(limit: int) -> int:
    """`due(limit=...)`'s rule, in one place. A batch size below 1 is a caller
    bug, and the two stores used to answer it two different ways — Postgres
    raised `InvalidRowCountInLimitClauseError` on `LIMIT -1`, Python's `[:-1]`
    returned everything-except-the-last. Plausible-looking wrong data is the
    worse of the two, so both now refuse."""
    if limit < 1:
        raise ValueError(f"limit must be >= 1; got {limit}")
    return limit


@runtime_checkable
class ReminderStore(Protocol):
    async def create(self, reminder: Reminder) -> None:
        """Insert-only. A duplicate `id` raises `DuplicateReminderError` — it
        never overwrites, because an overwrite loses a reminder while reporting
        success. Naive datetimes raise `NaiveDatetimeError`; aware ones are
        stored normalised to UTC."""
        ...

    async def get(self, reminder_id: UUID) -> Reminder | None:
        """Read one row by id. Exists so the contract suite can assert the
        TERMINAL STATE of a swept or cancelled row — without it no test can
        distinguish `FIRING -> FAILED` from `FIRING -> anything else`, because
        `due()` excludes both, and the at-most-once decision rests on exactly
        that distinction."""
        ...

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        """PENDING rows whose `due_at` has passed, oldest first, `id`
        ASCENDING within an equal `due_at`.

        The tiebreak is part of the contract, not an incidental: `due_at` alone
        leaves ties to the storage engine's physical order, which an ordinary
        `mark()` UPDATE reshuffles — and with `limit`, a tie straddling the
        batch boundary makes the BATCH's membership unspecified, not just its
        order.

        `now` must be timezone-aware. `limit` is the tick's batch size and must
        be >= 1; below that raises `ValueError` rather than each store guessing
        (see `check_limit`).
        """
        ...

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]:
        """That conversation's PENDING rows, ordered like `due()`: `due_at`
        then `id`."""
        ...

    async def all_pending(self, *, limit: int) -> Sequence[Reminder]:
        """EVERY conversation's PENDING rows, ordered like `due()`.

        The operator's view, and the one read `pending_for` cannot stand in for:
        `nora reminders list` has no conversation to scope by — finding out
        which conversations have rows waiting is the whole question it answers.
        Bounded by `limit` for the reason `due()` is, and `check_limit` is the
        same shared rule, so a caller cannot get "everything except the last"
        from one store and an error from the other.
        """
        ...

    async def count_pending(self, requester_person_id: str) -> int: ...

    async def claim(self, reminder_id: UUID, *, now: datetime) -> bool:
        """`PENDING -> FIRING`, as a COMPARE-AND-SET, AND — for a recurring row
        — the successor, minted in the same transaction. True iff this caller
        took the row; False leaves it and the series exactly as they were.

        THE SUCCESSOR BELONGS HERE AND NOWHERE ELSE (DEC-84). The claim is the
        one point in a reminder's life that is exactly-once by construction, and
        the successor has to inherit that: minted after the fire turn instead,
        a crash mid-turn would leave the row FIRING (swept to FAILED at boot,
        R-37b) with no successor written, and a daily reminder would end
        silently at the first crash. Minted here it is durable BEFORE any egress
        is attempted, so the series survives a fire that fails for any reason —
        a timeout, a model error, a dead process — while a row that is never
        claimed (cancelled, or refused by the fire-time policy re-check) never
        mints one, and the series stops exactly where the decision to stop it
        was made.

        `now` is the claim instant, and it is a PARAMETER for the reason it is
        one on `due()` and `sweep_firing()`: the catch-up rule
        (`plan_successor`) is a comparison against it, and a store reading its
        own wall clock would put that rule beyond a test's reach and let the
        two implementations answer it differently.

        `plan_successor` is the shared implementation — neither store does
        calendar arithmetic of its own.

        Separate from `mark` because the predicate is the whole point. `mark`
        moves a row by id alone — right for CLOSING OUT a row the caller already
        owns, and wrong for TAKING one, because `due()` is a query rather than a
        claim: the batch it returns is a snapshot of a past moment, and the tick
        awaits a turn per group before it reaches the later rows. A cancel
        landing in that window must win.

        With `mark` doing the take it did not. `UPDATE ... WHERE id = :id`
        matches any row that still exists, nothing ever deletes reminders, so
        the caller's "False means already resolved" branch was unreachable and
        a cancelled row went CANCELLED -> FIRING -> DONE and was spoken. The
        state predicate belongs in the statement, like `cancel`'s and
        `sweep_firing`'s, where it is one atomic decision instead of a
        read-then-write both stores would race.
        """
        ...

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        """True if a row changed. False means it was already resolved.

        Moves a row by id alone, whatever state it is in — the terminal write
        for a row the caller has already claimed. Use `claim` to TAKE a row.

        `fired_at`, when given, must be timezone-aware and is stored as UTC."""
        ...

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        """Scoped on purpose: a uuid carries none of its own.

        On a recurring row this cancels the SERIES, and needs no extra clause to
        do it: a series is a chain whose next link is only ever forged by
        `claim`, so cancelling its one pending occurrence is cancelling the
        whole of what is left. Occurrences already fired stay as they are — a
        cancel has never been able to un-send anything (§8.3).
        """
        ...

    async def sweep_firing(self, *, now: datetime) -> int:
        """Boot recovery. FIRING rows resolve to FAILED and are never re-fired
        (at-most-once). Returns how many.

        `now` is STAMPED ONTO `fired_at`, for the same reason `mark` stamps it
        on every other terminal write: `fired_at` records when the reminder was
        HANDLED, not when it spoke. A swept row is exactly the one an operator
        asks that of — "when did the crash eat this?" — and the sweep used to
        leave it NULL, so the one row whose history is unreconstructable from
        anything else was also the one row with no timestamp. `outcome` is
        already written (`failed_error`); the stamp is the other half of the
        same fact.

        Timezone-aware, like every other datetime crossing this port. Passed in
        rather than read from the wall clock inside each store so the two
        implementations cannot answer it differently and a test can assert an
        exact instant.
        """
        ...

    async def aclose(self) -> None: ...
