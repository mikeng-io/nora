"""The stored time trigger of a choose-moment intention."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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

    def due(self, tick: int, start: int) -> Iterator[datetime]:
        rule = self.recurrence
        for index in range(start, rule.count if rule else 1):
            try:
                at = (
                    occurrence_at(anchor=self.at, rule=rule, index=index)
                    if index and rule else self.at
                )
            except (ValueError, OverflowError):
                return
            if due_tick_of_at(at) > tick or (rule and rule.until and at > rule.until):
                return
            yield at


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
        rule = _rule(zone, recurrence) if recurrence is not None else None
        return TimeTrigger(at, zone, rule)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _rule(zone: str, value: object) -> RecurrenceRule:
    if not isinstance(value, Mapping):
        raise ValueError("invalid recurrence")
    every, count, interval = value.get("every"), value.get("count"), value.get("interval", 1)
    until = value.get("until")
    end = aware_instant(until)
    if (
        not isinstance(every, str) or type(count) is not int or type(interval) is not int
        or (until is not None and end is None)
    ):
        raise ValueError("invalid recurrence")
    return RecurrenceRule(Recurrence(every), zone, count, until=end, interval=interval)
