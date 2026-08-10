"""The recurrence calendar (DEC-85) — `occurrence_at` / `plan_successor`.

WHY THIS IS A SEPARATE FILE FROM THE CONTRACT SUITE
---------------------------------------------------
`tests/contract/test_reminder_store_contract.py` holds both stores to the same
chain; this holds the ARITHMETIC they both call to the calendar. The split
matters because the interesting inputs here — a spring-forward gap, 29 February,
the 31st of a short month, a three-day outage — are properties of dates rather
than of storage, and expressing each one as a stored row would be four extra
Postgres round trips to assert something neither store is responsible for.

Every case below is a date the naive implementation gets wrong:
`due_at + timedelta(days=1)` walks an hour at every DST boundary, and stepping
from the previous occurrence instead of from the series anchor turns a monthly
reminder set on the 31st into one that fires on the 28th forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nora.domain.reminders import (
    NaiveDatetimeError,
    Recurrence,
    RecurrenceRule,
    Reminder,
    ReminderState,
    Series,
    UnknownRecurrenceZoneError,
    occurrence_at,
    plan_successor,
    remaining_occurrences,
)

HK = "Asia/Hong_Kong"
NY = "America/New_York"
SERIES = uuid.UUID("22222222-2222-5222-9222-222222222222")


def _rule(every: Recurrence, *, tz: str = HK, count: int = 12, until=None):
    return RecurrenceRule(every=every, tz=tz, count=count, until=until)


def _local(zone: str, *args: int) -> datetime:
    """A wall clock in `zone`, as the instant it names."""
    return datetime(*args, tzinfo=ZoneInfo(zone))  # type: ignore[arg-type]


def _reminder(*, anchor: datetime, rule: RecurrenceRule, occurrence: int = 0):
    return Reminder(
        id=uuid.uuid5(SERIES, str(occurrence)),
        requester_person_id="miles", platform="signal",
        conversation_id="conv-1", conversation_type="group", text="食藥",
        created_at=anchor - timedelta(days=1),
        due_at=occurrence_at(anchor=anchor, rule=rule, index=occurrence),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
        series=Series(
            id=SERIES, anchor_at=anchor, occurrence=occurrence, rule=rule,
        ),
    )


# ── the type refuses what it cannot honour ───────────────────────────────────

def test_a_rule_cannot_be_unbounded():
    """`count` is required and >= 1, so «repeats forever» is not a value.

    This is the cap that cannot be forgotten: every other bound in this feature
    is a check somebody has to remember to make, and a check can be skipped by
    a new call site. A required field cannot.
    """
    with pytest.raises(ValueError):
        RecurrenceRule(every=Recurrence.DAILY, tz=HK, count=0)
    with pytest.raises(TypeError):
        RecurrenceRule(every=Recurrence.DAILY, tz=HK)  # type: ignore[call-arg]


def test_a_rule_is_never_the_none_recurrence():
    """`Recurrence.NONE` and a `RecurrenceRule` are two spellings of two
    different things, and allowing both would make «does this repeat?» a
    question with two answers that can disagree. A one-shot is `series=None`."""
    with pytest.raises(ValueError):
        RecurrenceRule(every=Recurrence.NONE, tz=HK, count=3)


def test_an_unresolvable_zone_is_refused_rather_than_guessed():
    """The same argument `NaiveDatetimeError` makes about a naive datetime: a
    recurrence's whole promise is a LOCAL wall clock, so a zone that does not
    resolve has no honest fallback — UTC, the host zone and a fixed +08:00 are
    all instants the requester never named."""
    with pytest.raises(UnknownRecurrenceZoneError):
        RecurrenceRule(every=Recurrence.DAILY, tz="Mars/Olympus", count=3)


def test_an_until_bound_must_be_timezone_aware():
    with pytest.raises(NaiveDatetimeError):
        RecurrenceRule(
            every=Recurrence.DAILY, tz=HK, count=3,
            until=datetime(2026, 10, 11, 9, 0),
        )


def test_a_row_outside_its_own_series_bound_cannot_exist():
    """`occurrence < count`, checked where the two meet. A row at index 5 of a
    3-occurrence series is a row whose successor logic is already past its end
    — it would read as a series that quietly stopped rather than as the
    corruption it is."""
    rule = _rule(Recurrence.DAILY, count=3)
    with pytest.raises(ValueError):
        Series(id=SERIES, anchor_at=_local(HK, 2026, 8, 8, 9), occurrence=3,
               rule=rule)


# ── the wall clock a recurrence keeps ────────────────────────────────────────

def test_a_daily_occurrence_keeps_its_local_time_across_spring_forward():
    """08:00 local on both sides — 13:00 UTC then 12:00 UTC.

    2026-03-08 is the US spring-forward. `anchor + timedelta(days=1)` gives
    13:00 UTC, which is 09:00 EDT: an hour late, on the first day, and an hour
    late for the rest of the series. THAT is the defect this rule exists to
    prevent, and it is invisible in Hong Kong, which has had no DST since 1979.
    """
    rule = _rule(Recurrence.DAILY, tz=NY)
    anchor = _local(NY, 2026, 3, 7, 8, 0)
    assert anchor == datetime(2026, 3, 7, 13, 0, tzinfo=UTC)

    after = occurrence_at(anchor=anchor, rule=rule, index=1)

    assert after == datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    assert after.astimezone(ZoneInfo(NY)).hour == 8
    assert after - anchor == timedelta(hours=23)


def test_a_daily_occurrence_keeps_its_local_time_across_autumn_back():
    """The other direction: a 25-hour step, same 08:00 local."""
    rule = _rule(Recurrence.DAILY, tz=NY)
    anchor = _local(NY, 2026, 10, 31, 8, 0)

    after = occurrence_at(anchor=anchor, rule=rule, index=1)

    assert after.astimezone(ZoneInfo(NY)).hour == 8
    assert after - anchor == timedelta(hours=25)


def test_a_local_time_that_does_not_exist_shifts_forward_rather_than_vanishing():
    """02:30 on a spring-forward morning is a wall clock that never happens.

    Resolved through the offset in force BEFORE the transition (`fold=0`), so
    the occurrence lands at 03:30 local — an hour late, once, and never
    skipped. A skipped occurrence is a reminder that silently did not happen,
    which is the one outcome this feature cannot produce quietly.
    """
    rule = _rule(Recurrence.DAILY, tz=NY)
    anchor = _local(NY, 2026, 3, 7, 2, 30)

    after = occurrence_at(anchor=anchor, rule=rule, index=1)

    local = after.astimezone(ZoneInfo(NY))
    assert (local.year, local.month, local.day) == (2026, 3, 8)
    assert (local.hour, local.minute) == (3, 30)


def test_a_local_time_that_exists_twice_fires_once_at_the_first():
    """01:30 happens twice on an autumn-back morning. `fold=0` takes the FIRST,
    so it fires once, earlier — never twice, which is what a `fold`-blind
    implementation that generated both would do."""
    rule = _rule(Recurrence.DAILY, tz=NY)
    anchor = _local(NY, 2026, 10, 31, 1, 30)

    after = occurrence_at(anchor=anchor, rule=rule, index=1)

    local = after.astimezone(ZoneInfo(NY))
    assert (local.hour, local.minute) == (1, 30)
    # EDT (-4) is the first of the two; EST (-5) would be an hour later in UTC.
    assert after == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


# ── month and year arithmetic ────────────────────────────────────────────────

def test_a_monthly_reminder_set_on_the_31st_clamps_and_then_recovers():
    """31 Jan → 28 Feb → 31 Mar → 30 Apr → 31 May.

    The recovery is the point, and it is only possible because every occurrence
    is computed from the ANCHOR. Stepping from the previous occurrence, 28 Feb
    + 1 month is 28 Mar, and the reminder spends the rest of the year on the
    28th — a monthly reminder that silently moved three days.
    """
    rule = _rule(Recurrence.MONTHLY, count=6)
    anchor = _local(HK, 2026, 1, 31, 9, 0)

    days = [
        occurrence_at(anchor=anchor, rule=rule, index=i)
        .astimezone(ZoneInfo(HK)).date()
        for i in range(5)
    ]

    assert [d.isoformat() for d in days] == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31",
    ]


def test_a_yearly_reminder_on_29_february_returns_on_the_next_leap_year():
    """29 Feb 2028 → 28 Feb 2029/2030/2031 → 29 Feb 2032.

    Clamping is what keeps the three non-leap years from being skipped, and the
    anchor is what brings the fourth back to the 29th. Chained clamping loses
    the 29th permanently after the first non-leap year.
    """
    rule = _rule(Recurrence.YEARLY, count=6)
    anchor = _local(HK, 2028, 2, 29, 9, 0)

    days = [
        occurrence_at(anchor=anchor, rule=rule, index=i)
        .astimezone(ZoneInfo(HK)).date().isoformat()
        for i in range(5)
    ]

    assert days == [
        "2028-02-29", "2029-02-28", "2030-02-28", "2031-02-28", "2032-02-29",
    ]


def test_a_weekly_reminder_keeps_its_weekday():
    rule = _rule(Recurrence.WEEKLY, count=4)
    anchor = _local(HK, 2026, 8, 8, 9, 0)      # a Saturday

    for i in range(4):
        moment = occurrence_at(anchor=anchor, rule=rule, index=i)
        local = moment.astimezone(ZoneInfo(HK))
        assert local.weekday() == anchor.astimezone(ZoneInfo(HK)).weekday()
        assert local.date().isoformat() == f"2026-08-{8 + 7 * i:02d}"


# ── catch-up ─────────────────────────────────────────────────────────────────

def test_the_successor_of_an_on_time_fire_is_the_very_next_occurrence():
    """The ordinary case, and the control for the catch-up tests below: when
    nothing was missed, nothing is skipped."""
    rule = _rule(Recurrence.DAILY, count=10)
    anchor = _local(HK, 2026, 8, 8, 9, 0)
    row = _reminder(anchor=anchor, rule=rule)

    successor = plan_successor(row, now=row.due_at)

    assert successor is not None
    assert successor.series is not None
    assert successor.series.occurrence == 1
    assert successor.due_at == occurrence_at(anchor=anchor, rule=rule, index=1)


def test_a_long_outage_skips_the_missed_occurrences_instead_of_bursting():
    """Ten days down: ONE row fires, and the next is tomorrow — not ten rows.

    The alternative is a group chat receiving ten «食藥」 messages in ten
    consecutive ticks, every one of them about a moment that has passed. The
    skipped occurrences are CONSUMED — index 11, not index 1 — so an outage
    cannot lengthen the series past the two weeks it was asked for.

    `now` is an hour PAST the tenth occurrence, so ten have gone by and the
    first one still in the future is the eleventh.
    """
    rule = _rule(Recurrence.DAILY, count=30)
    anchor = _local(HK, 2026, 8, 8, 9, 0)
    row = _reminder(anchor=anchor, rule=rule)

    successor = plan_successor(row, now=anchor + timedelta(days=10, hours=1))

    assert successor is not None
    assert successor.series is not None
    assert successor.series.occurrence == 11
    assert successor.due_at == occurrence_at(anchor=anchor, rule=rule, index=11)
    assert successor.due_at > anchor + timedelta(days=10, hours=1)


def test_an_outage_longer_than_the_series_ends_it_rather_than_replaying_it():
    """Down for longer than the whole series: it is over, and nothing fires.

    The failure this rules out is the worse half of the burst — a fortnight's
    countdown to a wedding anniversary, all fourteen messages arriving the week
    after the wedding.
    """
    rule = _rule(Recurrence.DAILY, count=5)
    anchor = _local(HK, 2026, 8, 8, 9, 0)
    row = _reminder(anchor=anchor, rule=rule)

    assert plan_successor(row, now=anchor + timedelta(days=40)) is None


def test_a_yearly_series_catches_up_without_walking_a_year_of_dates():
    """Catch-up is arithmetic, not iteration: a decade skipped in one step.

    Not a performance test — a correctness one. `_floor_index` must FLOOR for
    every rule, and a floor that overshoots would silently drop occurrences
    from the series' own count.
    """
    rule = _rule(Recurrence.YEARLY, count=60)
    anchor = _local(HK, 2026, 2, 24, 9, 0)
    row = _reminder(anchor=anchor, rule=rule)

    successor = plan_successor(row, now=_local(HK, 2036, 6, 1, 9, 0))

    assert successor is not None
    assert successor.series is not None
    assert successor.series.occurrence == 11
    assert successor.due_at == _local(HK, 2037, 2, 24, 9, 0)


# ── bounds ───────────────────────────────────────────────────────────────────

def test_a_one_shot_has_no_successor():
    row = Reminder(
        id=uuid.uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="conv-1", conversation_type="group", text="洗衫",
        created_at=datetime(2026, 8, 8, 1, 0, tzinfo=UTC),
        due_at=datetime(2026, 8, 8, 13, 0, tzinfo=UTC),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    assert plan_successor(row, now=row.due_at) is None


def test_the_count_bound_ends_the_series():
    rule = _rule(Recurrence.DAILY, count=3)
    anchor = _local(HK, 2026, 8, 8, 9, 0)
    row = _reminder(anchor=anchor, rule=rule, occurrence=2)

    assert plan_successor(row, now=row.due_at) is None


def test_the_until_bound_ends_the_series_before_the_count_does():
    rule = _rule(
        Recurrence.DAILY, count=30, until=_local(HK, 2026, 8, 10, 23, 59),
    )
    anchor = _local(HK, 2026, 8, 8, 9, 0)

    on_the_9th = plan_successor(
        _reminder(anchor=anchor, rule=rule, occurrence=1), now=anchor + timedelta(days=1),
    )
    assert on_the_9th is not None
    assert on_the_9th.series is not None
    assert on_the_9th.series.occurrence == 2

    past_the_bound = plan_successor(
        _reminder(anchor=anchor, rule=rule, occurrence=2), now=anchor + timedelta(days=2),
    )
    assert past_the_bound is None


def test_the_successors_id_is_derived_so_a_double_mint_collides():
    """`uuid5(series_id, occurrence)`, not `uuid4`.

    A fresh id would make a second mint of the same occurrence a duplicate
    REMINDER — the same thing said twice, which at-most-once (R-37b) exists to
    prevent. A derived one makes it a primary-key collision the database
    refuses, so the mistake cannot reach a person.
    """
    rule = _rule(Recurrence.DAILY, count=5)
    anchor = _local(HK, 2026, 8, 8, 9, 0)
    row = _reminder(anchor=anchor, rule=rule)

    first = plan_successor(row, now=row.due_at)
    again = plan_successor(row, now=row.due_at)

    assert first is not None and again is not None
    assert first.id == again.id == uuid.uuid5(SERIES, "1")


def test_remaining_occurrences_counts_what_follows_this_row():
    rule = _rule(Recurrence.DAILY, count=14)
    anchor = _local(HK, 2026, 9, 27, 9, 0)

    assert remaining_occurrences(None) == 0
    assert remaining_occurrences(
        _reminder(anchor=anchor, rule=rule, occurrence=0).series
    ) == 13
    assert remaining_occurrences(
        _reminder(anchor=anchor, rule=rule, occurrence=13).series
    ) == 0
