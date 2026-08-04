import asyncio
import sqlite3

import pytest

from nora.models import NormalizedMessage
from nora.store import db


@pytest.fixture
async def dbp(tmp_path):
    path = tmp_path / "messages.db"
    await db.init(path)
    return path


def _msg(conv="g1", body="hi", ts=1, sender="+852111", name="Miles") -> NormalizedMessage:
    return NormalizedMessage("signal", conv, "group", sender, name, ts, body, {"e": 1})


async def test_append_returns_a_rowid(dbp):
    row_id = await db.append(dbp, _msg())
    assert isinstance(row_id, int) and row_id > 0


async def test_recent_returns_oldest_first(dbp):
    for i in range(3):
        await db.append(dbp, _msg(body=f"m{i}", ts=i))
    rows = await db.recent(dbp, "g1", 10)
    assert [r["body"] for r in rows] == ["m0", "m1", "m2"]


async def test_recent_limits_to_the_newest_n_then_reverses(dbp):
    for i in range(5):
        await db.append(dbp, _msg(body=f"m{i}", ts=i))
    rows = await db.recent(dbp, "g1", 2)
    assert [r["body"] for r in rows] == ["m3", "m4"]


async def test_recent_rows_carry_the_fields_the_prefill_needs(dbp):
    await db.append(dbp, _msg())
    row = (await db.recent(dbp, "g1", 1))[0]
    assert set(row) >= {"sender_name", "sender_id", "body", "is_nora", "ts"}


async def test_recent_is_scoped_to_one_conversation(dbp):
    await db.append(dbp, _msg(conv="g1", body="a"))
    await db.append(dbp, _msg(conv="g2", body="b"))
    assert [r["body"] for r in await db.recent(dbp, "g1", 10)] == ["a"]


async def test_rolling_window_per_conversation(dbp):
    for i in range(600):
        await db.append(dbp, _msg(conv="A", body=f"a{i}", ts=i), max_per_conversation=500)
    await db.append(dbp, _msg(conv="B", body="b0", ts=0), max_per_conversation=500)
    assert len(await db.recent(dbp, "A", 10_000)) == 500
    assert len(await db.recent(dbp, "B", 10_000)) == 1
    assert (await db.recent(dbp, "A", 10_000))[0]["body"] == "a100"


async def test_nora_rows_are_flagged_and_default_to_pending(dbp):
    row_id = await db.append(dbp, _msg(body="reply"), is_nora=True)
    row = (await db.recent(dbp, "g1", 1))[0]
    assert row["is_nora"] == 1
    assert row["delivery_status"] == "pending"
    assert row_id > 0


async def test_delivery_status_transitions(dbp):
    row_id = await db.append(dbp, _msg(), is_nora=True)
    await db.set_delivery_status(dbp, row_id, "sent")
    assert (await db.recent(dbp, "g1", 1))[0]["delivery_status"] == "sent"
    await db.set_delivery_status(dbp, row_id, "failed")
    assert (await db.recent(dbp, "g1", 1))[0]["delivery_status"] == "failed"


async def test_invalid_delivery_status_is_rejected(dbp):
    row_id = await db.append(dbp, _msg())
    with pytest.raises(ValueError):
        await db.set_delivery_status(dbp, row_id, "delivered-ish")


async def test_conversation_type_is_constrained(dbp):
    bad = NormalizedMessage("signal", "g1", "channel", "+1", "X", 0, "hi", {})
    with pytest.raises(sqlite3.IntegrityError):  # the schema's CHECK constraint
        await db.append(dbp, bad)


async def test_raw_json_round_trips_the_envelope(dbp):
    import json

    await db.append(dbp, _msg())
    rows = await db.recent(dbp, "g1", 1)
    assert json.loads(rows[0]["raw_json"]) == {"e": 1}


async def test_concurrent_appends_under_wal_do_not_lose_rows(dbp):
    await asyncio.gather(*(db.append(dbp, _msg(body=f"c{i}", ts=i)) for i in range(50)))
    assert len(await db.recent(dbp, "g1", 10_000)) == 50


async def test_stats_reports_counts_for_health(dbp):
    await db.append(dbp, _msg())
    await db.append(dbp, _msg(body="r"), is_nora=True)
    s = await db.stats(dbp)
    assert s["messages"] == 2
    assert s["conversations"] == 1
    assert s["nora_messages"] == 1


async def test_init_is_idempotent(dbp):
    await db.init(dbp)
    await db.init(dbp)
    assert (await db.stats(dbp))["messages"] == 0
