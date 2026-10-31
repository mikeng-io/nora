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

RECURRENCE IS A CHAIN OF ONE-SHOT ROWS, NOT A ROW THAT REPEATS (DEC-85)
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
from collections.abc import Mapping, Sequence
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


#: The `kind` a proactive decision's row carries. The same spelling as
#: `nora.app.proactive_screen.PROACTIVE_KIND`, which the heartbeat reads; it is
#: here too because the one-open-row rule below is the STORE's, and a store
#: may not import the application layer.
PROACTIVE_KIND = "proactive"

#: The states in which a proactive row is still going to be said, or is being
#: said now. A note may have at most ONE proactive row in them.
OPEN_STATES: frozenset[str] = frozenset({"pending", "firing"})

#: Stamped on the row a note-merge fold (`SqlNoteStore.merge_into`, driven by
#: the Cognition session's `note_merge`) cancels when it resolves two notes'
#: open proactive rows into one. `uq_reminders_proactive_open_note` keys on the
#: RAW note id a row carries, so a merge that left both rows alone would leave
#: two rows that both FORWARD to the same survivor through `successor()`, a
#: collision the index never sees. The same string migration
#: `0064_proactive_open_note` uses for its boot-time cleanup of the identical
#: condition: one vocabulary for the same reason a row went.
CANCELLED_DUPLICATE_NOTE = "cancelled_duplicate_note"

#: Stamped when `SqlNoteStore.merge_into`'s reminders step finds that the
#: survivor's tag path wrote something after a row it is about to re-point
#: was linked — the same premise `nora.app.proactive_screen.
#: CANCELLED_NOTE_MOVED` and `nora.agent.tools._CANCELLED_NOTE_MOVED` already
#: name at fire time and at `remind` time respectively, duplicated here for
#: the same reason `CANCELLED_DUPLICATE_NOTE` is: a store may not import the
#: application layer, so its own copy of the vocabulary lives at the layer a
#: store may read from.
CANCELLED_NOTE_MOVED = "cancelled_note_moved"


def open_proactive_note(row: Reminder) -> str | None:
    """The note a row holds open, or `None` when it holds none.

    ONE PROACTIVE ROW PER NOTE, OPEN AT A TIME. Two passes can each book the
    same note, so both stores hold this rule at insert time: a second open row
    on the same note is
    `DuplicateProactiveNoteError`. A row that has fired, failed or been
    cancelled holds nothing open, so a re-raise after silence (the strikes
    brake) and a new occasion after the first one went off are both still
    writable.
    """
    if row.kind != PROACTIVE_KIND or row.state.value not in OPEN_STATES:
        return None
    note_id = (row.payload or {}).get("note_id")
    return note_id if isinstance(note_id, str) and note_id else None


class DuplicateProactiveNoteError(NoraError):
    """`create()` was handed a proactive row for a note that already has an
    open one (`open_proactive_note`). Postgres enforces it with
    `uq_reminders_proactive_open_note`; the in-memory store with the same
    check, no `await` between it and the write."""

    def __init__(self, note_id: str) -> None:
        super().__init__(f"note {note_id} already has an open proactive reminder")
        self.note_id = note_id


def open_proactive_subject(row: Reminder) -> tuple[str, str, str] | None:
    """The resolved (subject_kind, subject_id, action) an open proactive row
    holds, or `None`.

    ONE OPEN PROACTIVE ROW PER (SUBJECT, ACTION, DESTINATION) — the resolved
    shared-object reference, never a citation set or a prose match (DEC
    pending; `design/notebook-memory-types/20-shared-cognitive-state.md`).
    Same shape `open_proactive_note` established: both stores hold it at
    insert (`uq_reminders_proactive_open_subject`,
    `DuplicateProactiveSubjectError`). A row carrying no resolved subject has
    no subject key at all — it cannot be conflict-matched, and the writer is
    expected to have surfaced `unresolved` before reaching here."""
    if row.kind != PROACTIVE_KIND or row.state.value not in OPEN_STATES:
        return None
    return proactive_subject(row)


