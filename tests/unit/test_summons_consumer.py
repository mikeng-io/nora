"""`SummonsInbox` and `SummonsConsumer`: a summons card from the bridge's SSE
stream is queued only when the summons is on (and counted either way); the
consumer works ONE card per step, oldest first; a live `staged` row's flush is
retried before any new card; it backs off on `needs_human` and 503; and the
boot sweep fails a row a dead process left `claimed`, never retrying it.

Over the real occasion and client against the recording fake bridge, so "one
at a time" is counted in REQUESTS the session would have made, which is what
the rule is about. And no request is ever `GET /mentions`: the notification
feed is not read.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

from nora.app.summons_consumer import MAX_BACKOFF_SECONDS, RETRY_SECONDS, SummonsConsumer
from nora.app.summons_inbox import SummonsInbox
from nora.obs.metrics import Metrics
from nora.testing import InMemorySummonsLedger, ScriptedTurn
from tests.unit.summons_bridge import (
    STRANGER_IGID,
    FakeSummonsBridge,
    comment_item,
    default_comments,
    envelope,
    serve,
)
from tests.unit.test_summons_occasion import NOW, REPLY, SUMMONER_IGID, World, _card

OLDER = "17900000000000101"
NEWER = "17900000000000102"


@pytest.fixture
async def world(tmp_path):
    fake = FakeSummonsBridge()
    async for url in serve(fake):
        yield World(fake, url, tmp_path)


def _consumer(world: World, *, mode: str = "dry_run", turns=None,
              inbox: SummonsInbox | None = None) -> SummonsConsumer:
    occasion = world.occasion(
        mode=mode, turns=turns if turns is not None else [ScriptedTurn(
            tool_calls=[("send_reply", {"text": REPLY})],
        )] * 4,
    )
    return SummonsConsumer(
        inbox=inbox or SummonsInbox(enabled=True, metrics=world.metrics),
        occasion=occasion, ledger=world.ledger, metrics=world.metrics,
        clock=lambda: NOW,
    )


def _reads(fake: FakeSummonsBridge) -> list[str]:
    return [path for _, path in fake.calls]


# ---- the inbox ------------------------------------------------------------------


def test_a_card_is_queued_only_when_the_summons_is_on_and_counted_either_way():
    metrics = Metrics()
    off = SummonsInbox(enabled=False, metrics=metrics)
    off.offer(_card())
    assert len(off) == 0
    assert metrics.get("nora_summons_intake_total", outcome="disabled") == 1
    on = SummonsInbox(enabled=True, metrics=metrics)
    on.offer(_card())
    assert len(on) == 1
    assert metrics.get("nora_summons_intake_total", outcome="queued") == 1
    on.malformed()
    assert metrics.get("nora_summons_intake_total", outcome="malformed") == 1


async def test_a_full_inbox_drops_the_oldest_card_and_counts_it():
    metrics = Metrics()
    inbox = SummonsInbox(enabled=True, metrics=metrics, maxsize=2)
    for i in range(3):
        inbox.offer(_card(message_id=f"mid.${i}"))
    assert metrics.get("nora_summons_intake_total", outcome="overflow") == 1
    assert [(await inbox.get(wait_seconds=0)).message_id for _ in range(2)] == [
        "mid.$1", "mid.$2",
    ]
    assert await inbox.get(wait_seconds=0) is None


async def test_a_waiting_consumer_is_woken_by_an_offer():
    inbox = SummonsInbox(enabled=True, metrics=Metrics())
    waiter = asyncio.create_task(inbox.get(wait_seconds=5))
    await asyncio.sleep(0)
    inbox.offer(_card())
    card = await asyncio.wait_for(waiter, 1)
    assert card is not None and card.message_id == _card().message_id


# ---- the consumer ---------------------------------------------------------------


async def test_one_card_per_step_oldest_first(world):
    world.fake.comments = (200, {"items": [
        *default_comments(),
        comment_item(OLDER, "@noraexample older?", username="miles_example",
                     igid=SUMMONER_IGID, at="2026-09-25T11:50:00Z"),
    ]})
    consumer = _consumer(world)
    consumer._inbox.offer(_card(message_id="mid.$older", at=NOW - timedelta(minutes=10)))
    consumer._inbox.offer(_card(message_id="mid.$newer", at=NOW - timedelta(minutes=2)))
    await consumer.step(wait_seconds=0)
    assert {r.message_id: r.state for r in world.ledger._rows.values()} == {
        "mid.$older": "staged",
    }
    assert len(_reads(world.fake)) == 2  # its post and its comments, nothing else
    await consumer.step(wait_seconds=0)
    # The second card resolves to the SAME comment (the newest by the sender
    # that tags her), which the first already holds: one comment, one reply.
    assert {r.message_id: r.state for r in world.ledger._rows.values()} == {
        "mid.$older": "staged", "mid.$newer": "duplicate",
    }
    assert world.runner.invocations == 1
    assert "/mentions" not in _reads(world.fake)


async def test_the_same_card_delivered_twice_is_worked_once(world):
    consumer = _consumer(world)
    consumer._inbox.offer(_card())
    consumer._inbox.offer(_card())
    await consumer.step(wait_seconds=0)
    await consumer.step(wait_seconds=0)
    assert len(world.ledger._rows) == 1
    assert len(_reads(world.fake)) == 2
    assert world.metrics.get("nora_summons_total", outcome="already_claimed",
                             mode="dry_run") == 1


async def test_an_idle_step_waits_for_a_card_and_does_nothing_else(world):
    consumer = _consumer(world)
    await consumer.step(wait_seconds=0.01)
    assert world.fake.calls == [] and world.ledger._rows == {}
    assert consumer.pause_seconds == 0


async def test_a_live_staged_row_is_retried_before_any_new_card(world):
    world.fake.write = envelope("too_soon")
    consumer = _consumer(world, mode="live")
    consumer._inbox.offer(_card())
    await consumer.step(wait_seconds=0)
    assert consumer.pause_seconds == RETRY_SECONDS  # waited out, not hammered
    world.fake.calls.clear()
    world.fake.write = (201, {"id": "17900000000000777"})
    consumer._inbox.offer(_card(message_id="mid.$another"))
    await consumer.step(wait_seconds=0)
    assert world.fake.calls == [("POST", "/posts/CmiJBr2LAAB/comments")]
    assert len(consumer._inbox) == 1  # the new card waits its turn
    states = {r.message_id: r.state for r in world.ledger._rows.values()}
    assert states == {_card().message_id: "published"}
    assert consumer.pause_seconds == 0


@pytest.mark.parametrize("code", ["needs_human", "unavailable"])
async def test_the_consumer_backs_off_on_a_checkpoint_or_a_503_and_recovers(world, code):
    world.fake.post = envelope(code)
    consumer = _consumer(world)
    for i in range(12):
        consumer._inbox.offer(_card(message_id=f"mid.${i}"))
    await consumer.step(wait_seconds=0)
    assert consumer.pause_seconds == 2 * RETRY_SECONDS
    await consumer.step(wait_seconds=0)
    assert consumer.pause_seconds == 4 * RETRY_SECONDS
    for _ in range(10):
        await consumer.step(wait_seconds=0)
    assert consumer.pause_seconds == MAX_BACKOFF_SECONDS
    world.fake.post = FakeSummonsBridge().post
    consumer._inbox.offer(_card(message_id="mid.$ok"))
    await consumer.step(wait_seconds=0)
    assert consumer.pause_seconds == 0 and consumer._backoff == 0


async def test_a_card_filed_without_a_bridge_call_does_not_clear_the_backoff(world):
    """A stranger's card during a checkpoint says nothing about the session."""
    world.fake.post = envelope("needs_human")
    consumer = _consumer(world)
    consumer._inbox.offer(_card(message_id="mid.$1"))
    await consumer.step(wait_seconds=0)
    backoff = consumer._backoff
    assert backoff == 2 * RETRY_SECONDS
    consumer._inbox.offer(_card(message_id="mid.$2", igid=STRANGER_IGID))
    await consumer.step(wait_seconds=0)
    assert consumer._backoff == backoff


