"""`nora cognition health [--since ISO] [--unit U] [--json]`: the reads behind it
(the Cognition cut-over's safety net; `nora.domain.cognition_health` holds the
report, the thresholds and the verdict).

**Read-only: plain SELECTs, nothing is written, no model is called.** It answers
from durable rows because the Prometheus series that would answer are reset by
every restart (2 to 24 a day) and a rare counter reads empty for ever. Counts,
ages and rule names only: a session's `detail` is reduced to a restart flag IN SQL
and never fetched, a refusal is its `refusal_reason` (a code constant) and never its
`refusal_detail`, and no stimulus ref, room id or person id is selected.

**Fail closed per section.** Each read is its own `_try`: a section that cannot be
read lands in `HealthReport.errors` with the exception CLASS name, and the verdict
is ERROR, never a table that reads as zero.

The pending read is `SqlStimulusStore.view`, the evaluator's own, so a watermark,
a carry and a retraction are applied by the one rule that decides what wakes a unit.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime, tzinfo
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.cognition_health import (
    HealthReport,
    RefusalRule,
    SessionCount,
    TokenStats,
    UnitState,
)
from nora.domain.stimuli import RESTART_DETAIL
from nora.infra.sql.schema import (
    COGNITION_SESSION,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    COGNITION_WOULD_WRITE,
    DEPLOY_REVISION,
    STIMULUS,
)
from nora.infra.sql.stimuli import SqlStimulusStore

_T = TypeVar("_T")

#: The pending read's bound, the evaluator's own (`nora.app.wake.DEFAULT_PENDING_LIMIT`).
PENDING_LIMIT = 1000
#: The evaluator reads this far behind the clock so a late commit is not consumed
#: unseen (`WakeEvaluator.settle_seconds`, twice the store timeout of 5 s).
SETTLE_SECONDS = 10.0


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _age_s(now_ms: int, then_ms: int | None) -> float | None:
    return None if then_ms is None else max(0.0, (now_ms - then_ms) / 1000)


def _is_restart(detail: Any) -> Any:
    return sa.or_(detail == RESTART_DETAIL, detail.like(f"{RESTART_DETAIL}:%"))


async def _try(section: str, call: Awaitable[_T]) -> tuple[_T | None, tuple[str, str] | None]:
    """Fail closed: a read that raises is an ERROR carrying only the exception's
    class name (a message can quote a row), never an empty answer."""
    try:
        return await call, None
    except Exception as exc:  # noqa: BLE001 - any failure is this section's ERROR
        return None, (section, type(exc).__name__)


async def read_sessions(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, unit: str | None, tz: tzinfo,
) -> tuple[SessionCount, ...]:
    """Sessions that STARTED in the window, by day (in `tz`), unit and how they
    ended. An outcome row names its session (`session_id`); none yet is `open`."""
    s, o = COGNITION_SESSION, COGNITION_WAKE_OUTCOME
    stmt = sa.select(
        s.c.started_at_ms, s.c.unit, o.c.outcome, o.c.cause,
        _is_restart(o.c.detail).label("restart"),
    ).select_from(s.outerjoin(o, o.c.session_id == s.c.id)).where(
        s.c.started_at_ms >= since_ms, s.c.started_at_ms < until_ms,
    )
    if unit is not None:
        stmt = stmt.where(s.c.unit == unit)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    counts: dict[tuple[str, str, str, str], int] = {}
    for r in rows:
        outcome = r.outcome or "open"
        if outcome == "cancelled":
            cause = "restart" if r.restart else "other"
        elif outcome in ("failed", "timeout"):
            cause = r.cause
        else:
            cause = "none"
        day = _at(int(r.started_at_ms)).astimezone(tz).date().isoformat()
        key = (day, r.unit, outcome, cause)
        counts[key] = counts.get(key, 0) + 1
    return tuple(SessionCount(*k, count=n) for k, n in sorted(counts.items()))


async def read_open_wakes(
    engine: AsyncEngine, *, now_ms: int, session_timeout_seconds: float, unit: str | None,
) -> tuple[int, int]:
    """`(open, expired)`: tracked wakes with no outcome row, and those recorded
    `session_timeout_seconds` ago or more. Not window-bound: an orphan is an orphan
    whenever it was written."""
    w, o = COGNITION_WAKE, COGNITION_WAKE_OUTCOME
    stale = _at(now_ms - int(session_timeout_seconds * 1000))
    stmt = sa.select(
        sa.func.count().label("open"),
        sa.func.count().filter(w.c.recorded_at < stale).label("expired"),
    ).where(w.c.tracked == 1, ~sa.exists().where(o.c.wake_id == w.c.id))
    if unit is not None:
        stmt = stmt.where(w.c.unit == unit)
    async with engine.connect() as conn:
        row = (await conn.execute(stmt)).one()
    return int(row.open), int(row.expired)


async def read_units(
    engine: AsyncEngine, *, now_ms: int, session_timeout_seconds: float, unit: str | None,
) -> tuple[UnitState, ...]:
    """Each unit with a stimulus: what waits, how old, and when it last woke."""
    store = SqlStimulusStore(engine)
    async with engine.connect() as conn:
        names = (await conn.execute(sa.select(STIMULUS.c.unit).distinct())).scalars().all()
        last_wake = {
            r.unit: int(r.at.timestamp() * 1000)
            for r in (await conn.execute(
                sa.select(
                    COGNITION_WAKE.c.unit,
                    sa.func.max(COGNITION_WAKE.c.recorded_at).label("at"),
                ).group_by(COGNITION_WAKE.c.unit),
            )).all()
        }
    out: list[UnitState] = []
    for name in sorted(n for n in names if unit is None or n == unit):
        view = await store.view(
            unit=name, mode="shadow", now_ms=now_ms - int(SETTLE_SECONDS * 1000),
            limit=PENDING_LIMIT,
            stale_before_ms=now_ms - int(session_timeout_seconds * 1000),
        )
        eligible = [x for x in view.pending if not x.waits]
        kinds: dict[str, int] = {}
        for x in eligible:
            kinds[x.kind] = kinds.get(x.kind, 0) + 1
        oldest = min((x.pending_since_ms for x in eligible), default=None)
        woke = last_wake.get(name)
        out.append(UnitState(
            unit=name, eligible=len(eligible), waiting=len(view.pending) - len(eligible),
            oldest_eligible_age_seconds=_age_s(now_ms, oldest),
            by_kind=kinds, truncated=len(view.pending) >= PENDING_LIMIT,
            last_wake_age_seconds=_age_s(now_ms, woke), failure_streak=view.failures,
        ))
    return tuple(out)


async def read_tokens(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, unit: str | None,
) -> TokenStats:
    """Mean and max per session over the sessions that started in the window. A
    session counts as `with_usage` when ANY of its three counters was read."""
    s, o = COGNITION_SESSION, COGNITION_WAKE_OUTCOME
    read = sa.or_(
        o.c.input_tokens.is_not(None), o.c.output_tokens.is_not(None),
        o.c.cache_read_tokens.is_not(None),
    )
    stmt = sa.select(
        sa.func.count().filter(read).label("with_usage"),
        sa.func.count().filter(~sa.func.coalesce(read, False)).label("without"),
        sa.func.avg(o.c.input_tokens).label("in_mean"),
        sa.func.max(o.c.input_tokens).label("in_max"),
        sa.func.avg(o.c.output_tokens).label("out_mean"),
        sa.func.max(o.c.output_tokens).label("out_max"),
        sa.func.avg(o.c.cache_read_tokens).label("cr_mean"),
        sa.func.max(o.c.cache_read_tokens).label("cr_max"),
        sa.func.sum(o.c.cache_read_tokens).filter(o.c.input_tokens.is_not(None)).label("cr_sum"),
        sa.func.sum(o.c.input_tokens).filter(o.c.cache_read_tokens.is_not(None)).label("in_sum"),
    ).select_from(s.outerjoin(o, o.c.session_id == s.c.id)).where(
        s.c.started_at_ms >= since_ms, s.c.started_at_ms < until_ms,
    )
    if unit is not None:
        stmt = stmt.where(s.c.unit == unit)
    async with engine.connect() as conn:
        r = (await conn.execute(stmt)).one()

    def f(v: Any) -> float | None:
        return None if v is None else float(v)

    def i(v: Any) -> int | None:
        return None if v is None else int(v)

    share = None if not r.in_sum or r.cr_sum is None else float(r.cr_sum) / float(r.in_sum)
    return TokenStats(
        with_usage=int(r.with_usage), without_usage=int(r.without),
        input_mean=f(r.in_mean), input_max=i(r.in_max),
        output_mean=f(r.out_mean), output_max=i(r.out_max),
        cache_read_mean=f(r.cr_mean), cache_read_max=i(r.cr_max), cache_share=share,
    )


async def read_refusals(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, unit: str | None,
) -> tuple[int, tuple[RefusalRule, ...]]:
    """`(verb calls, refusals by (tier, rule))` in the window. Rule NAMES only."""
    t = COGNITION_WOULD_WRITE
    base = (t.c.recorded_at >= _at(since_ms), t.c.recorded_at < _at(until_ms))
    calls = sa.select(sa.func.count()).where(*base)
    refused = sa.select(t.c.tier, t.c.refusal_reason, sa.func.count().label("n")).where(
        *base, t.c.outcome == "refused",
    ).group_by(t.c.tier, t.c.refusal_reason)
    if unit is not None:
        calls = calls.where(t.c.unit == unit)
        refused = refused.where(t.c.unit == unit)
    async with engine.connect() as conn:
        total = int((await conn.execute(calls)).scalar_one())
        rows = (await conn.execute(refused)).all()
    return total, tuple(
        RefusalRule(tier=r.tier, rule=r.refusal_reason or "unnamed", count=int(r.n),
                    share=int(r.n) / total if total else 0.0)
        for r in rows
    )


async def read_process_starts(
    engine: AsyncEngine, *, since_ms: int, until_ms: int,
) -> tuple[int, int]:
    """`(starts, distinct builds)`: `deploy_revision` has one row per process start."""
    d = DEPLOY_REVISION
    stmt = sa.select(
        sa.func.count().label("n"), sa.func.count(sa.distinct(d.c.git_sha)).label("builds"),
    ).where(d.c.started_at >= _at(since_ms), d.c.started_at < _at(until_ms))
    async with engine.connect() as conn:
        r = (await conn.execute(stmt)).one()
    return int(r.n), int(r.builds)


async def read_reconcile(engine: AsyncEngine) -> str:
    """The live ledger's reconcile needs `cognition_revert`, which the cut-over's
    revert step builds. Until then it cannot be computed, and says so: an absent
    ledger is «unavailable», never a clean zero, and it is not part of the verdict."""
    async with engine.connect() as conn:
        present = (await conn.execute(
            sa.text("select to_regclass('public.cognition_revert') is not null"),
        )).scalar_one()
    if not present:
        return "unavailable (no live ledger: nothing is committed live yet)"
    return "unavailable (the ledger exists; its reconcile is read by `nora cognition revert`)"


async def run_health(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, now_ms: int, unit: str | None,
    tz: tzinfo, session_timeout_seconds: float,
) -> HealthReport:
    errors: list[tuple[str, str]] = []

    async def section(name: str, call: Awaitable[_T]) -> _T | None:
        value, err = await _try(name, call)
        if err is not None:
            errors.append(err)
        return value

    sessions = await section("sessions", read_sessions(
        engine, since_ms=since_ms, until_ms=until_ms, unit=unit, tz=tz))
    open_wakes = await section("open wakes", read_open_wakes(
        engine, now_ms=now_ms, session_timeout_seconds=session_timeout_seconds, unit=unit))
    units = await section("pending", read_units(
        engine, now_ms=now_ms, session_timeout_seconds=session_timeout_seconds, unit=unit))
    tokens = await section("tokens", read_tokens(
        engine, since_ms=since_ms, until_ms=until_ms, unit=unit))
    refusals = await section("refusals", read_refusals(
        engine, since_ms=since_ms, until_ms=until_ms, unit=unit))
    starts = await section("process starts", read_process_starts(
        engine, since_ms=since_ms, until_ms=until_ms))
    reconcile = await section("reconcile", read_reconcile(engine))
    return HealthReport(
        since_ms=since_ms, until_ms=until_ms, now_ms=now_ms, unit=unit,
        sessions=sessions or (), units=units or (),
        open_wakes=open_wakes[0] if open_wakes else 0,
        expired_open_wakes=open_wakes[1] if open_wakes else 0,
        tokens=tokens, verb_calls=refusals[0] if refusals else 0,
        refusals=refusals[1] if refusals else (),
        process_starts=starts[0] if starts else 0,
        distinct_builds=starts[1] if starts else 0,
        reconcile=reconcile or "unavailable (not read)", errors=tuple(errors),
    )