def proactive_subject(row: Reminder) -> tuple[str, str, str] | None:
    """The resolved (subject_kind, subject_id, action) key on ANY proactive
    row, whatever its state. `open_proactive_subject` adds the open-state
    gate for the insert arbiter; reads that reason about a CLOSED row — a
    delivered one the fire recheck is matching against — need the key
    without it."""
    if row.kind != PROACTIVE_KIND:
        return None
    payload = row.payload or {}
    kind = payload.get("subject_kind")
    sid = payload.get("subject_id")
    if not isinstance(kind, str) or not kind or not isinstance(sid, str) or not sid:
        return None
    action = payload.get("action")
    return (kind, sid, action if isinstance(action, str) else "")


class ConflictingProactiveActError(NoraError):
    """The batch's (subject, action) already has an OPEN proactive row at a
    destination the batch does not cover — the cross-room conflict the
    create-time check surfaces, enforced inside the locked transaction so a
    precheck cannot go stale between read and insert."""

    def __init__(
        self, subject: tuple[str, str, str], platform: str, conversation_id: str,
    ) -> None:
        super().__init__(
            f"act {subject} already pending at {platform}:{conversation_id}"
        )
        self.subject = subject
        self.platform = platform
        self.conversation_id = conversation_id


class DuplicateProactiveSubjectError(NoraError):
    """`create()` was handed a proactive row for a (subject, action,
    destination) that already has an open one (`open_proactive_subject`).
    Postgres enforces it with `uq_reminders_proactive_open_subject`; the
    in-memory store with the same check, no `await` between it and the
    write."""

    def __init__(
        self, subject: tuple[str, str, str], platform: str, conversation_id: str
    ) -> None:
        kind, sid, action = subject
        super().__init__(
            f"{kind}:{sid} already has an open proactive "
            f"{action or 'act'} for {platform}:{conversation_id}"
        )
        self.subject = subject


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
    """How often a series comes round. `NONE` is a one-shot reminder and
    stays the default everywhere (DEC-85).

    Deliberately NOT an RRULE (RFC 5545) and deliberately no dependency. These
    values cover the asks the feature serves — an annual birthday,
    a daily countdown into a date, a single offset from one — while an RRULE
    brings BYSETPOS/BYDAY/EXDATE, none of which any surface here can produce and
    all of which would have to round-trip through the schema, the tool arguments
    and the model's head. The cost of being wrong is one enum member and one
    migration; the cost of the dependency is a parser in the fire path.
    """

    NONE = "none"
    # SUB-DAY. These two are DURATIONS, not calendar steps —
    # see `occurrence_at`, where they take a different branch on purpose.
    MINUTELY = "minutely"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


# The two units whose step is a fixed elapsed duration rather than a walk on the
# local calendar. The distinction is the whole reason they are named separately
# from `DAILY` and up: 「每朝八點」 must stay 08:00 across a DST shift, while
# "every 10 minutes" must stay 600 seconds across one. Applying wall-clock
# arithmetic to an hourly rule makes it skip an hour every spring and repeat one
# every autumn, silently.
UNIT_SECONDS: dict[Recurrence, int] = {
    Recurrence.MINUTELY: 60,
    Recurrence.HOURLY: 3600,
}

# Derived, never hand-listed beside the table above: two collections that must
# agree can drift. One definition, one source.
DURATION_UNITS: frozenset[Recurrence] = frozenset(UNIT_SECONDS)

# THE STRUCTURAL FLOOR. `[heartbeat] tick_seconds` is how often she wakes, so a
# series faster than the tick cannot be delivered at all — rows would simply
# pile up and fire late in bursts. This is a property of the machine, not a
# policy, and it refuses regardless of what config says. The OPERATING floor is
# higher and lives in config (`[reminders] min_interval_seconds`, 300s by default); this is the wall
# underneath it.
MIN_PERIOD_SECONDS = 60

