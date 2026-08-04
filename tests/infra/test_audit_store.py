from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy.exc import IntegrityError

from nora.domain.models import DeliveryStatus, NormalizedMessage
from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations


def _msg(conv="g1", body="hello", ts=1) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=ts, body=body,
        raw={"x": 1},
    )


@pytest.fixture
async def make_store(tmp_path):
    """Factory, not a bare store: the retention window is construction-time
    policy, so a test that needs a small window builds a store with one rather
    than reaching into a private attribute."""
    created = []

    def _make(*, max_per_conversation=500, name="messages.db"):
        path = tmp_path / name
        run_migrations(path)
        s = SqlAuditStore(
            create_sql_engine(path), max_per_conversation=max_per_conversation
        )
        created.append(s)
        return s

    yield _make
    for s in created:
        await s.aclose()


@pytest.fixture
async def store(make_store):
    return make_store()


async def test_append_returns_the_new_row_id(store):
    assert await store.append(message=_msg()) == 1
    assert await store.append(message=_msg()) == 2


async def test_recent_returns_typed_transcript_rows(store):
    await store.append(message=_msg())
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.sender_id == "+852111"
    assert row.sender_name == "Miles"
    assert row.body == "hello"
    assert row.is_nora is False
    assert row.delivery_status is DeliveryStatus.PENDING
    assert json.loads(row.raw_json)["x"] == 1


async def test_recent_is_oldest_first_and_limited(store):
    for i in range(5):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    rows = await store.recent(conversation_id="g1", limit=2)
    assert [r.body for r in rows] == ["m3", "m4"]


async def test_recent_is_scoped_to_one_conversation(store):
    await store.append(message=_msg(conv="g1", body="a"))
    await store.append(message=_msg(conv="g2", body="b"))
    assert [r.body for r in await store.recent(conversation_id="g1", limit=10)] == ["a"]


async def test_is_nora_round_trips(store):
    await store.append(message=_msg(body="reply"), is_nora=True)
    assert (await store.recent(conversation_id="g1", limit=1))[0].is_nora is True


async def test_delivery_status_round_trips(store):
    await store.append(message=_msg(), delivery_status=DeliveryStatus.SENT)
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.delivery_status is DeliveryStatus.SENT


async def test_recent_breaks_ts_ties_by_id(store):
    """Signal timestamps are milliseconds and a burst can share one, so the
    `id` tie-break decides order for rows the clock cannot separate. Every
    other ordering test uses distinct ts values and would pass with the
    tie-break removed."""
    for body in ("first", "second", "third"):
        await store.append(message=_msg(body=body, ts=100))
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.body for r in rows] == ["first", "second", "third"]
    assert [r.id for r in rows] == sorted(r.id for r in rows)


async def test_recent_limit_with_tied_ts_takes_the_highest_ids(store):
    for body in ("a", "b", "c", "d"):
        await store.append(message=_msg(body=body, ts=100))
    rows = await store.recent(conversation_id="g1", limit=2)
    assert [r.body for r in rows] == ["c", "d"]


async def test_append_does_not_prune(make_store):
    """The defect this closes: db.append ran an INSERT plus a correlated-subquery
    prune in ONE transaction per message. Under WAL with N adapters that yields
    SQLITE_BUSY, and the ingest stage's failure behaviour is to drop the
    message — silent audit loss, the one thing SQLite is here to prevent.
    Append is now a bare INSERT; pruning is a periodic sweep."""
    store = make_store(max_per_conversation=3)
    for i in range(10):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert len(await store.recent(conversation_id="g1", limit=1000)) == 10


async def test_concurrent_appends_all_land(store):
    await asyncio.gather(
        *(store.append(message=_msg(body=f"c{i}", ts=i)) for i in range(50))
    )
    assert len(await store.recent(conversation_id="g1", limit=1000)) == 50


async def test_aclose_releases_the_connection_pool(make_store):
    """`aclose` delegates to `AsyncEngine.dispose()`, which releases pooled
    connections and installs a fresh pool. It deliberately does NOT poison the
    engine — verified 2026-08-05: a query after `dispose()` succeeds, opening a
    new connection. So this asserts the pool was actually replaced, which is
    the guarantee, rather than asserting a later call raises, which it does
    not. This proves dispose() ran, not that pooled connections were actually
    reclaimed — SQLAlchemy exposes no public signal for the latter."""
    store = make_store()
    await store.append(message=_msg())
    pool_before = store._engine.pool
    await store.aclose()
    assert store._engine.pool is not pool_before


