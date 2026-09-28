"""The Assess adaptor's nightly, deterministic data-quality checks
(design/cognition-loop/30-cognition.md §6's bullet list, in order,
`nora.domain.assess.DQ_CHECKS`). One function per check, each a plain read
over an `AsyncConnection` — no model, "the deterministic data-quality checks
stay code" (30 §6) — returning a `DqCheckResult`.

**Two checks are `not_applicable` today, honestly.** `sections_past_age_
horizon` and `induced_line_no_aggregate` read Character and Lessons rows
(`last_supported_at`, `reasoning = 'induced'`), and neither tier exists until
step 8 (`70-migration.md` row 8). A check that queried a table absent from
this deployment and reported "0 failing" would be indistinguishable from a
check that ran and found nothing wrong; CMS's fail-closed rule (`AGENTS.md`)
says those are different facts, so this module says so instead of guessing.

**`rollup_rebuild_divergence` takes both builds from its caller.** Producing
the second build (`nora.analytics.runner.RollupRunner.rebuild(switch=False)`)
is a real rebuild's cost (`25-rollup.md` §2's own caveat), so this module
only DIFFS two already-computed builds; `nora.app.assess_run` decides whether
and how often to pay for a fresh one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.assess import DqCheckResult
from nora.infra.sql.schema import (
    CITATION,
    CULTURE,
    EVENTS,
    MESSAGES,
    NOTE,
    PRESENCE_STAYS,
    ROLLUP_OUTING,
    STANDING,
)

__all__ = [
    "cross_room_duplicate_events",
    "empty_lineage_rows",
    "happened_place_mismatch",
    "induced_line_no_aggregate",
    "rollup_rebuild_divergence",
    "run_deterministic_checks",
    "sections_past_age_horizon",
    "unresolved_citations",
]

_SAMPLE_LIMIT = 5


def _sample(keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(keys[:_SAMPLE_LIMIT])


async def unresolved_citations(conn: AsyncConnection) -> DqCheckResult:
    """A citation whose cited message no longer resolves (hidden or gone)
    while the citing HEAD is not itself `unsupported_at` — the gap the
    withdrawal cascade (`nora.app.withdrawal`) should have closed but did
    not (a cascade that never ran over this id, or a message hidden by a
    path that never called it)."""
    note_rows = await conn.execute(
        sa.select(sa.literal("note").label("store"), NOTE.c.id.label("key"))
        .select_from(
            CITATION.join(NOTE, sa.cast(NOTE.c.id, sa.Text) == CITATION.c.citing_key)
            .outerjoin(
                MESSAGES,
                sa.cast(MESSAGES.c.id, sa.Text) == CITATION.c.cited_id,
            )
        )
        .where(
            CITATION.c.citing_store == "note",
            CITATION.c.cited_store == "message",
            NOTE.c.unsupported_at.is_(None),
            sa.or_(MESSAGES.c.id.is_(None), MESSAGES.c.hidden_at.isnot(None)),
        )
        .distinct()
    )
    standing_rows = await conn.execute(
        sa.select(
            sa.literal("standing").label("store"),
            (STANDING.c.platform + ":" + STANDING.c.conversation_id).label("key"),
        )
        .select_from(
            CITATION.join(
                STANDING,
                (STANDING.c.platform + ":" + STANDING.c.conversation_id)
                == CITATION.c.citing_key,
            )
            .outerjoin(
                MESSAGES,
                sa.cast(MESSAGES.c.id, sa.Text) == CITATION.c.cited_id,
            )
        )
        .where(
            CITATION.c.citing_store == "standing",
            CITATION.c.cited_store == "message",
            STANDING.c.unsupported_at.is_(None),
            sa.or_(MESSAGES.c.id.is_(None), MESSAGES.c.hidden_at.isnot(None)),
        )
        .distinct()
    )
    culture_rows = await conn.execute(
        sa.select(sa.literal("culture").label("store"), CULTURE.c.key.label("key"))
        .select_from(
            CITATION.join(CULTURE, CULTURE.c.key == CITATION.c.citing_key)
            .outerjoin(
                MESSAGES,
                sa.cast(MESSAGES.c.id, sa.Text) == CITATION.c.cited_id,
            )
        )
        .where(
            CITATION.c.citing_store == "culture",
            CITATION.c.cited_store == "message",
            CULTURE.c.unsupported_at.is_(None),
            sa.or_(MESSAGES.c.id.is_(None), MESSAGES.c.hidden_at.isnot(None)),
        )
        .distinct()
    )
    offenders = [
        f"{r.store}:{r.key}" for r in (*note_rows, *standing_rows, *culture_rows)
    ]
    if not offenders:
        return DqCheckResult(check="unresolved_citations", status="ok")
    return DqCheckResult(
        check="unresolved_citations", status="fail", failing=len(offenders),
        sample=_sample(offenders),
        detail="citing head not unsupported, but its cited message is hidden or gone",
    )


async def cross_room_duplicate_events(conn: AsyncConnection) -> DqCheckResult:
    """Two events of the same unit, in different rooms, with overlapping
    intervals and the same resolved venue — one happening the design says
    "candidates are listed for the operator, never merged by guess" (70 §5).
    This is that list, reported nightly rather than only at write time."""
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
    one window — the incremental build the deploy is running on, and a
    from-scratch batch rebuild `nora.app.assess_run` produced for the same
    watermark. Design 27's own measured 3.4% (25-rollup.md §2 «As built»)
    is the number this bounds going forward; equal rows is the design's own
    claim ("the incremental path and a batch rebuild are the same function
    and must agree exactly")."""
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
    covers its interval and people (40-register.md §5's own worked case:
    "the `Happened` section cites the outing")."""
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
    """NOT_APPLICABLE until step 8: `last_supported_at`/`support_count` are
    per-section columns on Character and Standing sections (10-tiers.md §3),
    and neither the sectioned Character account nor per-section Standing
    ages exist on this deployment yet (`70-migration.md` row 8)."""
    return DqCheckResult(
        check="sections_past_age_horizon", status="not_applicable",
        detail="Character (step 8) has not shipped; no per-section age column exists yet",
    )


async def induced_line_no_aggregate(conn: AsyncConnection) -> DqCheckResult:
    """NOT_APPLICABLE until step 8: `induced` is a Character/Lessons-only
    reasoning mode (10-tiers.md §4); neither tier writes a row yet."""
    return DqCheckResult(
        check="induced_line_no_aggregate", status="not_applicable",
        detail="Character/Lessons (step 8) have not shipped; reasoning='induced' has no writer",
    )


async def empty_lineage_rows(conn: AsyncConnection) -> DqCheckResult:
    """A live event head with `lineage = {}` — every row until the Cognition
    adaptor's write verbs state one (70-migration.md row 2, "no writer states
    either yet"), so this check is EXPECTED to fail on every deployment before
    step 3's writers land; it is reported so that expectation is measured,
    not assumed, and so the day it stops being total is visible."""
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


async def run_deterministic_checks(
    conn: AsyncConnection, *,
    rollup_builds: tuple[int, int] | None = None,
    rollup_window_ms: tuple[int, int] = (0, 0),
) -> tuple[DqCheckResult, ...]:
    """Every check `nora.domain.assess.DQ_CHECKS` names, in that order."""
    build_a, build_b = rollup_builds if rollup_builds else (None, None)
    since_ms, through_ms = rollup_window_ms
    return (
        await unresolved_citations(conn),
        await cross_room_duplicate_events(conn),
        await rollup_rebuild_divergence(
            conn, build_a=build_a, build_b=build_b,
            since_ms=since_ms, through_ms=through_ms,
        ),
        await happened_place_mismatch(conn),
        await sections_past_age_horizon(conn),
        await induced_line_no_aggregate(conn),
        await empty_lineage_rows(conn),
    )