# THE CEILING on `interval`, and it is about arithmetic rather than taste.
# `occurrence_at` steps from the anchor, so a large enough multiplier walks off
# the end of what Python can represent: `yearly x 10000` raises
# `ValueError: year 12026 is out of range` and `minutely x 10**12` raises
# `OverflowError` out of `timedelta`. `interval` is what makes them
# reachable, from a number the MODEL writes.
#
# 1000 is far past any real ask (a thousand years, a thousand weeks, a thousand
# minutes is 17 hours) and far short of both overflows. The point is that a
# bound exists at the type, not that this is the only defensible number.
MAX_INTERVAL = 1000


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
    # EVERY N UNITS. 1 is every unit. It is what makes "every 10 minutes"
    # expressible at all — MINUTELY alone can only say "every minute".
    #
    # It multiplies the CALENDAR units too: `every=WEEKLY, interval=2` is a
    # fortnight. That falls out of the same arithmetic and costs nothing to
    # allow, so refusing it would be an arbitrary hole rather than a decision.
    interval: int = 1

    def __post_init__(self) -> None:
        if self.every is Recurrence.NONE:
            raise ValueError(
                "a RecurrenceRule is never NONE — a one-shot reminder carries "
                "`series=None` instead, so the two states cannot both exist"
            )
        if self.count < 1:
            raise ValueError(f"count must be >= 1; got {self.count}")
        if not 1 <= self.interval <= MAX_INTERVAL:
            raise ValueError(
                f"interval must be between 1 and {MAX_INTERVAL}; "
                f"got {self.interval}"
            )
        # The structural floor, enforced on the TYPE so no caller can route
        # around it — the tool, a future API, a migration backfill. A rule
        # faster than the heartbeat tick is undeliverable by construction, and
        # the honest place to say so is where the rule is built rather than
        # where it is fired (which is a background loop nobody is watching).
        period = self.period_seconds
        if period is not None and period < MIN_PERIOD_SECONDS:
            raise ValueError(
                f"a recurrence may not repeat faster than every "
                f"{MIN_PERIOD_SECONDS}s (the heartbeat tick); "
                f"{self.every.value} x {self.interval} is {period}s"
            )

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

    @property
    def period_seconds(self) -> int | None:
        """The fixed elapsed step, or None for a CALENDAR rule that has none.

        None is not "unknown" — it is the correct answer for daily and up. A
        day is not 86,400 seconds across a DST boundary and a month is not any
        number of them, which is exactly why those units walk the local
        calendar instead. Callers that need a floor or a rate check must
        therefore handle None as "coarser than any sub-day rule", never as
        zero.
        """
        unit = UNIT_SECONDS.get(self.every)
        return None if unit is None else unit * self.interval


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
    # 365, matching the shipped `config.toml`. `nora.app.offline` builds
    # `ToolRegistry()` with no caps, so the EVAL harness runs on this default;
    # a default that disagrees with the shipped config is an instrument that
    # cannot measure the thing.
    max_horizon_days: int = 365
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
    # THE OPERATING FLOOR on how often a series may repeat. Distinct from
    # `MIN_PERIOD_SECONDS`, which is the
    # STRUCTURAL wall at the heartbeat tick and refuses regardless of config;
    # this is the number people actually hit and the one an operator moves.
    #
    # It is what makes an every-minute ask fail at the tool contract with
    # something she can SAY, rather than be silently clamped to the first sixty
    # firings — refusing at the boundary beats truncating inside it. At 300s one
    # series is capped at 12 firings/hour, which is roughly what a room can
    # serve given one turn at a time.
    min_interval_seconds: int = 300

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
    # `None` IS a one-shot (DEC-85). A recurring row's recurrence fields are all-or-nothing by
    # construction here and by `ck_reminders_recurrence` in the schema; there is
    # no half-set state either side can represent.
    series: Series | None = None
    # The event-model abstraction (DEC-139): `kind` names WHAT the row is
    # (the default `"reminder"` is an ordinary reminder) and `payload` is the
    # kind's denormalized shape, opaque to Reminder itself ("Reminder never
    # knows what fires").
    kind: str = "reminder"
    payload: Mapping[str, str] | None = None


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

    Five datetimes: `Series.anchor_at` and `RecurrenceRule.until` cross the
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
    # THE SUB-DAY FORK. A duration rule is NOT a calendar step and must
    # not take the wall-clock branch below. "Every 10 minutes" means 600
    # elapsed seconds — always, including across a DST transition. Routed
    # through the wall clock instead it would inherit the two resolutions the
    # docstring describes, and both are WRONG here: an hourly series would fire
    # 03:30 instead of 02:30 on spring-forward (an hour late, when the point is
    # that it is never more than an hour) and would collapse the autumn
    # overlap's two 01:30s into one (an hour SKIPPED, silently, in a series
    # whose whole promise is regularity).
    #
    # So this branch is plain UTC arithmetic and touches no zone at all —
    # `rule.tz` is still carried on the rule, because a duration series is
    # still SHOWN to a person in their own zone and `list_reminders` renders
    # it there; it just plays no part in computing the instant.
    period = rule.period_seconds
    if period is not None:
        return anchor + timedelta(seconds=period * index)
    zone = ZoneInfo(rule.tz)
    local = anchor.astimezone(zone)
    step = rule.interval * index
    if rule.every is Recurrence.DAILY:
        target = local.date() + timedelta(days=step)
    elif rule.every is Recurrence.WEEKLY:
        target = local.date() + timedelta(weeks=step)
    elif rule.every is Recurrence.MONTHLY:
        target = _add_months(local.date(), step)
    elif rule.every is Recurrence.YEARLY:
        target = _add_months(local.date(), 12 * step)
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
    # Duration rules floor by division, which is exact rather than approximate
    # — the step never varies, so there is nothing for the caller's walk to
    # correct. `max(0, ...)` because `now` before the anchor is a real state
    # (a series created with lead time) and a negative index is not.
    period = rule.period_seconds
    if period is not None:
        return max(0, int((now - anchor).total_seconds()) // period)
    zone = ZoneInfo(rule.tz)
    a = anchor.astimezone(zone)
    n = now.astimezone(zone)
    if rule.every is Recurrence.DAILY:
        return (n.date() - a.date()).days // rule.interval
    if rule.every is Recurrence.WEEKLY:
        return ((n.date() - a.date()).days // 7) // rule.interval
    if rule.every is Recurrence.MONTHLY:
        return ((n.year - a.year) * 12 + (n.month - a.month)) // rule.interval
    return (n.year - a.year) // rule.interval


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
    bug, and unchecked the two stores would answer it differently — Postgres
    raises `InvalidRowCountInLimitClauseError` on `LIMIT -1`, Python's `[:-1]`
    returns everything-except-the-last. Plausible-looking wrong data is the
    worse of the two, so both refuse."""
    if limit < 1:
        raise ValueError(f"limit must be >= 1; got {limit}")
    return limit


@runtime_checkable
class ReminderStore(Protocol):
    async def create(self, reminder: Reminder) -> None:
        """Insert-only. A duplicate `id` raises `DuplicateReminderError` — it
        never overwrites, because an overwrite loses a reminder while reporting
        success. A proactive row on a note that already has an open one
        (`open_proactive_note`) raises `DuplicateProactiveNoteError` and writes
        nothing. Naive datetimes raise `NaiveDatetimeError`; aware
        ones are stored normalised to UTC."""
        ...

    async def create_many(
        self, reminders: Sequence[Reminder], *, arbitration_key: str | None = None,
        arbitration_rooms: Sequence[tuple[str, str]] = (),
    ) -> None:
        """The batch variant of `create`, ATOMIC: every row lands or none
        does — a conflicting row refuses the whole insert, so a caller
        scheduling one act into several rooms can never leave an orphan row
        behind a refusal.

        `arbitration_key`, when given, is hashed into a transaction-scoped
        advisory lock that serialises the check-and-insert across processes
        for that key — the caller passes its unit so two writers on different
        processes cannot interleave a pending read between each other's
        creates. `arbitration_rooms` names the unit's rooms: inside the lock,
        an open proactive row carrying the same (subject, action) at a room
        the batch does not cover raises `ConflictingProactiveActError` — the
        cross-room check `check_remind` makes, enforced under the lock the
        precheck cannot hold. `None` skips the lock. Duplicate errors are
        `create`'s own.
        """
        ...

    async def get(self, reminder_id: UUID) -> Reminder | None:
        """Read one row by id. Exists so the contract suite can assert the
        TERMINAL STATE of a swept or cancelled row — without it no test can
        distinguish `FIRING -> FAILED` from `FIRING -> anything else`, because
        `due()` excludes both, and the at-most-once decision rests on exactly
        that distinction."""
        ...

    async def delivered_subject(
        self, rooms: Sequence[tuple[str, str]], *,
        requester_person_id: str, subject_kind: str, subject_id: str,
        action: str, limit: int = 5,
    ) -> Sequence[Reminder]:
        """Whether THIS (subject,action) was already delivered (`sent`
        outcome on a closed row) in the named rooms — the keyed existence
        read the create check and supersession run at canonical unit
        scope. A full page means «unproven remainder», never absence."""
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

    async def pending_of(self, requester_person_id: str) -> Sequence[Reminder]:
        """One requester's PENDING rows, ordered like `due()`: the rows
        `count_pending` counts. `remind` reads Nora's own (the `ai_peer`'s)
        to find the one a new proactive decision about the same note would
        double; `max_pending_per_requester` bounds it."""
        ...

    async def history(
        self, *, requester_person_id: str, since: datetime, limit: int,
    ) -> Sequence[Reminder]:
        """One requester's rows CREATED at or after `since`, in EVERY state,
        oldest first (`created_at`, then `id`) — the decision log.

        `pending_for` and `all_pending` see PENDING rows only, and the
        proactive adaptor's self-observation is
        about what it decided and how it went — `state`, `outcome`,
        `fired_at` — which only a read that keeps the finished rows can
        answer. Keyed by requester because a proactive row's owner is the
        reserved `ai_peer` id, so «Nora's own decisions» is one
        predicate; the caller joins rows to a unit through policy's
        room map, since `Reminder` has no unit column and adding one is
        a schema decision this read does not make.

        `since` must be timezone-aware; `limit` obeys `check_limit`, for the
        reason `due()` does.
        """
        ...

    async def recent_in(
        self, rooms: Sequence[tuple[str, str]], *, requester_person_id: str,
        since: datetime, limit: int,
    ) -> Sequence[Reminder]:
        """`history`, scoped to `rooms` IN THE QUERY: one requester's rows in
        these `(platform, conversation_id)`s created at or after `since`,
        oldest first, at most `limit`. The unit-scoped read `history` cannot
        stand in for — `history`'s global `limit` runs BEFORE a caller's room
        filter, so a unit whose rows sit beyond the cut is silently complete.
        `since` timezone-aware; `limit` obeys `check_limit`."""
        ...

    async def claim(self, reminder_id: UUID, *, now: datetime) -> bool:
        """`PENDING -> FIRING`, as a COMPARE-AND-SET, AND — for a recurring row
        — the successor, minted in the same transaction. True iff this caller
        took the row; False leaves it and the series exactly as they were.

        THE SUCCESSOR BELONGS HERE AND NOWHERE ELSE (DEC-85). The claim is the
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

        `mark`'s `UPDATE ... WHERE id = :id` matches any row that still
        exists, so using it to take a row would let a cancelled row go
        CANCELLED -> FIRING -> DONE and be spoken. The state predicate belongs
        in the statement, like `cancel`'s and `sweep_firing`'s, where it is one
        atomic decision instead of a read-then-write both stores would race.
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
        cancel cannot un-send anything.
        """
        ...

    async def sweep_firing(self, *, now: datetime) -> int:
        """Boot recovery. FIRING rows resolve to FAILED and are never re-fired
        (at-most-once). Returns how many.

        `now` is STAMPED ONTO `fired_at`, for the same reason `mark` stamps it
        on every other terminal write: `fired_at` records when the reminder was
        HANDLED, not when it spoke. A swept row is exactly the one an operator
        asks that of — "when did the crash eat this?" — and it is the one row
        whose history is unreconstructable from anything else. `outcome` is
        already written (`failed_error`); the stamp is the other half of the
        same fact.

        Timezone-aware, like every other datetime crossing this port. Passed in
        rather than read from the wall clock inside each store so the two
        implementations cannot answer it differently and a test can assert an
        exact instant.
        """
        ...

    async def aclose(self) -> None: ...