async def test_aclose_is_idempotent(make_store):
    store = make_store()
    await store.aclose()
    await store.aclose()


async def test_sqlite_busy_increments_the_metric_and_still_raises(tmp_path):
    """The metric exists because WAL permits one writer and an N-adapter burst
    produces SQLITE_BUSY. An untested counter is a counter that can silently
    stop counting — the failure mode this project has shipped twice.

    Provokes a genuine lock: a second engine holds BEGIN EXCLUSIVE while the
    store attempts an insert with busy_timeout=0.
    """
    import sqlalchemy as sa
    from sqlalchemy.exc import OperationalError

    path = tmp_path / "busy.db"
    run_migrations(path)

    class _Recorder:
        def __init__(self) -> None:
            self.names: list[str] = []

        def inc(self, name, **labels):
            self.names.append(name)

    holder = create_sql_engine(path, busy_timeout_ms=0)
    conn = await holder.connect()
    await conn.execute(sa.text("BEGIN EXCLUSIVE"))
    recorder = _Recorder()
    store = SqlAuditStore(create_sql_engine(path, busy_timeout_ms=0), metrics=recorder)
    try:
        with pytest.raises(OperationalError):
            await store.append(message=_msg())
        assert recorder.names == ["nora_store_busy_total"]
    finally:
        await conn.close()
        await holder.dispose()
        await store.aclose()


async def test_an_invalid_conversation_type_is_rejected_by_the_check_constraint(store):
    """`IntegrityError`, not a bare `Exception`: ruff's B017 rejects a blind
    `pytest.raises(Exception)` and `tests/**` does not ignore it, so a blind
    assertion would break the lint gate. It is also the weaker assertion — a
    typo raising AttributeError would satisfy it."""
    bad = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="channel",
        sender_id="+1", sender_name=None, timestamp=1, body="x", raw={},
    )
    with pytest.raises(IntegrityError):
        await store.append(message=bad)


async def test_set_delivery_status_updates_the_row(store):
    row_id = await store.append(message=_msg(), is_nora=True)
    await store.set_delivery_status(row_id=row_id, status=DeliveryStatus.SENT)
    assert (await store.recent(conversation_id="g1", limit=1))[0].delivery_status \
        is DeliveryStatus.SENT
    await store.set_delivery_status(row_id=row_id, status=DeliveryStatus.FAILED)
    assert (await store.recent(conversation_id="g1", limit=1))[0].delivery_status \
        is DeliveryStatus.FAILED


async def test_stats_counts_and_reports_the_schema_revision(store):
    from nora.infra.sql.migrations_runner import head_revision

    await store.append(message=_msg())
    await store.append(message=_msg(body="r"), is_nora=True)
    s = await store.stats()
    assert s["messages"] == 2
    assert s["conversations"] == 1
    assert s["nora_messages"] == 1
    assert s["latest_ts"] == 1
    assert s["schema_revision"] == head_revision()


async def test_stats_on_an_empty_store(store):
    s = await store.stats()
    assert s["messages"] == 0
    assert s["latest_ts"] == 0


async def test_stats_counts_distinct_conversations_and_the_max_ts(store):
    await store.append(message=_msg(conv="a", ts=5))
    await store.append(message=_msg(conv="b", ts=99))
    await store.append(message=_msg(conv="b", ts=7))
    s = await store.stats()
    assert s["conversations"] == 2
    assert s["latest_ts"] == 99
    assert s["messages"] == 3


async def test_prune_all_keeps_the_window_per_conversation(make_store):
    store = make_store(max_per_conversation=3)
    for i in range(10):
        await store.append(message=_msg(conv="A", body=f"a{i}", ts=i))
    for i in range(2):
        await store.append(message=_msg(conv="B", body=f"b{i}", ts=i))
    deleted = await store.prune_all()
    assert deleted == 7
    kept = await store.recent(conversation_id="A", limit=1000)
    assert [r.body for r in kept] == ["a7", "a8", "a9"]
    assert len(await store.recent(conversation_id="B", limit=1000)) == 2


async def test_prune_all_is_a_no_op_below_the_window(store):
    await store.append(message=_msg())
    assert await store.prune_all() == 0
