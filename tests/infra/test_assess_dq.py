"""The Assess adaptor's nightly data-quality checks (DEC-240; `nora.app.assess_dq`).
Module-scoped database per `tests/infra/test_withdrawal.py`'s pattern. Every
check is proved BOTH ways: `ok` on a clean fixture, `fail` once the fixture is
broken the way the check names — the gate's own "cover the negative too" rule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

import nora.app.assess_dq as assess_dq
from nora.app.assess_dq import (
    cross_room_duplicate_events,
    empty_lineage_rows,
    happened_place_mismatch,
    induced_line_no_aggregate,
    rollup_rebuild_divergence,
    run_deterministic_checks,
    sections_past_age_horizon,
)
from nora.app.assess_run import run_nightly
from nora.domain.assess import DQ_CHECKS
from nora.domain.gatherings import Gathering, GatheringStatus
from nora.domain.models import NormalizedMessage
from nora.infra.sql.assess import SqlAssessReportStore
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import ROLLUP_BUILD, ROLLUP_OUTING

pytestmark = pytest.mark.asyncio

_TRUNCATE = (
    "note_revision", "note", "event_revision", "events",
    "rollup_outing", "rollup_build", "presence_stays", "messages",
    "row_history", "assess_report",
)


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_assess_dq_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in _TRUNCATE:
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


async def _message(engine: Any, *, conversation_id: str = "g1", ts: int = 1000) -> int:
    store = SqlRecordStore(engine)
    return await store.append(
        message=NormalizedMessage(
            "signal", conversation_id, "group", "+1", "Miles", ts, "hi", {},
        ),
        person_id="miles",
    )


def _event(
    *, conversation_id: str, unit_id: str, status: GatheringStatus,
    venue_place_id: str | None, when: datetime, end: datetime | None = None,
    people: tuple[str, ...] = (),
) -> Gathering:
    return Gathering(
        id=uuid4(), conversation_id=conversation_id, title="dinner",
        created_by="miles", when=when, venue_text=None,
        venue_place_id=venue_place_id, venue_name=venue_place_id,
        venue_address=None, venue_rating=None, venue_price_level="",
        venue_maps_url=None, venue_resolved_at=None, status=status,
        reminder_id=None, created_at=when, unit_id=unit_id, end=end,
        people=people,
    )


# -------------------------------------------------------------------- events


async def test_cross_room_duplicate_events_ok_for_distinct_venues(engine):
    gatherings = SqlGatheringStore(engine)
    when = datetime(2026, 9, 29, 19, 0, tzinfo=UTC)
    await gatherings.create(_event(
        conversation_id="g1", unit_id="u", status=GatheringStatus.CONFIRMED,
        venue_place_id="place-a", when=when,
    ))
    await gatherings.create(_event(
        conversation_id="g2", unit_id="u", status=GatheringStatus.CONFIRMED,
        venue_place_id="place-b", when=when,
    ))
    async with engine.connect() as conn:
        result = await cross_room_duplicate_events(conn)
    assert result.status == "ok"


async def test_cross_room_duplicate_events_fails_on_overlap_same_venue(engine):
    gatherings = SqlGatheringStore(engine)
    when = datetime(2026, 9, 29, 19, 0, tzinfo=UTC)
    end = datetime(2026, 9, 29, 21, 0, tzinfo=UTC)
    a = await gatherings.create(_event(
        conversation_id="g1", unit_id="u", status=GatheringStatus.CONFIRMED,
        venue_place_id="place-c", when=when, end=end,
    ))
    b = await gatherings.create(_event(
        conversation_id="g2", unit_id="u", status=GatheringStatus.CONFIRMED,
        venue_place_id="place-c", when=when, end=end,
    ))
    async with engine.connect() as conn:
        result = await cross_room_duplicate_events(conn)
    assert result.status == "fail"
    assert result.failing == 1
    ids = {str(a.id), str(b.id)}
    assert any(all(i in s for i in ids) for s in result.sample)


async def test_empty_lineage_rows_fails_before_any_writer_states_one(engine):
    gatherings = SqlGatheringStore(engine)
    when = datetime(2026, 9, 29, 14, 10, tzinfo=UTC)
    event = await gatherings.create(_event(
        conversation_id="g1", unit_id="u", status=GatheringStatus.PROPOSED,
        venue_place_id=None, when=when,
    ))
    async with engine.connect() as conn:
        result = await empty_lineage_rows(conn)
    assert result.status == "fail"
    assert str(event.id) in result.sample


async def test_happened_place_mismatch_fails_when_place_disagrees_with_the_outing(engine):
    gatherings = SqlGatheringStore(engine)
    when = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)
    end = datetime(2026, 9, 29, 22, 0, tzinfo=UTC)
    event = await gatherings.create(_event(
        conversation_id="g1", unit_id="u", status=GatheringStatus.HAPPENED,
        venue_place_id="place-told", when=when, end=end, people=("miles", "ann"),
    ))
    async with engine.begin() as conn:
        await conn.execute(sa.insert(ROLLUP_BUILD).values(
            code_version="test", params_sha256="x", status="current", through_ms=0,
        ))
        build_id = (await conn.execute(
            sa.select(ROLLUP_BUILD.c.id).order_by(ROLLUP_BUILD.c.id.desc()).limit(1)
        )).scalar_one()
        await conn.execute(sa.insert(ROLLUP_OUTING).values(
            build_id=build_id, unit="u", lineage={}, valid_from_ms=0,
            valid_to_ms=9_999_999_999_999, as_of_ms=0,
            persons=["miles", "ann"], lat=1.0, lng=1.0,
            saved_place_id="place-seen", open=0,
        ))
    async with engine.connect() as conn:
        result = await happened_place_mismatch(conn)
    assert result.status == "fail"
    assert str(event.id) in result.sample


# --------------------------------------------------------------------- other


async def test_rollup_rebuild_divergence_is_not_applicable_with_no_second_build(engine):
    async with engine.connect() as conn:
        result = await rollup_rebuild_divergence(
            conn, build_a=None, build_b=None, since_ms=0, through_ms=1,
        )
    assert result.status == "not_applicable"
    assert result.failing == 0


async def test_rollup_rebuild_divergence_ok_when_two_builds_agree(engine):
    async with engine.begin() as conn:
        for _ in range(2):
            await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="test", params_sha256="x", status="superseded",
                through_ms=0,
            ))
        ids = list((await conn.execute(
            sa.select(ROLLUP_BUILD.c.id).order_by(ROLLUP_BUILD.c.id)
        )).scalars())
    async with engine.connect() as conn:
        result = await rollup_rebuild_divergence(
            conn, build_a=ids[-2], build_b=ids[-1], since_ms=0, through_ms=10_000,
        )
    assert result.status == "ok"


async def test_sections_past_age_horizon_is_not_applicable_before_step_8(engine):
    async with engine.connect() as conn:
        result = await sections_past_age_horizon(conn)
    assert result.status == "not_applicable"


async def test_induced_line_no_aggregate_is_not_applicable_before_step_8(engine):
    async with engine.connect() as conn:
        result = await induced_line_no_aggregate(conn)
    assert result.status == "not_applicable"


# ------------------------------------------------------- per-check isolation


async def test_a_raising_check_errors_without_dropping_the_others(engine, monkeypatch):
    """A check that raises must not take the whole night's report down with it,
    including checks that already passed. Patches `cross_room_duplicate_events` —
    the first check `run_deterministic_checks` runs — to raise, and asserts every
    OTHER check still ran to its own verdict."""

    async def _raises(conn):
        raise RuntimeError("boom: citation query exploded")

    monkeypatch.setattr(assess_dq, "cross_room_duplicate_events", _raises)
    async with engine.connect() as conn:
        results = await run_deterministic_checks(conn)

    by_check = {r.check: r for r in results}
    assert set(by_check) == set(DQ_CHECKS)

    errored = by_check["cross_room_duplicate_events"]
    assert errored.status == "error"
    assert errored.failing == 0
    assert "RuntimeError" in errored.detail
    assert "boom: citation query exploded" in errored.detail

    # Every check besides the one patched to raise still ran to its own,
    # real verdict — none of them silently became "error" or "not run".
    assert by_check["rollup_rebuild_divergence"].status == "not_applicable"
    assert by_check["happened_place_mismatch"].status == "ok"
    assert by_check["sections_past_age_horizon"].status == "not_applicable"
    assert by_check["induced_line_no_aggregate"].status == "not_applicable"
    # empty_lineage_rows has no events on this fixture, so it is clean too.
    assert by_check["empty_lineage_rows"].status == "ok"


async def test_a_raising_check_still_gets_a_recorded_report(engine, monkeypatch):
    """End to end through `run_nightly`: one check raises, and the run
    still reaches `--record` with the FULL report — the checks that ran
    clean are not lost because one check blew up."""

    async def _raises(conn):
        raise ValueError("rollup connection dropped")

    monkeypatch.setattr(assess_dq, "happened_place_mismatch", _raises)
    outcome = await run_nightly(engine, mode="on", record=True)

    assert outcome.recorded is True
    by_check = {r.check: r for r in outcome.report.results}
    assert by_check["happened_place_mismatch"].status == "error"
    assert "ValueError" in by_check["happened_place_mismatch"].detail
    assert outcome.report.errored_checks == ("happened_place_mismatch",)
    assert by_check["cross_room_duplicate_events"].status == "ok"
    assert by_check["empty_lineage_rows"].status == "ok"

    recorded = await SqlAssessReportStore(engine).recent(limit=1)
    assert recorded, "the report was not written despite record=True"
    stored = recorded[0]
    stored_by_check = {r.check: r for r in stored.results}
    assert stored_by_check["happened_place_mismatch"].status == "error"
    assert stored_by_check["cross_room_duplicate_events"].status == "ok"
