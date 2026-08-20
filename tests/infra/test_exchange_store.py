"""`SqlExchangeStore` — Dreamer Plan 2's durability layer. Mirrors
`tests/infra/test_language_store.py`'s structure, minus the watermark
section (Plan 2 has none of its own — see `nora.infra.sql.exchange`'s module
docstring) and minus `lag`/`seconds_since_last_message`/
`watermark_is_behind_the_record` (those stay on `SqlLanguageStore`, shared by
`_dream_once` across every consolidator).

`append_observations` reads `conversation_id`/`observed_at` off the `batch`
argument directly (not a `messages` re-query) — see
`nora.infra.sql.exchange`'s module docstring for why that is safe here — so
most `append_observations` tests build a `batch` in-process; `room_exchange`'s
read-time quote resolution DOES need real `messages` rows, so those use the
`record` fixture, same as the language store's own read tests.
"""

from __future__ import annotations

import time

import pytest
import sqlalchemy as sa

from nora.domain.models import DeliveryStatus, ExchangeCandidate, NormalizedMessage, TranscriptRow
from nora.domain.ports import RoomExchangeReader
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.exchange import SqlExchangeStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import EXCHANGE_OBSERVATION

DAY_MS = 86_400_000
NOW = int(time.time() * 1000)


def candidate(
    *, message_ids: tuple[int, ...], participants: tuple[str, ...],
    act: str = "insult_trade", confidence: float = 0.9,
) -> ExchangeCandidate:
    return ExchangeCandidate(
        act=act, message_ids=message_ids, participants=participants, confidence=confidence,
    )


def _row(mid: int, person_id: str, ts: int = NOW) -> TranscriptRow:
    return TranscriptRow(
        id=mid, sender_id=person_id, sender_name=person_id, ts=ts, body=f"body{mid}",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id=person_id,
    )


def _msg(conv: str = "g1", body: str = "hi", ts: int = NOW,
         sender: str = "+852111") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body, raw={},
    )


@pytest.fixture
async def store(pg_url) -> SqlExchangeStore:
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    s = SqlExchangeStore(engine)
    yield s
    await engine.dispose()


@pytest.fixture
async def record(pg_url) -> SqlRecordStore:
    run_migrations(pg_url)
    s = SqlRecordStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


def test_the_store_satisfies_the_reader_port():
    assert isinstance(SqlExchangeStore(create_sql_engine("postgresql+asyncpg://x/x")),
                      RoomExchangeReader)


# ---------------------------------------------------------------------------
# append_observations
# ---------------------------------------------------------------------------


async def test_appending_the_same_pattern_twice_stores_one(store):
    batch = [_row(1, "miles"), _row(2, "tyler")]
    obs = [candidate(message_ids=(1, 2), participants=("miles", "tyler"))]
    assert await store.append_observations(scope_id="g1", observations=obs, batch=batch) == 1
    assert await store.append_observations(scope_id="g1", observations=obs, batch=batch) == 0


async def test_appending_an_empty_sequence_stores_nothing(store):
    assert await store.append_observations(scope_id="g1", observations=[], batch=[]) == 0


async def test_a_candidate_whose_evidence_is_not_in_the_batch_is_dropped(store):
    """Should be unreachable in production (`ExchangePatternObserver._extract`
    already guarantees every candidate's `message_ids` are all in the SAME
    batch it was built from) — defence in depth, mirroring
    `test_a_candidate_naming_a_nonexistent_message_id_is_dropped`."""
    batch = [_row(1, "miles")]
    obs = [candidate(message_ids=(1, 2), participants=("miles", "tyler"))]
    assert await store.append_observations(scope_id="g1", observations=obs, batch=batch) == 0


async def test_a_candidate_dropped_for_missing_evidence_is_counted(store):
    from nora.obs.metrics import Metrics

    metrics = Metrics()
    metered = SqlExchangeStore(store._engine, metrics=metrics)
    batch = [_row(1, "miles")]
    obs = [candidate(message_ids=(1, 2), participants=("miles", "tyler"))]
    await metered.append_observations(scope_id="g1", observations=obs, batch=batch)
    assert metrics.get("nora_exchange_candidates_dropped_total", reason="not_found") == 1


async def test_observed_at_is_the_max_ts_among_the_evidence_rows(store):
    """`observed_at` is when the exchange CONCLUDED — the last message's own
    `ts`, never this process's own clock."""
    batch = [_row(1, "miles", ts=NOW - 5000), _row(2, "tyler", ts=NOW)]
    obs = [candidate(message_ids=(1, 2), participants=("miles", "tyler"))]
    await store.append_observations(scope_id="g1", observations=obs, batch=batch)
    async with store._engine.connect() as conn:
        row = (await conn.execute(
            sa.select(EXCHANGE_OBSERVATION.c.observed_at, EXCHANGE_OBSERVATION.c.conversation_id)
        )).one()
    assert row.observed_at == NOW
    assert row.conversation_id == "g1"


