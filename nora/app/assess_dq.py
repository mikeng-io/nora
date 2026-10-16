"""The Assess adaptor's nightly, deterministic data-quality checks
(`nora.domain.assess.DQ_CHECKS`, in that order). One function per check, each
a plain read over an `AsyncConnection` with no model, returning a
`DqCheckResult`.

`sections_past_age_horizon` and `induced_line_no_aggregate` always return
`not_applicable`: they need per-section `last_supported_at` and
`reasoning = 'induced'` rows that no table here carries. A check that queried
an absent table and reported "0 failing" would be indistinguishable from one
that ran and found nothing wrong; fail-closed (`AGENTS.md`) keeps those
different facts.

`rollup_rebuild_divergence` takes both builds from its caller. Producing the
second build (`nora.analytics.runner.RollupRunner.rebuild(switch=False)`) costs
a real rebuild, so this module only diffs two already-computed builds;
`nora.app.assess_run` decides whether and how often to pay for a fresh one.

One check raising never drops the report. `run_deterministic_checks` runs each
check through `_isolated`, which catches whatever that check raises and
reports it as `status="error"` (exception type and message in `detail`).
`error` is its own `DqStatus`, distinct from `fail`: a check that never
reached a verdict is not a check that found a violation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.assess import DqCheckResult
from nora.infra.sql.schema import (
    EVENTS,
    PRESENCE_STAYS,
    ROLLUP_OUTING,
)

__all__ = [
    "cross_room_duplicate_events",
    "empty_lineage_rows",
    "happened_place_mismatch",
    "induced_line_no_aggregate",
    "rollup_rebuild_divergence",
    "run_deterministic_checks",
    "sections_past_age_horizon",
]

_SAMPLE_LIMIT = 5


def _sample(keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(keys[:_SAMPLE_LIMIT])


async def cross_room_duplicate_events(conn: AsyncConnection) -> DqCheckResult:
    """Pairs of events of the same unit, in different rooms, with overlapping
    intervals and the same resolved venue: likely one happening. Candidates
    are listed for the operator, never merged."""
    e1 = EVENTS.alias("e1")
    e2 = EVENTS.alias("e2")
    live = ("proposed", "confirmed", "happened")
    rows = await conn.execute(
        sa.select(e1.c.id, e2.c.id)
        .select_from(e1.join(e2, sa.and_(
            e1.c.unit_id == e2.c.unit_id,
            e1.c.unit_id != "",
            e1.c.conversation_id != e2.c.conversation_id,
            e1.c.id < e2.c.id,
        )))
        .where(
            e1.c.status.in_(live), e2.c.status.in_(live),
            e1.c.venue_place_id.isnot(None),
            e1.c.venue_place_id == e2.c.venue_place_id,
            e1.c.when.isnot(None), e2.c.when.isnot(None),
            e1.c.when <= sa.func.coalesce(e2.c.end, e2.c.when),
            e2.c.when <= sa.func.coalesce(e1.c.end, e1.c.when),
        )
    )
    pairs = [f"{a}/{b}" for a, b in rows]
    if not pairs:
        return DqCheckResult(check="cross_room_duplicate_events", status="ok")
    return DqCheckResult(
        check="cross_room_duplicate_events", status="fail", failing=len(pairs),
        sample=_sample(pairs),
        detail="same unit, different rooms, overlapping interval, same venue_place_id",
    )


async def rollup_rebuild_divergence(
    conn: AsyncConnection, *, build_a: int | None, build_b: int | None,
    since_ms: int, through_ms: int,
) -> DqCheckResult:
    """Diffs `presence_stays` between two already-built Rollup builds over
    one window: the incremental build in service and a from-scratch batch
    rebuild produced for the same watermark. The incremental path and a batch
    rebuild are the same function and must agree exactly; any differing row
    is a failure."""
    if build_a is None or build_b is None:
        return DqCheckResult(
            check="rollup_rebuild_divergence", status="not_applicable",
            detail="no second build was rebuilt this run (opt-in, 25-rollup.md §2's cost caveat)",
        )
    def _select(build_id: int) -> sa.Select[Any]:
        return sa.select(
            PRESENCE_STAYS.c.subject_id, PRESENCE_STAYS.c.started_ts,
            PRESENCE_STAYS.c.ended_ts, PRESENCE_STAYS.c.place_id,
        ).where(
            PRESENCE_STAYS.c.build_id == build_id,
            PRESENCE_STAYS.c.started_ts >= since_ms,
            PRESENCE_STAYS.c.started_ts < through_ms,
        )

    rows_a = {tuple(r) for r in await conn.execute(_select(build_a))}
    rows_b = {tuple(r) for r in await conn.execute(_select(build_b))}
    diff = rows_a ^ rows_b
    if not diff:
        return DqCheckResult(check="rollup_rebuild_divergence", status="ok")
    sample = [str(row) for row in list(diff)[:_SAMPLE_LIMIT]]
    return DqCheckResult(
        check="rollup_rebuild_divergence", status="fail", failing=len(diff),
        sample=tuple(sample),
        detail=f"stays differ between build {build_a} and rebuild {build_b}",
    )


def _epoch_ms(col: sa.ColumnElement[Any]) -> sa.ColumnElement[Any]:
    return sa.cast(sa.func.extract("epoch", col) * 1000, sa.BigInteger)


async def happened_place_mismatch(conn: AsyncConnection) -> DqCheckResult:
    """A `happened` event whose place differs from the Rollup outing that
    covers its interval and people."""
    events_ms = (
        sa.select(
            EVENTS.c.id, EVENTS.c.unit_id, EVENTS.c.venue_place_id, EVENTS.c.people,
            _epoch_ms(EVENTS.c.when).label("when_ms"),
            sa.func.coalesce(
                _epoch_ms(EVENTS.c.end), _epoch_ms(EVENTS.c.when),
            ).label("end_ms"),
        )
        .where(EVENTS.c.status == "happened", EVENTS.c.when.isnot(None))
        .cte("happened_events_ms")
    )
    rows = await conn.execute(
        sa.select(events_ms.c.id, events_ms.c.venue_place_id, ROLLUP_OUTING.c.saved_place_id)
        .select_from(
            events_ms.join(
                ROLLUP_OUTING,
                sa.and_(
                    ROLLUP_OUTING.c.unit == events_ms.c.unit_id,
                    ROLLUP_OUTING.c.persons.overlap(events_ms.c.people),
                    ROLLUP_OUTING.c.valid_from_ms <= events_ms.c.end_ms,
                    ROLLUP_OUTING.c.valid_to_ms >= events_ms.c.when_ms,
                ),
            )
        )
        .where(
            events_ms.c.venue_place_id.isnot(None),
            ROLLUP_OUTING.c.saved_place_id.isnot(None),
            events_ms.c.venue_place_id != ROLLUP_OUTING.c.saved_place_id,
        )
    )
    offenders = [str(r.id) for r in rows]
    if not offenders:
        return DqCheckResult(check="happened_place_mismatch", status="ok")
    return DqCheckResult(
        check="happened_place_mismatch", status="fail", failing=len(offenders),
        sample=_sample(offenders),
        detail="event.venue_place_id differs from its covering outing's saved_place_id",
    )


async def sections_past_age_horizon(conn: AsyncConnection) -> DqCheckResult:
    """Always `not_applicable`: `last_supported_at`/`support_count` are
    per-section columns on Character and Standing sections, and no per-section
    age exists on this deployment."""
    return DqCheckResult(
        check="sections_past_age_horizon", status="not_applicable",
        detail="Character (step 8) has not shipped; no per-section age column exists yet",
    )


async def induced_line_no_aggregate(conn: AsyncConnection) -> DqCheckResult:
    """Always `not_applicable`: `induced` is a Character/Lessons-only reasoning
    mode and no row carries it."""
    return DqCheckResult(
        check="induced_line_no_aggregate", status="not_applicable",
        detail="Character/Lessons (step 8) have not shipped; reasoning='induced' has no writer",
    )


async def empty_lineage_rows(conn: AsyncConnection) -> DqCheckResult:
    """A live event head with `lineage = {}`: a row no Cognition write verb has
    stated a lineage for. Expected to fail while writers leave it empty; the
    count makes the day it stops being total visible."""
    rows = await conn.execute(
        sa.select(EVENTS.c.id).where(EVENTS.c.lineage == sa.text("'{}'::jsonb"))
    )
    offenders = [str(r.id) for r in rows]
    if not offenders:
        return DqCheckResult(check="empty_lineage_rows", status="ok")
    return DqCheckResult(
        check="empty_lineage_rows", status="fail", failing=len(offenders),
        sample=_sample(offenders),
        detail="event head rows with lineage={}  (expected total before step 3's write verbs)",
    )


async def _isolated(check: str, awaitable: Awaitable[DqCheckResult]) -> DqCheckResult:
    """Run one check's coroutine to a result, never letting it raise past
    here. A check that raises (query error, connection drop, bug) reports
    `status="error"` with the exception's type and message in `detail`; it
    must not take the other checks, or verdicts already recorded, down with
    it. An ERROR is never a PASS."""
    try:
        return await awaitable
    except Exception as exc:  # noqa: BLE001 - isolate any check's failure, by design
        return DqCheckResult(
            check=check, status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )


async def run_deterministic_checks(
    conn: AsyncConnection, *,
    rollup_builds: tuple[int, int] | None = None,
    rollup_window_ms: tuple[int, int] = (0, 0),
) -> tuple[DqCheckResult, ...]:
    """Every check `nora.domain.assess.DQ_CHECKS` names, in that order, each
    run through `_isolated`."""
    build_a, build_b = rollup_builds if rollup_builds else (None, None)
    since_ms, through_ms = rollup_window_ms
    return (
        await _isolated(
            "cross_room_duplicate_events", cross_room_duplicate_events(conn),
        ),
        await _isolated("rollup_rebuild_divergence", rollup_rebuild_divergence(
            conn, build_a=build_a, build_b=build_b,
            since_ms=since_ms, through_ms=through_ms,
        )),
        await _isolated("happened_place_mismatch", happened_place_mismatch(conn)),
        await _isolated("sections_past_age_horizon", sections_past_age_horizon(conn)),
        await _isolated("induced_line_no_aggregate", induced_line_no_aggregate(conn)),
        await _isolated("empty_lineage_rows", empty_lineage_rows(conn)),
    )
