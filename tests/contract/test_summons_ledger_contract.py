"""`SummonsLedger` has two implementations, so it gets one suite both pass.

The property the table exists for is that a public summons is answered at
most once: claimed on the summons CARD's message id before anything is read,
and of two concurrent claims on one card exactly one wins; the comment the card
resolves to is bound once and stays unique across rows, so two cards for one
comment get one answer. The rest is what the consumer reads back:
`awaiting_flush` (a LIVE `staged` row, never a dry-run one), and the boot
sweep, which turns a row a dead process left `claimed` into `failed` and never
into a retry.

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

from nora.domain.summons import OPEN_STATES, SUMMONS_STATES, SummonsLedger
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
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate instagram_summons"))
        await engine.dispose()


async def _claim(ledger: SummonsLedger, message_id: str = "mid.$1001", *,
                 mode: str = "dry_run", now: datetime = NOW):
    return await ledger.claim(
        message_id=message_id, media_id="2002", shortcode="CmiJBr2LAAB",
        author_igid="3003", mention_at=now - timedelta(minutes=5), mode=mode, now=now,
    )


async def test_a_card_is_claimed_once_and_a_second_claim_gets_nothing(ledger):
    first = await _claim(ledger)
    assert first is not None
    assert await _claim(ledger) is None
    row = await ledger.get(first)
    assert row is not None and row.state == "claimed"
    assert (row.message_id, row.comment_id) == ("mid.$1001", None)


async def test_of_concurrent_claims_on_one_card_exactly_one_wins(ledger):
    """The idempotency line under concurrency: ten claims of one message id at
    once, one id. Falsified by keying the claim on anything but the message
    id (the SQL store's ON CONFLICT target, or the fake's membership check):
    ten ids, or IntegrityError on nine."""
    got = await asyncio.gather(*(_claim(ledger) for _ in range(10)))
    assert sum(1 for g in got if g is not None) == 1


async def test_two_cards_are_two_claims_even_on_one_post(ledger):
    a = await _claim(ledger, "mid.$a")
    b = await _claim(ledger, "mid.$b")
    assert a is not None and b is not None and a != b


async def test_a_comment_is_bound_once_and_never_to_two_rows(ledger):
    """Two cards that resolve to ONE comment: the first bind wins, the second
    is refused (comment_id stays UNIQUE), and a bound row is never re-bound."""
    a = await _claim(ledger, "mid.$a")
    b = await _claim(ledger, "mid.$b")
    assert a is not None and b is not None
    assert await ledger.bind_comment(a, "9001", now=NOW) is True
    assert await ledger.bind_comment(b, "9001", now=NOW) is False
    assert await ledger.bind_comment(a, "9002", now=NOW) is False
    assert (await ledger.get(a)).comment_id == "9001"
    assert (await ledger.get(b)).comment_id is None


async def test_of_concurrent_binds_of_one_comment_exactly_one_wins(ledger):
    ids = [await _claim(ledger, f"mid.${i}") for i in range(10)]
    got = await asyncio.gather(*(
        ledger.bind_comment(i, "9001", now=NOW) for i in ids if i is not None
    ))
    assert got.count(True) == 1


async def test_only_a_claimed_row_can_be_bound(ledger):
    summons_id = await _claim(ledger)
    assert summons_id is not None
    await ledger.finish(summons_id, "unresolved", now=NOW)
    assert await ledger.bind_comment(summons_id, "9001", now=NOW) is False


async def test_every_state_in_the_vocabulary_can_be_written(ledger):
    """The SQL CHECK and `SUMMONS_STATES` agree: each final state is
    writable (`unresolved` among them)."""
    for state in SUMMONS_STATES:
        if state in OPEN_STATES:
            continue
        summons_id = await _claim(ledger, f"mid.${state}")
        assert summons_id is not None
        assert await ledger.finish(summons_id, state, now=NOW) is True
        assert (await ledger.get(summons_id)).state == state


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


async def test_clear_reply_nulls_the_text_and_an_omitted_one_keeps_it(ledger):
    """`reply_text=None` means "leave the column", so a refusal that must not
    keep its text says so with `clear_reply`: the stored text becomes NULL.
    A finish that does not pass it keeps the text. Falsified by dropping the
    clear: the refused row keeps the body it was staged with."""
    cleared = await _claim(ledger, "mid.$clear")
    kept = await _claim(ledger, "mid.$keep")
    assert cleared is not None and kept is not None
    await ledger.finish(cleared, "staged", now=NOW, reply_text="sensitive body")
    await ledger.finish(kept, "staged", now=NOW, reply_text="fine body")
    assert await ledger.finish(cleared, "refused", now=NOW, bridge_code="canary",
                               clear_reply=True) is True
    assert await ledger.finish(kept, "published", now=NOW, reply_comment_id="9009") is True
    cleared_row = await ledger.get(cleared)
    kept_row = await ledger.get(kept)
    assert (cleared_row.state, cleared_row.reply_text) == ("refused", None)
    assert (kept_row.state, kept_row.reply_text) == ("published", "fine body")


async def test_an_unknown_state_is_refused(ledger):
    summons_id = await _claim(ledger)
    assert summons_id is not None
    with pytest.raises(ValueError, match="state"):
        await ledger.finish(summons_id, "posted", now=NOW)


async def test_only_a_live_staged_row_awaits_a_flush(ledger):
    """A dry-run `staged` row is final: it must never be read as a live reply
    waiting out `too_soon`, or switching the mode to live would post a
    backlog of dry-run text."""
    dry = await _claim(ledger, "mid.$1001", mode="dry_run")
    assert dry is not None
    await ledger.finish(dry, "staged", now=NOW, reply_text="dry")
    assert await ledger.awaiting_flush() is None
    live = await _claim(ledger, "mid.$1002", mode="live", now=NOW + timedelta(seconds=1))
    assert live is not None
    await ledger.finish(live, "staged", now=NOW, reply_text="live")
    waiting = await ledger.awaiting_flush()
    assert waiting is not None and waiting.id == live and waiting.reply_text == "live"


TIMEOUT = timedelta(minutes=15)


async def test_a_final_row_is_final(ledger):
    """`finish` moves a row only out of claimed / staged / flushing. A second
    writer (an old container overlapping a new one) cannot overwrite what the
    first recorded. Falsified by dropping the state condition from `finish`'s
    WHERE (or the fake's OPEN_STATES check): the silent row becomes published."""
    summons_id = await _claim(ledger)
    assert summons_id is not None
    assert await ledger.finish(summons_id, "silent", now=NOW) is True
    assert await ledger.finish(summons_id, "published", now=NOW, reply_comment_id="9") is False
    row = await ledger.get(summons_id)
    assert row is not None and row.state == "silent" and row.reply_comment_id is None


async def test_a_staged_row_is_taken_for_flush_exactly_once(ledger):
    summons_id = await _claim(ledger, mode="live")
    assert summons_id is not None
    await ledger.finish(summons_id, "staged", now=NOW, reply_text="x")
    got = await asyncio.gather(*(ledger.take_for_flush(summons_id, now=NOW) for _ in range(10)))
    assert got.count(True) == 1
    row = await ledger.get(summons_id)
    assert row is not None and row.state == "flushing"
    # A flushing row is not waiting for a flush, and can still be finished.
    assert await ledger.awaiting_flush() is None
    assert await ledger.finish(summons_id, "published", now=NOW) is True


async def test_only_a_staged_row_can_be_taken(ledger):
    summons_id = await _claim(ledger, mode="live")
    assert summons_id is not None
    assert await ledger.take_for_flush(summons_id, now=NOW) is False  # still claimed
    await ledger.finish(summons_id, "silent", now=NOW)
    assert await ledger.take_for_flush(summons_id, now=NOW) is False  # final


async def test_the_sweep_closes_old_open_rows_and_leaves_young_ones_alone(ledger):
    """A `claimed` row older than the timeout is `failed`; a `flushing` one is
    `ambiguous` (its POST may have gone out) and never re-posted; a row
    younger than the timeout — another process still working it — is left
    alone. Falsified by dropping the `updated_at` bound: the young row fails."""
    old = NOW - timedelta(minutes=30)
    left = await _claim(ledger, "mid.$1001", now=old)
    flushing = await _claim(ledger, "mid.$1002", mode="live", now=old)
    young = await _claim(ledger, "mid.$1003", now=NOW - timedelta(minutes=1))
    done = await _claim(ledger, "mid.$1004", now=old)
    assert None not in (left, flushing, young, done)
    await ledger.finish(flushing, "staged", now=old, reply_text="x")
    assert await ledger.take_for_flush(flushing, now=old)
    await ledger.finish(done, "silent", now=old)

    assert await ledger.sweep_stale_claims(now=NOW, older_than=TIMEOUT) == {
        "failed": 1, "ambiguous": 1,
    }
    states = {cid: (await ledger.get(i)).state for cid, i in (
        ("1001", left), ("1002", flushing), ("1003", young), ("1004", done),
    )}
    assert states == {"1001": "failed", "1002": "ambiguous", "1003": "claimed", "1004": "silent"}
    assert (await ledger.get(left)).bridge_code == "swept_at_boot"
    # A swept card stays claimed: it is never answered again.
    assert await _claim(ledger, "mid.$1001") is None
