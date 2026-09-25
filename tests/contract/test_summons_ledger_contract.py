"""`SummonsLedger` has two implementations, so it gets one suite both pass.

The property the table exists for is that a public summons is answered at
most once (design/instagram-adoption/19): claimed before the model runs, and
of two concurrent claims on one comment exactly one wins. The rest is what the
poller reads back: `seen` (which mentions are already handled), `awaiting_flush`
(a LIVE `staged` row, never a dry-run one), and the boot sweep, which turns a
row a dead process left `claimed` into `failed` and never into a retry.

Database scoped to the MODULE and truncated between tests (AGENTS.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.summons import SummonsLedger
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.summons import SqlSummonsLedger
from nora.testing import InMemorySummonsLedger

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def _summons_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_summons_" + uuid4().hex
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
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def ledger(request: Any) -> AsyncIterator[SummonsLedger]:
    if request.param == "memory":
        yield InMemorySummonsLedger()
        return
    engine = create_sql_engine(request.getfixturevalue("_summons_db"))
    try:
        yield SqlSummonsLedger(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("truncate instagram_summons"))
        await engine.dispose()


async def _claim(ledger: SummonsLedger, comment_id: str = "1001", *, mode: str = "dry_run",
                 now: datetime = NOW):
    return await ledger.claim(
        comment_id=comment_id, media_id="2002", shortcode="CmiJBr2LAAB",
        author_igid="3003", mention_at=now - timedelta(minutes=5), mode=mode, now=now,
    )


async def test_a_comment_is_claimed_once_and_a_second_claim_gets_nothing(ledger):
    first = await _claim(ledger)
    assert first is not None
    assert await _claim(ledger) is None
    row = await ledger.get(first)
    assert row is not None and row.state == "claimed" and row.comment_id == "1001"


async def test_of_concurrent_claims_on_one_comment_exactly_one_wins(ledger):
    """The idempotency line under concurrency: ten claims at once, one id.
    Falsified by removing the ON CONFLICT (the SQL store raises
    IntegrityError on nine) or the fake's membership check (ten ids)."""
    got = await asyncio.gather(*(_claim(ledger) for _ in range(10)))
    assert sum(1 for g in got if g is not None) == 1


async def test_finish_records_the_state_and_only_the_fields_it_was_given(ledger):
    summons_id = await _claim(ledger)
    assert summons_id is not None
    await ledger.finish(summons_id, "staged", now=NOW, person_id="p_miles",
                        reply_text="it's a heron", trace_id="abc123")
    await ledger.finish(summons_id, "published", now=NOW, reply_comment_id="9009")
    row = await ledger.get(summons_id)
    assert row is not None
    assert (row.state, row.person_id, row.reply_text, row.reply_comment_id, row.trace_id) == (
        "published", "p_miles", "it's a heron", "9009", "abc123",
    )


async def test_an_unknown_state_is_refused(ledger):
    summons_id = await _claim(ledger)
    assert summons_id is not None
    with pytest.raises(ValueError, match="state"):
        await ledger.finish(summons_id, "posted", now=NOW)


async def test_seen_names_exactly_the_comments_already_in_the_ledger(ledger):
    await _claim(ledger, "1001")
    await _claim(ledger, "1003")
    assert await ledger.seen(("1001", "1002", "1003")) == frozenset({"1001", "1003"})
    assert await ledger.seen(()) == frozenset()


async def test_only_a_live_staged_row_awaits_a_flush(ledger):
    """A dry-run `staged` row is final: it must never be read as a live reply
    waiting out `too_soon`, or switching the mode to live would post a
    backlog of dry-run text."""
    dry = await _claim(ledger, "1001", mode="dry_run")
    assert dry is not None
    await ledger.finish(dry, "staged", now=NOW, reply_text="dry")
    assert await ledger.awaiting_flush() is None
    live = await _claim(ledger, "1002", mode="live", now=NOW + timedelta(seconds=1))
    assert live is not None
    await ledger.finish(live, "staged", now=NOW, reply_text="live")
    waiting = await ledger.awaiting_flush()
    assert waiting is not None and waiting.id == live and waiting.reply_text == "live"


async def test_the_boot_sweep_fails_every_claimed_row_and_touches_nothing_else(ledger):
    left = await _claim(ledger, "1001")
    done = await _claim(ledger, "1002")
    assert left is not None and done is not None
    await ledger.finish(done, "silent", now=NOW)
    assert await ledger.sweep_stale_claims(now=NOW) == 1
    swept = await ledger.get(left)
    kept = await ledger.get(done)
    assert swept is not None and swept.state == "failed"
    assert swept.bridge_code == "swept_at_boot"
    assert kept is not None and kept.state == "silent"
    # And it is a claim that stays claimed: the comment is still `seen`.
    assert await _claim(ledger, "1001") is None
