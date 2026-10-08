"""Contract for the Record read the rollup folds (`events_since`).

`nora.infra.sql.mobility.SqlMobilityStore` over real Postgres and
`nora.testing.mobility.FakeMobilityStore`. The property: a person's fixes and
their circle's successful polls come back as ONE stream in the canonical
order, `(recorded_ms, rank, id)`, a fix before a poll on a tie. The SQL store
reads the two sources with separate statements, so the merge is its own step;
a store that returned all fixes and then all polls would hand the fold a
poll before the fix that proves the clock moved, and every schedule of the
same Record would no longer fold to the same rollup.

Everything here is synthetic: invented person and circle keys, coordinates
around a neutral point.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from nora.domain.rollup import FIX_RANK, POLL_RANK, EventPosition, FixEvent, PollEvent
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.mobility import SqlMobilityStore
from nora.testing.mobility import FakeMobilityStore

T0 = 1_790_000_000_000
LAT, LNG = 22.3000, 114.1700
START = EventPosition(0, -1, 0)


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield FakeMobilityStore()
        return
    engine = create_sql_engine(request.getfixturevalue("pg_url"))
    try:
        yield SqlMobilityStore(engine)
    finally:
        await engine.dispose()


async def _fix(store: Any, *, id: int, ts: int, recorded: int) -> None:
    await store._seed_fix_for_test(
        id=id, person_id="p_alpha", ts=ts, lat=LAT, lng=LNG, accuracy_m=50.0,
        wire_state="live", recorded_at=recorded,
    )


async def _read(store: Any, *, limit: int = 50) -> list[Any]:
    return await store.events_since(
        person_ids=["p_alpha"], circle_ids=["c_one"], after=START,
        through_ms=T0 + 10 * 3_600_000, limit=limit,
    )


def _shape(events: list[Any]) -> list[tuple[str, int]]:
    return [
        ("fix" if isinstance(e, FixEvent) else "poll", e.position.recorded_ms - T0)
        for e in events
    ]


async def test_fixes_and_polls_merge_in_recorded_order_a_fix_first_on_a_tie(store):
    # Recorded order is: fix@10, POLL@20, fix@30, then a poll AND a fix both at 40.
    await _fix(store, id=1, ts=T0, recorded=T0 + 10)
    await _fix(store, id=2, ts=T0 + 5, recorded=T0 + 30)
    await _fix(store, id=3, ts=T0 + 9, recorded=T0 + 40)
    await store._seed_poll_for_test(at_ms=T0 + 15, recorded_ms=T0 + 20, scope="c_one")
    await store._seed_poll_for_test(at_ms=T0 + 35, recorded_ms=T0 + 40, scope="c_one")
    events = await _read(store)
    assert _shape(events) == [
        ("fix", 10), ("poll", 20), ("fix", 30), ("fix", 40), ("poll", 40),
    ]
    assert [e.position.rank for e in events if e.position.recorded_ms == T0 + 40] == [
        FIX_RANK, POLL_RANK,
    ]
    assert isinstance(events[1], PollEvent)


async def test_the_limit_cuts_the_merged_stream_not_each_source(store):
    # Three fixes recorded late, two polls recorded early: the first three
    # events of the MERGED stream are polls and the earliest fix.
    for i, rec in enumerate((100, 110, 120), start=1):
        await _fix(store, id=i, ts=T0 + i, recorded=T0 + rec)
    await store._seed_poll_for_test(at_ms=T0 + 10, recorded_ms=T0 + 10, scope="c_one")
    await store._seed_poll_for_test(at_ms=T0 + 20, recorded_ms=T0 + 20, scope="c_one")
    assert _shape(await _read(store, limit=3)) == [("poll", 10), ("poll", 20), ("fix", 100)]
