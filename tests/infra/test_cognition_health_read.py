"""`nora cognition health` over real Postgres (the Cognition cut-over's PR-7): the
reads, the verdict and the exit code, each pinned by a seeded world. The tables are
the evaluator's own (`SqlStimulusStore`), so a wake, an outcome and a pending read
mean here exactly what they mean in production.

The four worlds the flip-day check must tell apart:

- healthy: sessions closed, nothing old waiting. PASS, exit 0.
- stalled: a stimulus that could wake a unit has waited past four hours. FAIL, exit 1.
- zero sessions while stimuli wait. FAIL, exit 1 (and zero sessions with NOTHING
  waiting is ERROR, exit 2: nothing to judge is not a pass).
- failures: more than a tenth of the sessions that ended failed or timed out. FAIL.

Counts, ages and rule names only: a canary in every text-bearing column never reaches
the report.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from nora.app.cognition_health import run_health
from nora.domain.cognition_health import (
    HealthReport,
    exit_code,
    judge,
)
from nora.domain.ports import NewWakeOutcome
from nora.domain.stimuli import NewStimulus, NewWake, WakeGuard, restart_detail
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COGNITION_SESSION
from nora.infra.sql.stimuli import SqlStimulusStore

HK = ZoneInfo("Asia/Hong_Kong")
NOW = int(datetime(2026, 10, 2, 12, 0, tzinfo=UTC).timestamp() * 1000)
HOUR = 3_600_000
DAY = 24 * HOUR
SINCE = NOW - DAY
TIMEOUT = 3900.0
CANARY = "CANARY-TEXT-7731"


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _stimulus(store: SqlStimulusStore, unit: str, at: int, *, waits: bool = False,
                    kind: str = "message") -> int:
    sid = await store.append(stimulus=NewStimulus(
        unit=unit, kind=kind, item=kind, ref=f"messages:{CANARY}:{at}:{waits}", at_ms=at,  # type: ignore[arg-type]
        platform="signal", conversation_id=f"room-{CANARY}", person_id=f"person-{CANARY}",
        waits=waits,
    ), recorded_at_ms=at)
    assert sid is not None
    return sid


async def _session(
    engine: Any, store: SqlStimulusStore, unit: str, at: int, outcome: str | None, *,
    cause: str = "infra", detail: str = "", through: int = 1,
    tokens: tuple[int, int, int] | None = None, tracked: bool = True,
) -> int:
    """A wake, its session row and (unless `outcome` is None) its outcome."""
    view = await store.view(unit=unit, mode="live", now_ms=at, limit=10)
    last = view.last_wake
    wake = await store.record_wake(wake=NewWake(
        unit=unit, mode="live", reason="settled", from_stimulus_id=0,
        through_stimulus_id=through, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=at, cut_cooldown=False, at_ms=at, tracked=tracked,
    ), recorded_at_ms=at, guard=WakeGuard(
        last_wake_id=None if last is None else last.id,
        last_outcome=None if view.last_session is None else view.last_session.outcome,
    ) if tracked else None)
    assert wake is not None
    async with engine.begin() as conn:
        sid = (await conn.execute(sa.insert(COGNITION_SESSION).values(
            unit=unit, mode="live", started_at_ms=at, from_stimulus_id=1,
            through_stimulus_id=through, situation={}, agenda={"items": []},
            wake_id=wake, recorded_at=datetime.fromtimestamp(at / 1000, tz=UTC),
        ).returning(COGNITION_SESSION.c.id))).scalar_one()
    if outcome is not None:
        i, o, c = tokens or (None, None, None)
        await store.record_outcome(outcome=NewWakeOutcome(
            wake_id=wake, unit=unit, outcome=outcome, at_ms=at + 60_000, detail=detail,
            session_id=sid, cause=cause, input_tokens=i, output_tokens=o, cache_read_tokens=c,
        ), recorded_at_ms=at + 60_000)
    return sid


async def _health(engine: Any, **kw: Any) -> HealthReport:
    return await run_health(
        engine, since_ms=SINCE, until_ms=NOW, now_ms=NOW, unit=kw.pop("unit", None), tz=HK,
        session_timeout_seconds=TIMEOUT, **kw,
    )


def _verdict(report: HealthReport) -> tuple[str, list[str]]:
    level, findings = judge(report)
    return level, [f.code for f in findings]


ISO = {"since": "s", "until": "u"}


# ---- the four worlds --------------------------------------------------------------------


async def test_a_healthy_loop_passes_with_exit_zero(engine):
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - HOUR)            # one hour old: waiting, not stalled
    for k in range(6):
        await _session(engine, store, "alpha", NOW - (10 - k) * HOUR // 2, "closed",
                       tokens=(80_000, 1_000, 60_000))
    report = await _health(engine)
    assert _verdict(report) == ("PASS", [])
    assert exit_code(judge(report)[0]) == 0
    assert report.sessions_with("closed") == 6 and report.eligible_pending == 1


async def test_a_stimulus_waiting_past_four_hours_is_a_stalled_loop(engine):
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - 5 * HOUR)
    await _session(engine, store, "beta", NOW - 2 * HOUR, "closed")   # sessions exist elsewhere
    level, codes = _verdict(await _health(engine))
    assert (level, codes) == ("FAIL", ["stalled"])
    assert exit_code(level) == 1


async def test_a_stimulus_a_wake_waits_on_is_not_stale_just_because_it_is_old(engine):
    """Only stimuli that COULD wake the unit count: a reply riding the next wake
    (`waits`) is old by design and never a stall."""
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - 9 * HOUR, waits=True)
    await _session(engine, store, "alpha", NOW - HOUR, "closed", through=0)
    assert _verdict(await _health(engine)) == ("PASS", [])


INERT = frozenset({"time"})


async def test_a_time_stimulus_no_wake_can_claim_is_not_a_stalled_loop(engine):
    """`inert_kinds_for`: while no skill ships for `time`, a `time` stimulus is never
    claimed by a wake, so five hours of it is not a loop that stopped consuming.
    Production is in this state, and without it `nora cognition health` reported
    `stalled` and exited 1 on every flip day (review of #714). The control reads the
    same rows without the inert set and DOES report the stall."""
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - 5 * HOUR, kind="time")
    await _session(engine, store, "beta", NOW - 2 * HOUR, "closed")
    report = await _health(engine, inert_kinds=INERT)
    assert _verdict(report) == ("PASS", [])
    assert report.eligible_pending == 0
    assert _verdict(await _health(engine)) == ("FAIL", ["stalled"])


async def test_the_inert_set_skips_only_its_kind(engine):
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - 5 * HOUR, kind="time")
    await _stimulus(store, "alpha", NOW - 6 * HOUR, kind="message")
    await _session(engine, store, "beta", NOW - 2 * HOUR, "closed")
    report = await _health(engine, inert_kinds=INERT)
    assert _verdict(report) == ("FAIL", ["stalled"])
    assert report.eligible_pending == 1                   # the message, not the floor


async def test_zero_sessions_and_only_an_inert_stimulus_is_unknown_not_pending_work(engine):
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - HOUR, kind="time")
    assert _verdict(await _health(engine, inert_kinds=INERT)) == ("ERROR", ["no_sessions"])
    assert _verdict(await _health(engine)) == ("FAIL", ["no_sessions_with_pending"])


async def test_zero_sessions_while_a_stimulus_waits_fails(engine):
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - HOUR)
    level, codes = _verdict(await _health(engine))
    assert (level, codes) == ("FAIL", ["no_sessions_with_pending"])
    assert exit_code(level) == 1


async def test_zero_sessions_and_nothing_waiting_is_an_error_never_a_pass(engine):
    """An empty window is not a healthy loop, it is an unmeasured one: ERROR, exit 2."""
    level, codes = _verdict(await _health(engine))
    assert (level, codes) == ("ERROR", ["no_sessions"])
    assert exit_code(level) == 2


async def test_more_than_a_tenth_failed_is_a_failing_loop(engine):
    store = SqlStimulusStore(engine)
    for k in range(7):
        await _session(engine, store, "alpha", NOW - (20 - k) * HOUR // 2, "closed")
    for k in range(2):
        await _session(engine, store, "alpha", NOW - (6 - k) * HOUR // 2, "failed", cause="infra")
    await _session(engine, store, "alpha", NOW - HOUR // 2, "timeout", cause="infra")
    report = await _health(engine)
    assert _verdict(report) == ("FAIL", ["failure_ratio"])
    assert [(c.outcome, c.cause) for c in report.sessions if c.outcome != "closed"] == [
        ("failed", "infra"), ("timeout", "infra"),
    ]


async def test_a_ratio_over_too_few_sessions_is_reported_and_not_judged(engine):
    store = SqlStimulusStore(engine)
    await _session(engine, store, "alpha", NOW - 3 * HOUR, "closed")
    await _session(engine, store, "alpha", NOW - 2 * HOUR, "failed")
    assert _verdict(await _health(engine)) == ("PASS", [])         # 1 of 2, under the floor


async def test_a_restart_is_not_a_failure_in_the_ratio(engine):
    store = SqlStimulusStore(engine)
    for k in range(6):
        await _session(engine, store, "alpha", NOW - (20 - k) * HOUR // 2, "closed")
    for k in range(5):
        await _session(engine, store, "alpha", NOW - (8 - k) * HOUR // 2, "cancelled",
                       detail=restart_detail("the process was stopping"))
    report = await _health(engine)
    assert _verdict(report) == ("PASS", [])
    assert [c.cause for c in report.sessions if c.outcome == "cancelled"] == ["restart"]


async def test_a_wake_with_no_outcome_past_the_session_timeout_is_a_defect(engine):
    store = SqlStimulusStore(engine)
    await _session(engine, store, "alpha", NOW - 3 * HOUR, "closed")
    await _session(engine, store, "alpha", NOW - 2 * HOUR, None, through=2)  # killed, no outcome
    await _session(engine, store, "beta", NOW - 60_000, None)                  # young: in flight
    report = await _health(engine)
    assert (report.open_wakes, report.expired_open_wakes) == (2, 1)
    assert _verdict(report) == ("FAIL", ["expired_open_wake"])


# ---- counts only ------------------------------------------------------------------------




# ---- the sections -----------------------------------------------------------------------




async def test_pending_is_counted_per_unit_and_kind_by_the_evaluators_own_read(engine):
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - 2 * HOUR)
    await _stimulus(store, "alpha", NOW - HOUR, kind="time")
    await _stimulus(store, "alpha", NOW - HOUR, waits=True)
    await _stimulus(store, "beta", NOW - 30 * 60_000)
    await _session(engine, store, "gamma", NOW - HOUR, "closed")
    report = await _health(engine)
    alpha = next(u for u in report.units if u.unit == "alpha")
    assert (alpha.eligible, alpha.waiting, dict(alpha.by_kind)) == (2, 1, {"message": 1, "time": 1})
    assert alpha.oldest_eligible_age_seconds == pytest.approx(2 * 3600)
    assert next(u for u in report.units if u.unit == "beta").oldest_eligible_age_seconds == \
        pytest.approx(30 * 60)
    only = await _health(engine, unit="beta")
    assert [u.unit for u in only.units] == ["beta"]


async def test_a_killed_sessions_stimuli_are_pending_again_once_its_wake_expires(engine):
    """The evaluator's own read applies the expiry: a wake with no outcome older than the
    session timeout is read as failed, so what it claimed is pending again. A report that
    read the wake as still open would call the unit healthy while its input sat unseen."""
    store = SqlStimulusStore(engine)
    sid = await _stimulus(store, "alpha", NOW - 3 * HOUR)
    await _session(engine, store, "alpha", NOW - 2 * HOUR, None, through=sid)   # killed, no outcome
    report = await _health(engine)
    alpha = next(u for u in report.units if u.unit == "alpha")
    assert (alpha.eligible, report.expired_open_wakes) == (1, 1)
    assert alpha.failure_streak == 1                      # the expiry reads as one failed wake


async def test_a_session_a_wake_claimed_does_not_leave_its_stimuli_pending(engine):
    """The pending count is the WATERMARK's: a closed wake consumed them. A report
    that counted every stimulus ever raised would read every unit as stalled."""
    store = SqlStimulusStore(engine)
    sid_old = await _stimulus(store, "alpha", NOW - 6 * HOUR)
    await _session(engine, store, "alpha", NOW - 5 * HOUR, "closed", through=sid_old)
    report = await _health(engine)
    assert report.eligible_pending == 0
    assert _verdict(report) == ("PASS", [])






async def test_days_are_counted_in_the_cognition_zone(engine):
    store = SqlStimulusStore(engine)
    # 23:30 HKT on 10-01 is 15:30 UTC; 00:30 HKT on 10-02 is 16:30 UTC the day before.
    late = int(datetime(2026, 10, 1, 15, 30, tzinfo=UTC).timestamp() * 1000)
    early = int(datetime(2026, 10, 1, 16, 30, tzinfo=UTC).timestamp() * 1000)
    await _session(engine, store, "alpha", late, "closed")
    await _session(engine, store, "alpha", early, "closed")
    days = sorted(c.day for c in (await _health(engine)).sessions)
    assert days == ["2026-10-01", "2026-10-02"]


async def test_a_session_with_no_outcome_yet_is_open(engine):
    store = SqlStimulusStore(engine)
    await _session(engine, store, "alpha", NOW - 60_000, None)
    report = await _health(engine)
    assert [(c.outcome, c.cause) for c in report.sessions] == [("open", "none")]


async def test_the_report_writes_nothing(engine):
    """READ-ONLY: every table's row count before and after, the history table included
    (it grows on any in-place write), over a world that has every kind of row."""
    store = SqlStimulusStore(engine)
    await _stimulus(store, "alpha", NOW - HOUR)
    await _session(engine, store, "alpha", NOW - 3 * HOUR, "closed", tokens=(1, 2, 3))
    await _session(engine, store, "alpha", NOW - 2 * HOUR, None, through=2)

    async def counts() -> dict[str, int]:
        async with engine.connect() as conn:
            tables = (await conn.execute(sa.text(
                "select tablename from pg_tables where schemaname = 'public'"
            ))).scalars().all()
            return {
                t: (await conn.execute(
                    sa.text(f'select count(*) from "{t}"'),  # noqa: S608 - names from pg_tables
                )).scalar_one()
                for t in tables
            }

    before = await counts()
    report = await _health(engine)
    assert report.total_sessions == 2                         # positive control: it read
    assert await counts() == before
