from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

_HKT = timezone(timedelta(hours=8), "HKT")


def utc_now() -> datetime:
    """The default clock for anything in this module that needs one.

    A NAMED function, not an inline `lambda: datetime.now(UTC)` in a signature
    and never `= datetime.now(UTC)`: ruff's `B008` rejects a call in an argument
    default, and it is right to — that form freezes the clock at import time, so
    every reminder set after the process booted would be measured against the
    moment it started. `nora.agent.registry` imports this as its own default, so
    the tool layer has ONE clock rather than two that can disagree.
    """
    return datetime.now(UTC)


def _parse_when(raw: str) -> datetime | None:
    """The model's `when`, as an instant — or `None` when it is not one."""
    try:
        parsed = datetime.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=_HKT)
    return parsed


def _parse_until(raw: str) -> datetime | None:
    """`until` as an instant — and a BARE DATE means the END of that day."""
    text = str(raw).strip()
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        return _parse_when(text)
    return datetime.combine(day, time.max).replace(tzinfo=_HKT)
