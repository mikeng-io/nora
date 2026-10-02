"""The outcome row keeps what the session spent, in real Postgres (the Cognition
cut-over's PR-7, migration 0090).

`nora_cognition_unit_tokens_total` is reset by every restart (2 to 24 a day), so a
session's input, output and cache-read tokens and its root `trace_id` are written on
the one row a session leaves at its end, `cognition_wake_outcome`. The in-memory store
and the unit tests hold the DOMAIN object; only a round trip through the SQL insert
shows that the column list names every field: a `values(...)` that drops `trace_id`
(or one token column) passes every test that reads the dataclass back. Each outcome the
code can write goes through the same insert, so each is read back from the table.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.ports import NewWakeOutcome
from nora.domain.stimuli import WAKE_OUTCOMES, NewStimulus, NewWake, WakeGuard
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COGNITION_WAKE_OUTCOME
from nora.infra.sql.stimuli import SqlStimulusStore

NOW = int(datetime(2026, 10, 2, 12, 0, tzinfo=UTC).timestamp() * 1000)
TRACE = "0af7651916cd43dd8448eb211c80319c"


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _wake(store: SqlStimulusStore, unit: str) -> int:
    sid = await store.append(stimulus=NewStimulus(
        unit=unit, kind="message", item="message", ref=f"messages:{unit}", at_ms=NOW,
        platform="signal", conversation_id="room", person_id="p",
    ), recorded_at_ms=NOW)
    assert sid is not None
    wake = await store.record_wake(wake=NewWake(
        unit=unit, mode="live", reason="settled", from_stimulus_id=0, through_stimulus_id=sid,
        stimulus_count=1, eligible_count=1, oldest_pending_ms=NOW, cut_cooldown=False,
        at_ms=NOW, tracked=True,
    ), recorded_at_ms=NOW, guard=WakeGuard(last_wake_id=None, last_outcome=None))
    assert wake is not None
    return wake


async def _row(engine: Any, wake_id: int) -> Any:
    o = COGNITION_WAKE_OUTCOME
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(
            o.c.outcome, o.c.input_tokens, o.c.output_tokens, o.c.cache_read_tokens, o.c.trace_id,
        ).where(o.c.wake_id == wake_id))).one()


@pytest.mark.parametrize("outcome", WAKE_OUTCOMES)
async def test_every_outcome_keeps_its_tokens_and_trace_id_in_the_row(engine, outcome):
    store = SqlStimulusStore(engine)
    wake = await _wake(store, f"unit-{outcome}")
    recorded = await store.record_outcome(outcome=NewWakeOutcome(
        wake_id=wake, unit=f"unit-{outcome}", outcome=outcome, at_ms=NOW + 60_000,
        input_tokens=81_234, output_tokens=1_357, cache_read_tokens=60_011, trace_id=TRACE,
    ), recorded_at_ms=NOW + 60_000)
    assert recorded.written and recorded.outcome == outcome
    row = await _row(engine, wake)
    assert (row.outcome, row.input_tokens, row.output_tokens, row.cache_read_tokens) == (
        outcome, 81_234, 1_357, 60_011,
    )
    assert row.trace_id == TRACE


async def test_usage_the_runner_could_not_read_is_null_in_the_row_never_zero(engine):
    """A restart's outcome, a session that failed before a model call, a provider that
    reported nothing: NULL, so the health command counts it `without_usage` rather than
    averaging a zero in."""
    store = SqlStimulusStore(engine)
    wake = await _wake(store, "unit-unread")
    await store.record_outcome(outcome=NewWakeOutcome(
        wake_id=wake, unit="unit-unread", outcome="cancelled", at_ms=NOW + 60_000,
    ), recorded_at_ms=NOW + 60_000)
    row = await _row(engine, wake)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.trace_id) == (
        None, None, None, None,
    )


async def test_a_zero_that_was_read_stays_a_zero(engine):
    """The other half: zero tokens READ is a fact (a cache-cold call with no cache read),
    and must not collapse into the unread NULL."""
    store = SqlStimulusStore(engine)
    wake = await _wake(store, "unit-zero")
    await store.record_outcome(outcome=NewWakeOutcome(
        wake_id=wake, unit="unit-zero", outcome="closed", at_ms=NOW + 60_000,
        input_tokens=500, output_tokens=40, cache_read_tokens=0, trace_id=TRACE,
    ), recorded_at_ms=NOW + 60_000)
    row = await _row(engine, wake)
    assert row.cache_read_tokens == 0 and row.input_tokens == 500
