"""The stored time trigger of a choose-moment intention."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from sys import maxsize
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nora.domain.reminders import Recurrence, RecurrenceRule, occurrence_at
from nora.domain.ticks import due_tick_of_at


def aware_instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
        return at.astimezone(UTC) if at.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True, slots=True)
class TimeTrigger:
    at: datetime
    zone: str
    recurrence: RecurrenceRule | None = None
    series_anchor: datetime | None = None
    series_offset: int = 0

    def occurrence(self, index: int) -> datetime | None:
        rule = self.recurrence
        if index >= (rule.count if rule else 1):
            return None
        try:
            at = (
                occurrence_at(anchor=self.series_anchor or self.at, rule=rule,
                              index=self.series_offset + index)
                if index and rule else self.at
            )
        except (ValueError, OverflowError):
            return None
        return at if rule is None or rule.until is None or at <= rule.until else None

    def due_occurrences(self, tick: int, start: int) -> Iterator[datetime]:
        index = start
        while (at := self.occurrence(index)) is not None:
            if due_tick_of_at(at) > tick:
                return
            yield at
            index += 1


def decode_time_trigger(value: object) -> TimeTrigger | None:
    """Read the authoritative instant, IANA zone, and optional finite recurrence."""
    if not isinstance(value, Mapping) or value.get("kind") != "time":
        return None
    at, zone = aware_instant(value.get("at")), value.get("zone")
    if at is None or not isinstance(zone, str):
        return None
    try:
        ZoneInfo(zone)
        recurrence = value.get("recurrence")
        if recurrence is None:
            return TimeTrigger(at, zone)
        if not isinstance(recurrence, Mapping):
            return None
        rule = _rule(zone, recurrence)
        anchor, offset = recurrence.get("anchor"), recurrence.get("offset", 0)
        start = aware_instant(anchor)
        if type(offset) is not int or offset < 0:
            return None
        if start is None and (anchor is not None or offset):
            return None
        return TimeTrigger(at, zone, rule, start, offset)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _rule(zone: str, value: Mapping) -> RecurrenceRule:
    every, count, interval = value.get("every"), value.get("count"), value.get("interval", 1)
    until = value.get("until")
    end = aware_instant(until)
    if "count" not in value and end is not None:
        count = maxsize  # The stored end date bounds this series.
    if (
        not isinstance(every, str) or type(count) is not int or type(interval) is not int
        or (until is not None and end is None)
    ):
        raise ValueError("invalid recurrence")
    return RecurrenceRule(Recurrence(every), zone, count, until=end, interval=interval)
