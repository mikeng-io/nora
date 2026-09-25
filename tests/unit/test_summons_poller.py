"""`SummonsPoller`: one summons reaches the bridge per tick, oldest first;
cheap outcomes are filed in the same tick; a live `staged` row's flush is
that tick's one action; it backs off on `needs_human` and 503; and the boot
sweep fails a row a dead process left `claimed`, never retrying it.

Over the real occasion and client against the recording fake bridge, so
"one per tick" is counted in REQUESTS the session would have made, which is
what the rule is about.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from nora.app.summons_poller import MAX_BACKOFF_SECONDS, SummonsPoller
from nora.testing import InMemorySummonsLedger, ScriptedTurn
from tests.unit.summons_bridge import (
    STRANGER_IGID,
    FakeSummonsBridge,
    comment_item,
    default_comments,
    envelope,
    mention_item,
    serve,
)
from tests.unit.test_summons_occasion import NOW, REPLY, SUMMONER_IGID, World

OLDER = "17900000000000101"
NEWER = "17900000000000102"


@pytest.fixture
async def world(tmp_path):
    fake = FakeSummonsBridge()
    async for url in serve(fake):
        yield World(fake, url, tmp_path)


def _poller(world: World, *, mode: str = "dry_run", turns=None) -> SummonsPoller:
    occasion = world.occasion(
        mode=mode, turns=turns if turns is not None else [ScriptedTurn(
            tool_calls=[("send_reply", {"text": REPLY})],
        )],
    )
    return SummonsPoller(
        client=world.client, occasion=occasion, ledger=world.ledger,
        metrics=world.metrics, poll_seconds=120, clock=lambda: NOW,
    )


def _two_summons(fake: FakeSummonsBridge) -> None:
    """Two answerable summonses, NEWER listed first as the feed does."""
    fake.mentions = (200, {"items": [
        mention_item(comment_id=NEWER, at="2026-09-25T11:58:00Z"),
        mention_item(comment_id=OLDER, at="2026-09-25T11:50:00Z"),
    ], "next_cursor": None})
    fake.comments = (200, {"items": [
        *default_comments(),
        comment_item(OLDER, "@noraexample older?", username="miles_example",
                     igid=SUMMONER_IGID, at="2026-09-25T11:50:00Z"),
        comment_item(NEWER, "@noraexample newer?", username="miles_example",
                     igid=SUMMONER_IGID, at="2026-09-25T11:58:00Z"),
    ]})


def _reads(fake: FakeSummonsBridge) -> list[str]:
    return [path for method, path in fake.calls if path != "/mentions"]


async def test_one_summons_per_tick_oldest_first(world):
    _two_summons(world.fake)
    poller = _poller(world)
    await poller.tick()
    first = {r.comment_id: r.state for r in world.ledger._rows.values()}
    assert first == {OLDER: "staged"}
    assert len(_reads(world.fake)) == 2  # its post and its comments, nothing else
    await poller.tick()
    second = {r.comment_id: r.state for r in world.ledger._rows.values()}
    assert second == {OLDER: "staged", NEWER: "staged"}
    assert world.runner.invocations == 2  # one model turn per tick


async def test_cheap_outcomes_are_filed_in_the_same_tick_as_the_one_read(world):
    world.fake.mentions = (200, {"items": [
        mention_item(comment_id="17900000000000201", igid=STRANGER_IGID,
                     at="2026-09-25T11:00:00Z"),
        mention_item(comment_id="17900000000000202", at="2026-08-01T00:00:00Z"),
        mention_item(comment_id="17900000000000203", at=None),
        mention_item(),
    ]})
    await _poller(world).tick()
    states = sorted(r.state for r in world.ledger._rows.values())
    assert states == ["ignored_summoner", "staged", "stale", "stale"]
    assert len(_reads(world.fake)) == 2


async def test_a_mention_already_in_the_ledger_is_not_handled_again(world):
    poller = _poller(world)
    await poller.tick()
    await poller.tick()
    assert len(world.ledger._rows) == 1
    assert len(_reads(world.fake)) == 2


async def test_a_live_staged_row_is_the_ticks_one_action_and_reads_no_mentions(world):
    world.fake.write = envelope("too_soon")
    poller = _poller(world, mode="live")
    await poller.tick()
    world.fake.calls.clear()
    world.fake.write = (201, {"id": "17900000000000777"})
    await poller.tick()
    assert world.fake.calls == [("POST", "/posts/CmiJBr2LAAB/comments")]
    (row,) = world.ledger._rows.values()
    assert row.state == "published"


@pytest.mark.parametrize("code", ["needs_human", "unavailable"])
async def test_the_poller_backs_off_on_a_checkpoint_or_a_503_and_recovers(world, code):
    world.fake.mentions = envelope(code)
    poller = _poller(world)
    assert poller.next_delay == 120
    await poller.tick()
    assert poller.next_delay == 240
    await poller.tick()
    assert poller.next_delay == 480
    for _ in range(10):
        await poller.tick()
    assert poller.next_delay == MAX_BACKOFF_SECONDS
    assert world.metrics.get("nora_summons_polls_total", status=code) == 12
    world.fake.mentions = (200, {"items": [], "next_cursor": None})
    await poller.tick()
    assert poller.next_delay == 120


async def test_a_read_that_hits_a_checkpoint_mid_summons_backs_off_too(world):
    world.fake.post = envelope("needs_human")
    poller = _poller(world)
    await poller.tick()
    assert poller.next_delay == 240


async def test_any_other_failure_does_not_back_off(world):
    world.fake.mentions = envelope("internal")
    poller = _poller(world)
    await poller.tick()
    assert poller.next_delay == 120
    assert world.metrics.get("nora_summons_polls_total", status="error") == 1


async def test_the_sweep_fails_an_old_claimed_row_and_it_is_never_retried(world):
    await world.ledger.claim(
        comment_id="17900000000000001", media_id="1", shortcode="CmiJBr2LAAB",
        author_igid=SUMMONER_IGID, mention_at=NOW - timedelta(minutes=30),
        mode="live", now=NOW - timedelta(minutes=20),
    )
    poller = _poller(world, mode="live")
    assert await poller.recover() == 1
    (row,) = world.ledger._rows.values()
    assert row.state == "failed" and row.bridge_code == "swept_at_boot"
    await poller.tick()
    assert world.fake.posts() == []
    assert world.runner.invocations == 0


async def test_a_young_claimed_row_another_process_holds_is_left_alone(world):
    await world.ledger.claim(
        comment_id="17900000000000001", media_id="1", shortcode="CmiJBr2LAAB",
        author_igid=SUMMONER_IGID, mention_at=NOW, mode="live",
        now=NOW - timedelta(minutes=2),
    )
    assert await _poller(world, mode="live").recover() == 0
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


async def test_a_failed_tick_logs_its_type_never_its_parameters(world, caplog):
    world.ledger = _LeakyLedger()
    poller = _poller(world)
    with caplog.at_level(logging.DEBUG, logger="nora.app.summons_poller"):
        await poller._guarded(poller.tick, "poller tick")
    assert "RuntimeError" in caplog.text
    assert "victim" not in caplog.text and "10000999" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    assert world.metrics.get("nora_errors_total", stage="summons_poller") == 1