async def test_scopes_are_independent(store):
    batch1 = [_row(1, "miles"), _row(2, "tyler")]
    batch2 = [_row(3, "miles"), _row(4, "tyler")]
    await store.append_observations(
        scope_id="g1", observations=[
            candidate(message_ids=(1, 2), participants=("miles", "tyler"))], batch=batch1)
    assert await store.append_observations(
        scope_id="g2", observations=[
            candidate(message_ids=(3, 4), participants=("miles", "tyler"))], batch=batch2) == 1


# ---------------------------------------------------------------------------
# rescore / room_exchange
# ---------------------------------------------------------------------------


async def test_rescore_counts_occurrences_across_replays_of_a_pattern(store):
    b1 = [_row(1, "miles"), _row(2, "tyler")]
    b2 = [_row(3, "miles"), _row(4, "tyler")]
    await store.append_observations(scope_id="g1", batch=b1, observations=[
        candidate(message_ids=(1, 2), participants=("miles", "tyler")),
    ])
    await store.append_observations(scope_id="g1", batch=b2, observations=[
        candidate(message_ids=(3, 4), participants=("miles", "tyler")),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [pat] = (await store.room_exchange(scope_id="g1", limit=10)).patterns
    assert pat.occurrence_count == 2


async def test_two_acts_between_the_same_pair_are_two_rows(store):
    batch = [_row(1, "miles"), _row(2, "tyler")]
    await store.append_observations(scope_id="g1", batch=batch, observations=[
        candidate(message_ids=(1, 2), participants=("miles", "tyler"), act="insult_trade"),
        candidate(message_ids=(1, 2), participants=("miles", "tyler"), act="banter"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    patterns = (await store.room_exchange(scope_id="g1", limit=10)).patterns
    assert {p.act for p in patterns} == {"insult_trade", "banter"}


async def test_stale_entries_do_not_render(store):
    batch = [_row(1, "miles"), _row(2, "tyler")]
    await store.append_observations(scope_id="g1", batch=batch, observations=[
        candidate(message_ids=(1, 2), participants=("miles", "tyler")),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW + 200 * DAY_MS)
    assert not (await store.room_exchange(scope_id="g1", limit=10)).patterns


async def test_room_exchange_is_empty_for_an_unknown_scope(store):
    assert not await store.room_exchange(scope_id="nobody", limit=10)


async def test_room_exchange_orders_by_recognition_score_descending(store):
    frequent = [_row(i, "miles" if i % 2 else "tyler") for i in range(1, 21)]
    rare = [_row(101, "alice"), _row(102, "bob")]
    await store.append_observations(scope_id="g1", batch=frequent, observations=[
        candidate(message_ids=(i, i + 1), participants=("miles", "tyler"), act="banter")
        for i in range(1, 20, 2)
    ])
    await store.append_observations(scope_id="g1", batch=rare, observations=[
        candidate(message_ids=(101, 102), participants=("alice", "bob"), act="tease"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    patterns = (await store.room_exchange(scope_id="g1", limit=10)).patterns
    assert [p.act for p in patterns] == ["banter", "tease"]


async def test_room_exchange_honours_its_limit(store):
    batch = [_row(i, "miles" if i % 2 else "tyler") for i in range(1, 11)]
    await store.append_observations(scope_id="g1", batch=batch, observations=[
        candidate(message_ids=(i, i + 1), participants=("miles", "tyler"),
                   act=("banter" if i % 4 else "tease"))
        for i in range(1, 10, 2)
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    patterns = (await store.room_exchange(scope_id="g1", limit=1)).patterns
    assert len(patterns) == 1


async def test_room_exchange_resolves_quotes_from_real_messages(store, record):
    """The quote is resolved at READ time from `messages`, never stored on
    `exchange_observation` (see `rescore`'s own docstring) — this is the
    only test that needs a real `messages` row to prove that resolution
    happens at all."""
    mid1 = await record.append(message=_msg(body="you absolute idiot", sender="miles"))
    mid2 = await record.append(message=_msg(body="takes one to know one", sender="tyler"))
    batch = [_row(mid1, "miles"), _row(mid2, "tyler")]
    await store.append_observations(scope_id="g1", batch=batch, observations=[
        candidate(message_ids=(mid1, mid2), participants=("miles", "tyler")),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [pat] = (await store.room_exchange(scope_id="g1", limit=10)).patterns
    texts = {e.text for e in pat.examples}
    assert "you absolute idiot" in texts
    assert "takes one to know one" in texts


async def test_room_exchange_never_raises_against_an_unreachable_engine():
    unreachable = create_sql_engine("postgresql+asyncpg://nobody:nobody@localhost:1/nobody")
    store = SqlExchangeStore(unreachable)
    result = await store.room_exchange(scope_id="g1", limit=10)
    assert result.patterns == ()
    await unreachable.dispose()
