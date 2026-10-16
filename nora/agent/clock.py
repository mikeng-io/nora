"""How Nora states the time: ONE formatter for every place she is told it.

Two surfaces tell her when it is: the clock line at the top of every turn
(`nora.agent.prefill._now_line`) and the `now` tool, which she may call in
the middle of one. Both read `local_now()` and render through `state_time`, so
they cannot disagree about anything but the minute that passed between them.

The place is the zone's own city (`Asia/Hong_Kong` is Hong Kong), not a
location feed: it is where the clock is, which is what the deployment's zone
was set to for (`docker-compose.yml`'s `TZ`, «The group is in Hong Kong»). A
zone that names no place (`UTC`, `Etc/GMT+8`) or cannot be named renders no
place, never a guessed one.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: The IANA areas whose zones are named for a city (`Asia/Hong_Kong`). The rest
#: (`UTC`, `Etc/GMT+8`) name an offset, and an offset is not a place.
_PLACE_AREAS = frozenset({
    "Africa", "America", "Antarctica", "Asia", "Atlantic", "Australia",
    "Europe", "Indian", "Pacific",
})


def local_now() -> datetime:
    """The one clock read both surfaces use: now, in the process's own zone."""
    return datetime.now().astimezone()


def local_zone() -> ZoneInfo | None:
    """The process's own IANA zone: `TZ` (the container sets it), else the zone
    `/etc/localtime` links to (a developer machine), else None. The same source
    `datetime.now().astimezone()` reads, so the zone named and the time stated
    cannot come from two different places."""
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        _, found, name = os.path.realpath("/etc/localtime").partition("zoneinfo/")
        if not found:
            return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def zone_key(now: datetime) -> str | None:
    """The IANA name of the zone `now` is stated in, or None.

    A `ZoneInfo`-aware clock carries its own key. `local_now()` carries only an
    offset and an abbreviation, so the process's own zone is named for it, and
    only when that zone's offset at `now` IS `now`'s offset: a clock stated in
    some other zone gets no name rather than the wrong one."""
    key = getattr(now.tzinfo, "key", None)
    if isinstance(key, str) and key:
        return key
    zone = local_zone()
    if zone is None or zone.utcoffset(now) != now.utcoffset():
        return None
    return zone.key


def zone_place(key: str) -> str | None:
    """The city an IANA zone is named for (`America/Argentina/Buenos_Aires` is
    Buenos Aires), or None for a zone that names an offset."""
    area, _, rest = key.partition("/")
    if area not in _PLACE_AREAS or not rest:
        return None
    return rest.rsplit("/", 1)[-1].replace("_", " ")


def state_time(now: datetime) -> str:
    """«Wednesday 23 September 2026, 23:07, Hong Kong time (HKT, Asia/Hong_Kong)»:
    the weekday, date, time, and whatever of the zone can be named."""
    stamp = now.strftime("%A %-d %B %Y, %H:%M")
    key = zone_key(now)
    if key is None:
        return f"{stamp} ({now.tzname()})"
    place = zone_place(key)
    where = f", {place} time" if place else ""
    return f"{stamp}{where} ({now.tzname()}, {key})"
