"""Contract proof for `ExchangeConsolidator`/`SqlExchangeStore` against the
`RoomConsolidator`/`RoomExchangeReader` ports — the real implementation only
(unlike `test_room_consolidator_contract.py`'s fake+sql matrix; a faithful
in-memory double for participant/act extraction would duplicate most of that
file's own machinery for one extra data point, and the property that matters
most here — idempotency and scope isolation against REAL Postgres — is
already proven the way it counts). `RoomExchangeReader.room_exchange` never
raising is proven against a real unreachable engine, mirroring
`test_an_unavailable_backend_degrades_to_an_empty_room_language`'s own
argument for why that must be checked against the real implementation, not
only a fake that raises by construction.
"""

from __future__ import annotations

import json
import time

import pytest

from nora.domain.models import DeliveryStatus, NormalizedMessage, RoomExchange, TranscriptRow
from nora.domain.ports import RoomConsolidator, RoomExchangeReader
from nora.domain.provenance import instruction_sha256
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.exchange import SqlExchangeStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.language.exchange_consolidator import ExchangeConsolidator
from nora.language.exchange_observer import ExchangePatternObserver
from tests.objectives import append_to_objective, editable_objectives, shipped_objectives

# The `objective` collaborator, wired the way production wires it: the REAL
# `DreamerInstructions` over the REAL `nora/dreamer/` tree, not a double. The
# bodies it serves are byte-identical to the constants these tests were written
# against (`tests/unit/test_dreamer_instructions.py` proves it), so nothing
# below changed meaning — and a broken objective file now fails here rather
# than on the next Dreamer tick.
OBJECTIVES = shipped_objectives()


def _now_ms() -> int:
    return int(time.time() * 1000)


class _FakeModelClient:
    """Mirrors `test_room_consolidator_contract.py::_FakeModelClient` — reads
    the rendered prompt's own message lines and derives a fixed pattern
    whenever at least two distinct speakers appear in the batch, rather than
    re-deriving `nora.language.exchange_observer._build_prompt`'s literal
    format for a real act-classification rule (there is no cheap textual
    marker for "these two are in an exchange" the way `"<form>:<sense>"`
    stands in for a lexeme)."""

    model = "fake-contract-exchange-model"

    async def complete(self, *, prompt: str) -> str:
        ids: list[int] = []
        for line in prompt.splitlines():
            if not line.startswith("["):
                continue
            ids.append(int(line[1:line.index("]")]))
        if len(ids) < 2:
            return '{"patterns": []}'
        return json.dumps({"patterns": [
            {"act": "banter", "message_ids": ids[:2], "confidence": 0.9},
        ]})


@pytest.fixture
async def backing(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlExchangeStore(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def consolidator(backing: SqlExchangeStore) -> ExchangeConsolidator:
    return ExchangeConsolidator(observer=ExchangePatternObserver(
        _FakeModelClient(),
        objective=OBJECTIVES,
    ), store=backing)


@pytest.fixture
async def batch_with_one_pattern(pg_url) -> list[TranscriptRow]:
    """Two real `messages` rows from two different senders — real ids,
    matching `SqlExchangeStore.append_observations`'s own need for `batch`
    rows to resolve (see its module docstring)."""
    record = SqlRecordStore(create_sql_engine(pg_url))
    try:
        mid1 = await record.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=_now_ms(),
            body="you absolute idiot", raw={},
        ), person_id="miles")
        mid2 = await record.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852222", sender_name="Tyler", timestamp=_now_ms(),
            body="takes one to know one", raw={},
        ), person_id="tyler")
    finally:
        await record.aclose()
    return [
        TranscriptRow(id=mid1, sender_id="+852111", sender_name="Miles", ts=_now_ms(),
                      body="you absolute idiot", is_nora=False,
                      delivery_status=DeliveryStatus.SENT, raw_json=None, person_id="miles"),
        TranscriptRow(id=mid2, sender_id="+852222", sender_name="Tyler", ts=_now_ms(),
                      body="takes one to know one", is_nora=False,
                      delivery_status=DeliveryStatus.SENT, raw_json=None, person_id="tyler"),
    ]


