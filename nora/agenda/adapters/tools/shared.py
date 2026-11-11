from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from nora.agent.toolkit.clock import _parse_until
from nora.domain.reminders import (
    UNIT_SECONDS,
    Recurrence,
    RecurrenceRule,
    ReminderCaps,
    UnknownRecurrenceZoneError,
    occurrence_at,
)

log = logging.getLogger("nora.tools")


_PERIOD_DAYS = {
    # Sub-day units are 0 days — their first occurrence is always inside the
    # horizon, so they never need the one-period headroom the calendar units
    # get below.
    Recurrence.MINUTELY: 0,
    Recurrence.HOURLY: 0,
    Recurrence.DAILY: 1,
    Recurrence.WEEKLY: 7,
    Recurrence.MONTHLY: 31,
    Recurrence.YEARLY: 366,
}


@dataclass(frozen=True)
class _RepeatRefusal:
    """A `repeat`/`times`/`until` triple that cannot be honoured, as a value."""

    status: str
    note: str
    # The three-value tool vocabulary (R-27). Every one of these is `refused` —
    # the model can fix its own arguments — except the unresolvable-timezone
    # case, which is a deployment fault no sentence in the room can repair.
    outcome: str = "refused"


def _occurrences_through(
    *,
    every: Recurrence,
    step: int,
    tz: str,
    due_at: datetime,
    ends_at: datetime,
    cap: int,
) -> int:
    """How many occurrences of this rule land at or before `ends_at`, capped."""
    # Built INSIDE the caller's `try`, never here: an unresolvable
    # `[reminders].timezone` is a deployment fault that `_parse_repeat` answers
    # as `unavailable`, and a probe built outside that guard would raise out of the
    # tool — a silent turn (R-5). Adding an argument must not change which failure
    # mode a broken deployment produces.
    probe = RecurrenceRule(
        every=every,
        tz=tz,
        count=cap,
        until=ends_at,
        interval=step,
    )
    fits = 0
    for index in range(cap):
        if occurrence_at(anchor=due_at, rule=probe, index=index) > ends_at:
            break
        fits += 1
    return max(1, fits)


def _parse_repeat(
    *,
    repeat: object,
    every_n: object,
    times: object,
    until: object,
    due_at: datetime,
    caps: ReminderCaps,
) -> RecurrenceRule | _RepeatRefusal | None:
    """The model's three recurrence arguments as one rule — or None, or a refusal."""
    word = str(repeat or Recurrence.NONE.value).strip().lower()
    try:
        every = Recurrence(word)
    except ValueError:
        return _RepeatRefusal(
            "bad_repeat",
            f"I can't repeat something {word!r}. I do minutely, hourly, daily, "
            "weekly, monthly or yearly, and `every_n` multiplies any of them; "
            "say which is closest.",
        )
    if every is Recurrence.NONE:
        if every_n is not None or times is not None or until is not None:
            return _RepeatRefusal(
                "bad_bound",
                "You gave me how long to keep repeating but not what to repeat. "
                "Set `repeat` to minutely/hourly/daily/weekly/monthly/yearly, or "
                "drop the limit if it really is a one-off.",
            )
        return None

    step = 1
    if every_n is not None:
        try:
            step = int(str(every_n).strip())
        except (TypeError, ValueError):
            return _RepeatRefusal(
                "bad_repeat",
                "I couldn't read how many units apart. Give a plain number: "
                '`repeat="minutely", every_n=10` is every ten minutes.',
            )
        if step < 1:
            return _RepeatRefusal(
                "bad_repeat",
                "Every less-than-one is not a gap. Say how far apart they meant.",
            )

    # THE OPERATING FLOOR. Sub-day recurrence makes 「每分鐘」 sayable,
    # and a room runs ONE turn at a time — a series faster than
    # it can serve does not arrive sooner, it queues and starves real tags.
    # REFUSED here rather than clamped: a bound nobody was told about is a silent
    # failure, and 「最快五分鐘一次」 is a sentence she can say.
    unit_seconds = UNIT_SECONDS.get(every)
    if unit_seconds is not None and unit_seconds * step < caps.min_interval_seconds:
        floor_minutes = caps.min_interval_seconds // 60
        return _RepeatRefusal(
            "too_often",
            f"I can't repeat faster than every {floor_minutes} minutes. I only "
            f"wake up to check periodically, so anything quicker would just "
            f"arrive late and in clumps. Offer them every {floor_minutes} "
            "minutes, or a one-off nearer the time.",
        )

    ends_at: datetime | None = None
    if until is not None:
        ends_at = _parse_until(str(until))
        if ends_at is None:
            return _RepeatRefusal(
                "bad_bound",
                "I couldn't read the end date. Give it as a date like 2026-10-11, "
                "or say how many times instead.",
            )
        if ends_at < due_at:
            return _RepeatRefusal(
                "bad_bound",
                "That end date is before the first one would even go off. Check "
                "which date they meant: one of the two is wrong.",
            )

    # EVERYTHING that can raise on a bad rule lives inside ONE try:
    # `_occurrences_through` builds a probe `RecurrenceRule`, and outside this guard
    # an unresolvable `[reminders].timezone` would escape as an exception out of the
    # tool — a silent turn (R-5) — but ONLY on the `until` path. The same
    # deployment fault answers `unavailable` without `until`. Adding an argument
    # must not change which failure mode a broken deployment produces.
    try:
        count = _resolve_count(
            every=every,
            step=step,
            times=times,
            ends_at=ends_at,
            due_at=due_at,
            caps=caps,
        )
        if isinstance(count, _RepeatRefusal):
            return count
        return RecurrenceRule(
            every=every,
            tz=caps.timezone,
            count=count,
            until=ends_at,
            interval=step,
        )
    except UnknownRecurrenceZoneError:
        # A DEPLOYMENT fault, not a model one: `caps.timezone` comes from
        # `[reminders]`. `unavailable` is the honest status for it — she cannot
        # hold this, and nothing the person says would fix it.
        log.exception("recurrence timezone %r is not resolvable", caps.timezone)
        return _RepeatRefusal(
            "unavailable",
            "I can't set a repeating one right now because my calendar isn't "
            "set up properly. Say so plainly rather than promising.",
            outcome="unavailable",
        )
    except (ValueError, OverflowError):
        # An `every_n` past `MAX_INTERVAL`, or one whose occurrences leave the
        # representable calendar entirely (`yearly x 10000` is year 12026;
        # `minutely x 10**12` overflows `timedelta`). The multiplier is a number the
        # MODEL writes, so this is an ordinary refusal it can fix rather than a
        # deployment fault.
        return _RepeatRefusal(
            "bad_repeat",
            "That is too far apart for me to keep track of. Say a smaller gap: "
            "every few minutes, hours, days, weeks, months or years.",
        )


