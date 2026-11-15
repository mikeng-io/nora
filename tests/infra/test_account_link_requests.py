"""Real PostgreSQL serializes private decisions and preserves their evidence.

One migrated database per module, truncated between tests. These tests prove
durable consent and concurrency, not ownership publication or activation.
"""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from nora.domain.account_linking import LinkState, LinkStateError
from nora.infra.sql.account_links import SqlLinkRequests
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import MANAGED_LINK_DECISION, MANAGED_LINK_REQUEST
from tests.unit.test_account_linking_state import (
    LEFT,
    NONCES,
    NOW,
    RIGHT,
    decision,
    pending,
    proof,
)


@pytest.fixture(scope="module")
def database(pg_module_url):
    return pg_module_url


@pytest.fixture
async def store(database):
    engine = create_sql_engine(database)
    async with engine.begin() as connection:
        await connection.execute(sa.text("TRUNCATE managed_link_decision, managed_link_request"))
    try:
        yield SqlLinkRequests(engine)
    finally:
        await engine.dispose()


async def accept(store, link, side):
    return await store.decide(decision(link, side), proof(side), now=NOW + timedelta(seconds=2))


async def test_request_and_approval_evidence_survive_a_new_store_instance(store, database):
    link = pending()
    await store.create(link)
    await accept(store, link, LEFT)
    await accept(store, link, RIGHT)
    engine = create_sql_engine(database)
    try:
        recovered = await SqlLinkRequests(engine).get(link.request_id)
        assert recovered is not None and recovered.ready
        assert recovered.effect_id == link.effect_id
        assert recovered.approvals[RIGHT].issuer == "synthetic-signal-service"
        async with engine.connect() as connection:
            evidence = (await connection.execute(sa.select(MANAGED_LINK_DECISION))).all()
            snapshot = await connection.scalar(sa.select(MANAGED_LINK_REQUEST.c.snapshot))
        assert len(evidence) == 2
        assert all(NONCES[side] not in json.dumps(snapshot) for side in (LEFT, RIGHT))
        assert all(NONCES[side] not in json.dumps(row.evidence)
                   for row in evidence for side in (LEFT, RIGHT))
    finally:
        await engine.dispose()


async def test_concurrent_holders_do_not_overwrite_each_others_approval(store, database):
    link = await store.create(pending())
    other_engine = create_sql_engine(database)
    try:
        other = SqlLinkRequests(other_engine)
        results = await asyncio.gather(accept(store, link, LEFT), accept(other, link, RIGHT))
        assert any(result.ready for result in results)
        recovered = await store.get(link.request_id)
        assert recovered is not None and recovered.approved == frozenset({LEFT, RIGHT})
    finally:
        await other_engine.dispose()


async def test_concurrent_retries_consume_one_approval_once(store, database):
    link = await store.create(pending())
    results = await asyncio.gather(*(accept(store, link, LEFT) for _ in range(5)))
    assert all(result.approved == frozenset({LEFT}) for result in results)
    engine = create_sql_engine(database)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(sa.select(sa.func.count()).select_from(
                MANAGED_LINK_DECISION,
            ))
        assert count == 1
    finally:
        await engine.dispose()


async def test_retrying_request_creation_preserves_existing_approval(store):
    link = await store.create(pending())
    await accept(store, link, LEFT)
    retry = await store.create(link)
    assert retry.approved == frozenset({LEFT})
    with pytest.raises(LinkStateError, match="different effect"):
        await store.create(replace(link, surviving_person="synthetic-b"))


async def test_decision_id_cannot_be_reused_for_a_different_effect_or_request(store):
    link = await store.create(pending())
    await accept(store, link, LEFT)
    changed = replace(decision(link, LEFT), observed_revisions={
        **link.revisions, "authority": "changed",
    })
    with pytest.raises(LinkStateError, match="different effect"):
        await store.decide(changed, proof(LEFT), now=NOW + timedelta(seconds=2))
    second = await store.create(replace(link, request_id="another-request"))
    changed = replace(decision(second, LEFT), operation_id=decision(link, LEFT).operation_id)
    with pytest.raises(LinkStateError, match="different effect"):
        await store.decide(changed, proof(LEFT, request_id=second.request_id),
                           now=NOW + timedelta(seconds=2))
    assert (await store.get(second.request_id)).approved == frozenset()


async def test_request_and_decision_identifiers_share_a_namespace(store):
    link = await store.create(pending())
    op = replace(decision(link, LEFT), operation_id=link.request_id)
    with pytest.raises(LinkStateError, match="already names a request"):
        await store.decide(op, proof(LEFT), now=NOW + timedelta(seconds=2))
    await accept(store, link, LEFT)
    with pytest.raises(LinkStateError, match="already names a decision"):
        await store.create(replace(link, request_id=decision(link, LEFT).operation_id))


async def test_invalid_proof_rolls_back_and_does_not_poison_a_valid_retry(store):
    link = await store.create(pending())
    with pytest.raises(LinkStateError):
        await store.decide(decision(link, RIGHT), proof(RIGHT, nonce="wrong-proof"),
                           now=NOW + timedelta(seconds=2))
    unchanged = await store.get(link.request_id)
    assert unchanged is not None and not unchanged.approved
    accepted = await accept(store, link, RIGHT)
    assert accepted.approved == frozenset({RIGHT})


@pytest.mark.parametrize("kind,side,state", [
    ("refuse_link", RIGHT, LinkState.REFUSED), ("cancel_link", LEFT, LinkState.CANCELLED),
])
async def test_terminal_decision_and_its_retry_do_not_reopen_request(store, kind, side, state):
    link = await store.create(pending())
    op = decision(link, side, kind)
    first = await store.decide(op, proof(side), now=NOW + timedelta(seconds=2))
    repeated = await store.decide(op, proof(side), now=NOW + timedelta(seconds=3))
    assert first.state is repeated.state is state
    with pytest.raises(LinkStateError):
        await accept(store, link, RIGHT)


async def test_unknown_request_is_distinct_from_an_unreachable_database(store):
    assert await store.get("absent-generated-request") is None
    engine = create_sql_engine("postgresql+asyncpg://generated:generated@127.0.0.1:1/generated")
    try:
        with pytest.raises((OSError, sa.exc.SQLAlchemyError)):
            await SqlLinkRequests(engine).get("absent-generated-request")
    finally:
        await engine.dispose()