async def test_any_other_failure_does_not_back_off(world):
    world.fake.post = envelope("internal")
    consumer = _consumer(world)
    consumer._inbox.offer(_card())
    await consumer.step(wait_seconds=0)
    assert consumer.pause_seconds == 0


async def test_the_sweep_fails_an_old_claimed_row_and_it_is_never_retried(world):
    await world.ledger.claim(
        message_id="mid.$dead", media_id="1", shortcode="CmiJBr2LAAB",
        author_igid=SUMMONER_IGID, mention_at=NOW - timedelta(minutes=30),
        mode="live", now=NOW - timedelta(minutes=20),
    )
    consumer = _consumer(world, mode="live")
    assert await consumer.recover() == 1
    (row,) = world.ledger._rows.values()
    assert row.state == "failed" and row.bridge_code == "swept_at_boot"
    await consumer.step(wait_seconds=0)
    assert world.fake.posts() == []
    assert world.runner.invocations == 0


async def test_boot_runs_the_sweep_before_the_first_card(world):
    await world.ledger.claim(
        message_id="mid.$dead", media_id="1", shortcode="CmiJBr2LAAB",
        author_igid=SUMMONER_IGID, mention_at=NOW - timedelta(minutes=30),
        mode="live", now=NOW - timedelta(minutes=20),
    )
    consumer = _consumer(world, mode="live")
    task = asyncio.create_task(consumer.run())
    try:
        for _ in range(50):
            await asyncio.sleep(0.01)
            (row,) = world.ledger._rows.values()
            if row.state != "claimed":
                break
        assert row.state == "failed"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_young_claimed_row_another_process_holds_is_left_alone(world):
    await world.ledger.claim(
        message_id="mid.$young", media_id="1", shortcode="CmiJBr2LAAB",
        author_igid=SUMMONER_IGID, mention_at=NOW, mode="live",
        now=NOW - timedelta(minutes=2),
    )
    assert await _consumer(world, mode="live").recover() == 0
    (row,) = world.ledger._rows.values()
    assert row.state == "claimed"


class _LeakyLedger(InMemorySummonsLedger):
    """A database error whose message carries a bound parameter, the way
    SQLAlchemy renders `[parameters: (...)]` into `str(exc)`."""

    async def awaiting_flush(self):
        raise RuntimeError(
            "(psycopg.errors.UniqueViolation) duplicate key [SQL: UPDATE ...] "
            "[parameters: ('@victim you are a fraud', '10000999')]"
        )


async def test_a_failed_step_logs_its_type_never_its_parameters(world, caplog):
    world.ledger = _LeakyLedger()
    consumer = _consumer(world)
    with caplog.at_level(logging.DEBUG, logger="nora.app.summons_consumer"):
        await consumer._guarded(consumer.step, "consumer step")
    assert "RuntimeError" in caplog.text
    assert "victim" not in caplog.text and "10000999" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    assert world.metrics.get("nora_errors_total", stage="summons_consumer") == 1
    assert consumer.pause_seconds == RETRY_SECONDS  # never a hot loop
