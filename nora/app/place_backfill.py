"""Backfill area names for the retained reverse-geocode cache."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from nora.app.gatherings import screened_area
from nora.domain.ports import PlaceArea

log = logging.getLogger("nora.app.place_backfill")


class _Cache(Protocol):
    async def without_area(self, *, limit: int) -> list[tuple[float, float]]: ...

    async def set_area(
        self, *, lat_rounded: float, lng_rounded: float, area: PlaceArea,
    ) -> bool: ...


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
    geocodes: Counts
    #: Why the cache was skipped, when no reverse geocoder was configured.
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
    *, cache: _Cache,
    geocoder: _AreaGeocoder | None, apply: bool = False, per_second: float = 2.0,
    limit: int = 10_000,
    content_checks: bool = True,
) -> PlaceBackfillReport:
    """See the module docstring."""
    pace = _Pace(per_second)
    report = PlaceBackfillReport(applied=apply, geocodes=Counts())
    if geocoder is None:
        report.geocodes_skipped = "no reverse geocoder ([presence] geocode_provider)"
    else:
        await _geocodes(
            cache,
            geocoder,
            report.geocodes,
            apply=apply,
            pace=pace,
            limit=limit,
            content_checks=content_checks,
        )
    return report


async def _geocodes(
    cache: _Cache, geocoder: _AreaGeocoder, counts: Counts, *, apply: bool, pace: _Pace,
    limit: int,
    content_checks: bool = True,
) -> None:
    pending = await cache.without_area(limit=limit)
    counts.found = len(pending)
    if not apply:
        return
    for lat, lng in pending:
        await pace.wait()
        counts.asked += 1
        area = screened_area(
            await geocoder.area_for(lat=lat, lng=lng), content_checks=content_checks
        )
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
    counts = report.geocodes
    if report.geocodes_skipped:
        lines.append(f"  cached geocodes: skipped, {report.geocodes_skipped}")
    else:
        line = f"  cached geocodes: {counts.found} without an area"
        if report.applied:
            line += (
                f", {counts.asked} lookup(s), {counts.placed} placed, "
                f"{counts.unnamed} named no area"
                + (f", {counts.failed} FAILED" if counts.failed else "")
            )
        lines.append(line)
        if report.applied and counts.placed:
            lines.append("  outings take the cached areas at the next Rollup tick")
    if not report.applied:
        lines.append("re-run with --apply to ask the geocoder and write")
    return lines