def _resolve_count(
    *,
    every: Recurrence,
    step: int,
    times: object,
    ends_at: datetime | None,
    due_at: datetime,
    caps: ReminderCaps,
) -> int | _RepeatRefusal:
    """How many occurrences the series may hold — the TIGHTER of every bound."""
    count = caps.max_occurrences_per_series
    if ends_at is not None:
        count = _occurrences_through(
            every=every,
            step=step,
            tz=caps.timezone,
            due_at=due_at,
            ends_at=ends_at,
            cap=caps.max_occurrences_per_series,
        )
    if times is None:
        return count
    try:
        # `str(...)` first, because the MODEL writes this: a JSON tool call can
        # land it as `14`, `"14"` or `14.0`, and `int(14.0)` silently truncates
        # while `int("14.7")` raises. One route in, one refusal.
        asked = int(str(times).strip())
    except (TypeError, ValueError):
        return _RepeatRefusal(
            "bad_bound",
            "I couldn't read how many times. Give me a plain number, or an end date instead.",
        )
    if asked < 1:
        return _RepeatRefusal(
            "bad_bound",
            "Fewer than one time is not a reminder. Say how many they meant, "
            "or set it as a one-off.",
        )
    return min(asked, count)


_UNKNOWN_OWNER = "someone I can no longer name"

_SELF_OWNER = "you (a reminder you set for yourself)"


def _reminders_ok_note(rows: list[dict]) -> str:
    """How to report a `list_reminders` answer (ADR 0015 §3), built per what"""
    if not rows:
        return "Nothing is waiting here, and that is a real answer."
    parts = [
        "This is everyone's reminders here; set_by says who set each one.",
    ]
    if any(r.get("repeat") != Recurrence.NONE.value for r in rows):
        parts.append(
            "A repeating row is a whole series: say how many are left "
            "(repeat_left) and when it ends (repeat_until) if it has an end."
        )
    if any(r.get("set_by") == _UNKNOWN_OWNER for r in rows):
        parts.append(
            f'A set_by of "{_UNKNOWN_OWNER}" is still a real reminder; you just '
            "can't say who set it."
        )
    return " ".join(parts)


def _owner_name(persons, person_id: str, *, ai_peer: str = "") -> str:
    """The display name for a reminder's requester — `PersonRef.name`."""
    if ai_peer and person_id == ai_peer:
        return _SELF_OWNER
    ref = getattr(persons, "by_person", {}).get(person_id)
    name = getattr(ref, "name", "") if ref is not None else ""
    return name or _UNKNOWN_OWNER


_EVENTS_OFF: Mapping[str, object] = {
    "status": "unavailable",
    "note": "Events aren't set up here. Say so, and don't pretend it's done.",
}

_NO_SUCH_EVENT: Mapping[str, object] = {
    "status": "refused",
    "note": "I don't know that event here. Check event_search for its id, and don't guess one.",
}

_NOT_AN_EVENT_ID: Mapping[str, object] = {
    "status": "refused",
    "note": "That doesn't look like an event id. Use one from event_search.",
}

_VENUE_REPORT: Mapping[str, str] = {
    "nothing_found": "Nobody here has said anything usable about places they "
    "like. Say so, and don't invent a pick.",
    "unavailable": "I can't read what people here like right now. Say you "
    "couldn't check, and don't guess a venue.",
}
