"""`nora memory backfill-places [--apply] [--per-second N] [--limit N]`: give
the places resolved before `0118_place_area` their district and city.

design/unified-memory/00-intent.md «Sequence» step 3a. Two work lists, each
the rows that still name no area, so a re-run asks only what is left:

- **Event venues** (`SqlGatheringStore.without_venue_area`): one Geocoding
  lookup per distinct place id (`GoogleGeocoder.area_of_place`, by Google's
  own id, no coordinate out), screened like a live resolution
  (`nora.app.gatherings.screened_area`, R-28), written through
  `GatheringStore.update`, the path `update_event` writes through: a normal
  revision, projected into the memory index in the same transaction.
- **Cached geocodes** (`SqlGeocodeCacheStore.without_area`): the cached
  coordinate asked again of the configured reverse geocoder
  (`area_for`), the areas screened (R-28) and written beside the
  address. These are coordinates a `where_is` question already sent to
  the same geocoder (DEC-69's collector egress); nothing new is located.
  An outing takes its area from this cache at the next Rollup tick
  (`nora.analytics.units`), which recomputes every outing and re-projects
  the ones that changed; an outing at a saved place still takes none
  (R-35).

A dry run by default: it counts the work lists and calls nothing. `--apply`
asks at most `per_second` lookups a second (each is two calls, one per
language) and writes. A place the geocoder names no area for stays as it
is and is asked again by the next run. Prints counts only, never an id, a
name or a coordinate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from nora.app.gatherings import screened_area
from nora.domain.gatherings import Gathering
from nora.domain.ports import PlaceArea

log = logging.getLogger("nora.app.place_backfill")


class _Events(Protocol):
    async def without_venue_area(self, *, limit: int) -> list[Gathering]: ...

    async def update(
        self, event: Gathering, *, base_revision_id: UUID | None = None,
    ) -> Gathering: ...


class _Cache(Protocol):
    async def without_area(self, *, limit: int) -> list[tuple[float, float]]: ...

    async def set_area(
        self, *, lat_rounded: float, lng_rounded: float, area: PlaceArea,
    ) -> bool: ...


class _PlaceAreas(Protocol):
    async def area_of_place(self, place_id: str) -> PlaceArea | None: ...


class _AreaGeocoder(Protocol):
    async def area_for(self, *, lat: float, lng: float) -> PlaceArea | None: ...


@dataclass
class Counts:
    """One work list: rows found, lookups asked, rows written, lookups that
    named no area, and failures."""

    found: int = 0
    asked: int = 0
    placed: int = 0
    unnamed: int = 0
    failed: int = 0


@dataclass
class PlaceBackfillReport:
    applied: bool
    venues: Counts
    geocodes: Counts
    #: Why a list was not read at all (no Maps key, no geocoder), else None.
    venues_skipped: str | None = None
    geocodes_skipped: str | None = None


class _Pace:
    """At most `per_second` lookups a second, measured from each start."""

    def __init__(self, per_second: float) -> None:
        self._gap = 1.0 / per_second if per_second > 0 else 0.0
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        if now < self._next:
            await asyncio.sleep(self._next - now)
        self._next = max(now, self._next) + self._gap


async def backfill_places(
    *, events: _Events, cache: _Cache, places: _PlaceAreas | None,
    geocoder: _AreaGeocoder | None, apply: bool = False, per_second: float = 2.0,
    limit: int = 10_000,
) -> PlaceBackfillReport:
    """See the module docstring."""
    pace = _Pace(per_second)
    report = PlaceBackfillReport(applied=apply, venues=Counts(), geocodes=Counts())
    if places is None:
        report.venues_skipped = "no Maps key ([route] enabled with its key)"
    else:
        await _venues(events, places, report.venues, apply=apply, pace=pace, limit=limit)
    if geocoder is None:
        report.geocodes_skipped = "no reverse geocoder ([presence] geocode_provider)"
    else:
        await _geocodes(cache, geocoder, report.geocodes, apply=apply, pace=pace, limit=limit)
    return report


async def _venues(
    events: _Events, places: _PlaceAreas, counts: Counts, *, apply: bool, pace: _Pace,
    limit: int,
) -> None:
    pending = await events.without_venue_area(limit=limit)
    counts.found = len(pending)
    if not apply:
        return
    by_place: dict[str, list[Gathering]] = {}
    for event in pending:
        if event.venue_place_id:
            by_place.setdefault(event.venue_place_id, []).append(event)
    for place_id, group in by_place.items():
        await pace.wait()
        counts.asked += 1
        try:
            area = screened_area(await places.area_of_place(place_id))
        except Exception:
            log.exception("backfill-places: a venue lookup failed")
            counts.failed += len(group)
            continue
        if area is None:
            counts.unnamed += len(group)
            continue
        counts.placed += await _write_events(events, group, area, counts)


async def _write_events(
    events: _Events, group: Sequence[Gathering], area: PlaceArea, counts: Counts,
) -> int:
    written = 0
    for event in group:
        try:
            await events.update(
                replace(event, venue_district=area.district, venue_city=area.city),
                base_revision_id=event.head_revision_id,
            )
        except Exception:
            log.exception("backfill-places: an event write failed")
            counts.failed += 1
            continue
        written += 1
    return written


async def _geocodes(
    cache: _Cache, geocoder: _AreaGeocoder, counts: Counts, *, apply: bool, pace: _Pace,
    limit: int,
) -> None:
    pending = await cache.without_area(limit=limit)
    counts.found = len(pending)
    if not apply:
        return
    for lat, lng in pending:
        await pace.wait()
        counts.asked += 1
        area = screened_area(await geocoder.area_for(lat=lat, lng=lng))
        if area is None:
            counts.unnamed += 1
            continue
        try:
            if await cache.set_area(lat_rounded=lat, lng_rounded=lng, area=area):
                counts.placed += 1
        except Exception:
            log.exception("backfill-places: a geocode cache write failed")
            counts.failed += 1


def render(report: PlaceBackfillReport) -> list[str]:
    head = "APPLIED" if report.applied else "dry run: nothing asked, nothing written"
    lines = [f"memory backfill-places ({head})"]
    for label, counts, skipped in (
        ("event venues", report.venues, report.venues_skipped),
        ("cached geocodes", report.geocodes, report.geocodes_skipped),
    ):
        if skipped:
            lines.append(f"  {label}: skipped, {skipped}")
            continue
        line = f"  {label}: {counts.found} without an area"
        if report.applied:
            line += (
                f", {counts.asked} lookup(s), {counts.placed} placed, "
                f"{counts.unnamed} named no area"
                + (f", {counts.failed} FAILED" if counts.failed else "")
            )
        lines.append(line)
    if report.applied and not report.geocodes_skipped and report.geocodes.placed:
        lines.append("  outings take the cached areas at the next Rollup tick")
    if not report.applied:
        lines.append("re-run with --apply to ask the geocoder and write")
    return lines
