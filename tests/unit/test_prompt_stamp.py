"""`stamp`: a record's time as a calendar moment and an age.

A row the transcript tools return for «what did we say yesterday» must carry a
day she can read, not epoch milliseconds. These pin the three things the form
exists for: the LOCAL day (an evening in Hong Kong is the next morning's date
in UTC, and the wrong day is the whole failure), the weekday, and the same age
wording as `age`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from nora.domain.prompt import age, stamp

HKT = timezone(timedelta(hours=8), "HKT")
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=HKT)   # a Friday morning


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def test_last_night_reads_as_yesterday_with_its_weekday():
    last_night = datetime(2026, 9, 24, 21, 14, tzinfo=HKT)

    assert stamp(_ms(last_night), NOW) == "2026-09-24 Thu 21:14 (12h ago)"


def test_the_day_is_the_local_one_not_utcs():
    """00:30 HKT on the 25th is 16:30 UTC on the 24th. Rendered in UTC it
    would sit on the wrong day, which is exactly the question «yesterday?»."""
    after_midnight = datetime(2026, 9, 24, 16, 30, tzinfo=UTC)

    assert stamp(_ms(after_midnight), NOW).startswith("2026-09-25 Fri 00:30 ")


def test_the_age_is_ages_own_wording():
    """A row read through a tool and the same row in the prefilled transcript
    must agree on how old it is."""
    for back in (timedelta(seconds=30), timedelta(minutes=45),
                 timedelta(hours=30), timedelta(days=5)):
        ts = _ms(NOW - back)
        assert stamp(ts, NOW).endswith(f"({age(ts, NOW)})")