def test_both_pieces_satisfy_their_ports():
    store = SqlExchangeStore(create_sql_engine("postgresql+asyncpg://x/x"))
    assert isinstance(store, RoomExchangeReader)
    assert isinstance(
        ExchangeConsolidator(observer=ExchangePatternObserver(
            _FakeModelClient(),
            objective=OBJECTIVES,
        ), store=store),
        RoomConsolidator,
    )


async def test_an_empty_batch_appends_nothing(consolidator: ExchangeConsolidator):
    assert await consolidator.consolidate(scope_id="g1", batch=[]) == 0


async def test_the_same_batch_twice_appends_once(
    consolidator: ExchangeConsolidator, batch_with_one_pattern: list[TranscriptRow],
):
    first = await consolidator.consolidate(scope_id="g1", batch=batch_with_one_pattern)
    assert first > 0
    second = await consolidator.consolidate(scope_id="g1", batch=batch_with_one_pattern)
    assert second == 0, "the natural key must make a re-run a no-op"


async def test_scope_is_honoured(
    consolidator: ExchangeConsolidator, backing: SqlExchangeStore,
    batch_with_one_pattern: list[TranscriptRow],
):
    await consolidator.consolidate(scope_id="g1", batch=batch_with_one_pattern)
    assert not await backing.room_exchange(scope_id="g2", limit=10)
    g1 = await backing.room_exchange(scope_id="g1", limit=10)
    assert g1, "the batch DID contain a pattern — g1 must not be empty too"
    assert g1.patterns[0].act == "banter"


async def test_an_unavailable_backend_degrades_to_an_empty_room_exchange():
    engine = create_sql_engine(
        "postgresql+asyncpg://nobody:nobody@dreamer-exchange-unreachable.invalid:5432/nope"
    )
    try:
        store = SqlExchangeStore(engine)
        result = await store.room_exchange(scope_id="g1", limit=10)
        assert result == RoomExchange(scope_id="g1")
        assert not result
    finally:
        await engine.dispose()


# --- instruction provenance, end to end (substrate spec §7.2) --------------
#
# The exchange sibling of `test_room_consolidator_contract.py`'s own block, and
# it exists for the same reason: the observer's suite proves the digest is
# right and the store's suite proves the column round-trips, and nothing joined
# them, so a consolidator dropping the field on the way through would pass
# both.


async def _stamps(engine) -> list[str]:
    import sqlalchemy as sa

    from nora.infra.sql.schema import EXCHANGE_OBSERVATION

    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(EXCHANGE_OBSERVATION.c.instruction_sha256)
            .order_by(EXCHANGE_OBSERVATION.c.id)
        )).all()
    return [r.instruction_sha256 for r in rows]


async def test_every_row_carries_the_stamp_the_instruction_store_reports(
    pg_url, backing, batch_with_one_pattern, tmp_path,
):
    objectives, _ = editable_objectives(tmp_path)
    consolidator = ExchangeConsolidator(
        observer=ExchangePatternObserver(_FakeModelClient(), objective=objectives),
        store=backing,
    )
    assert await consolidator.consolidate(scope_id="g1", batch=batch_with_one_pattern) > 0
    engine = create_sql_engine(pg_url)
    try:
        expected = instruction_sha256(objectives.objective_body("room-exchange"))
        stamps = await _stamps(engine)
        assert stamps and all(s == expected for s in stamps)
    finally:
        await engine.dispose()


async def test_editing_the_objective_makes_later_rows_distinguishable(
    pg_url, backing, batch_with_one_pattern, tmp_path,
):
    objectives, root = editable_objectives(tmp_path)
    consolidator = ExchangeConsolidator(
        observer=ExchangePatternObserver(_FakeModelClient(), objective=objectives),
        store=backing,
    )
    await consolidator.consolidate(scope_id="g1", batch=batch_with_one_pattern)
    engine = create_sql_engine(pg_url)
    try:
        before = await _stamps(engine)
        append_to_objective(root, "room-exchange", "One exchange per pair, at most.\n")
        await consolidator.consolidate(scope_id="g2", batch=batch_with_one_pattern)
        after = [s for s in await _stamps(engine) if s not in before]
        assert before and after
        assert set(before).isdisjoint(after)
    finally:
        await engine.dispose()
