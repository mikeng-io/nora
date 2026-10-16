from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest
from sqlalchemy.exc import IntegrityError

from nora.domain.models import DeliveryStatus, NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations


def _msg(conv="g1", body="hello", ts=1) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=ts, body=body,
        raw={"x": 1},
    )


@pytest.fixture
async def make_store(pg_url):
    """Factory, not a bare store: the retention window is construction-time
    policy, so a test that needs a small window builds a store with one rather
    than reaching into a private attribute.

    Every store built here shares this test's own freshly-created Postgres
    database (`pg_url`); the schema is migrated once, idempotently."""
    run_migrations(pg_url)
    created = []

    def _make(*, max_per_conversation=500, metrics=None):
        s = SqlRecordStore(
            create_sql_engine(pg_url), max_per_conversation=max_per_conversation,
            metrics=metrics,
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
    """Append is a bare INSERT; pruning is a periodic sweep. A hot write path has
    no business doing retention: the ingest stage's failure behaviour is to drop
    the message, so an append that can fail on its prune means silent audit
    loss, the one thing the record store is here to prevent."""
    store = make_store(max_per_conversation=3)
    for i in range(10):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert len(await store.recent(conversation_id="g1", limit=1000)) == 10


async def test_concurrent_appends_all_land(store):
    """The MVCC probe: 50 appends fired concurrently all land. Under Postgres
    MVCC the writers do not serialise, and every row commits."""
    await asyncio.gather(
        *(store.append(message=_msg(body=f"c{i}", ts=i)) for i in range(50))
    )
    assert len(await store.recent(conversation_id="g1", limit=1000)) == 50


async def test_aclose_releases_the_connection_pool(make_store):
    """`aclose` delegates to `AsyncEngine.dispose()`, which releases pooled
    connections and installs a fresh pool. It deliberately does NOT poison the
    engine: a query after `dispose()` succeeds, opening a
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


async def test_set_delivery_outcome_updates_the_row(store):
    row_id = await store.append(message=_msg(), is_nora=True)
    await store.set_delivery_outcome(row_id=row_id, status=DeliveryStatus.SENT)
    assert (await store.recent(conversation_id="g1", limit=1))[0].delivery_status \
        is DeliveryStatus.SENT
    await store.set_delivery_outcome(row_id=row_id, status=DeliveryStatus.FAILED)
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


async def test_prune_all_takes_a_pruned_messages_edits_with_it(make_store):
    """`message_edit` is ON DELETE CASCADE. Without
    it the sweep would fail on the foreign key the first time an edited
    message aged out — and a sweep that raises stops pruning every room."""
    import sqlalchemy as sa

    from nora.domain.models import EditTarget

    store = make_store(max_per_conversation=1)
    old = await store.append(message=_msg(body="old", ts=1))
    await store.append_edit(message_id=old, message=dataclasses.replace(
        _msg(body="old, edited", ts=5), edit_of=EditTarget(sent_ts=1),
    ))
    await store.append(message=_msg(body="new", ts=2))
    assert await store.prune_all() == 1
    async with store._engine.connect() as conn:
        left = (await conn.execute(sa.text("select count(*) from message_edit"))).scalar()
    assert left == 0


async def test_prune_all_is_a_no_op_below_the_window(store):
    await store.append(message=_msg())
    assert await store.prune_all() == 0


async def test_prune_all_deletes_nothing_at_exactly_the_window(make_store):
    """Off-by-one guard on a delete path. `> keep` vs `>= keep` in the window
    filter is a one-character change that silently discards the oldest kept
    message on every sweep."""
    store = make_store(max_per_conversation=3)
    for i in range(3):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert await store.prune_all() == 0
    assert len(await store.recent(conversation_id="g1", limit=100)) == 3


async def test_prune_all_below_the_window_deletes_nothing(make_store):
    store = make_store(max_per_conversation=5)
    for i in range(2):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    assert await store.prune_all() == 0


async def test_prune_all_breaks_ts_ties_by_id_at_the_cutoff(make_store):
    """When the retention boundary falls inside a group of rows sharing one
    `ts`, the `id` tie-break decides who survives. Signal timestamps are
    milliseconds and a burst shares one, so this boundary is reachable in
    practice, not hypothetical."""
    store = make_store(max_per_conversation=2)
    for body in ("a", "b", "c", "d"):
        await store.append(message=_msg(body=body, ts=100))
    assert await store.prune_all() == 2
    assert [r.body for r in await store.recent(conversation_id="g1", limit=100)] == ["c", "d"]


async def test_prune_all_returns_the_number_actually_deleted(make_store):
    """The return value feeds a log line; a count that drifts from reality
    makes the sweep unauditable."""
    store = make_store(max_per_conversation=2)
    for i in range(7):
        await store.append(message=_msg(body=f"m{i}", ts=i))
    before = len(await store.recent(conversation_id="g1", limit=1000))
    deleted = await store.prune_all()
    after = len(await store.recent(conversation_id="g1", limit=1000))
    assert deleted == before - after == 5


async def test_stats_nora_messages_is_zero_on_an_empty_store(store):
    """Pins the COALESCE(SUM(is_nora), 0) path — SUM over no rows is NULL, and
    a dropped coalesce would surface as None rather than 0."""
    s = await store.stats()
    assert s["nora_messages"] == 0
    assert s["conversations"] == 0


async def test_set_delivery_outcome_on_an_unknown_row_is_a_silent_no_op(store):
    """Decided behaviour, not an accident: it matches the legacy
    `nora/store/db.py::set_delivery_status`, and `Deliverer` calls this after a
    send it cannot un-send — raising there would convert a harmless race into a
    silent failure under R-5."""
    await store.append(message=_msg())
    await store.set_delivery_outcome(row_id=999_999, status=DeliveryStatus.SENT, ts=1)
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.delivery_status is DeliveryStatus.PENDING


# ---------------------------------------------------------------------------
# the window bound says when it bit (`nora.domain.bounds`)
# ---------------------------------------------------------------------------


class _Metrics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[tuple[str, object], ...]]] = []

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        self.calls.append((name, tuple(sorted(labels.items()))))


def _truncations(metrics: _Metrics) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
    """The bound counter's calls only: every `append` also counts its memory
    index projection (`nora_memory_projection_total`), which is not what these
    tests are about."""
    return [call for call in metrics.calls if call[0] == "nora_bound_truncated_total"]


async def test_a_clipped_transcript_window_is_counted(make_store):
    """`recent` is the read `_subjects_in_play` builds the room's subject set
    from, so a silent cut here makes an empty facts block ambiguous three ways
    — no such fact, its subject fell outside the window, or the window itself
    was clipped. This is what makes the third one visible."""
    metrics = _Metrics()
    store = make_store(metrics=metrics)
    for i in range(5):
        await store.append(message=_msg(ts=i + 1, body=f"m{i}"))

    rows = await store.recent(conversation_id="g1", limit=3)

    assert [r.body for r in rows] == ["m2", "m3", "m4"], (
        "the NEWEST three, oldest-first — the over-fetched row must be cut "
        "from the OLD end, before the reverse, not after it"
    )
    assert _truncations(metrics) == [("nora_bound_truncated_total", (("site", "audit.recent"),))]


async def test_a_window_that_exactly_fits_is_not_counted(make_store):
    """THE CASE THE COUNTER EXISTS TO DISTINGUISH. Three rows under a limit of
    three is a full page with nothing behind it — the bound did not bite, and a
    counter that fired here would be measuring «the page was full», which is
    true of almost every healthy read."""
    metrics = _Metrics()
    store = make_store(metrics=metrics)
    for i in range(3):
        await store.append(message=_msg(ts=i + 1, body=f"m{i}"))

    rows = await store.recent(conversation_id="g1", limit=3)

    assert [r.body for r in rows] == ["m0", "m1", "m2"]
    assert _truncations(metrics) == [], "a full page is not a truncation"


async def test_the_over_fetched_row_never_reaches_the_caller(make_store):
    """The bug this shape invites: fetch limit+1 and forget to cut, so every
    bounded read quietly returns one row more than it promised."""
    store = make_store()
    for i in range(20):
        await store.append(message=_msg(ts=i + 1, body=f"m{i}"))
    for limit in (1, 2, 7, 19):
        rows = await store.recent(conversation_id="g1", limit=limit)
        assert len(rows) == limit, f"limit={limit} returned {len(rows)}"
