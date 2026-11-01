"""`SqlProfileStore.profile_write`'s `receipt=` rides the write's own
transaction: the result row and the lines commit or roll back together, so a
crash between them neither loses the receipt nor leaves it behind on a
refused plan (the Register act-result pattern). The last test drives the
whole seam — `WakeMemory` → real store → `act_result` replay — not just the
callback's presence."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_memory import WakeMemory
from nora.domain.memory_graph import Source
from nora.domain.profile import Account, AddOp, ProfileCitable, Said
from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.schema import COMPREHEND_RESULT, PROFILE, PROFILE_LINE

UNIT = "u1"
PERSON = "ada"
ROOM = "room-a"
ACCOUNT = Account(key=f"{UNIT}#character:{PERSON}", entity_kind="human",
                  unit=UNIT, subject=PERSON)
DAY1 = 1_735_600_800_000   # 10:00 HKT 2025-01-01
DAY1B = 1_735_615_200_000  # 14:00 HKT 2025-01-01
DAY2 = 1_735_790_400_000   # 12:00 HKT 2025-01-02
NOW = 1_736_049_600_000    # 2025-01-05
AS_OF = datetime.fromtimestamp(NOW / 1000, tz=UTC)


@pytest.fixture
async def store(pg_module_url):
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as conn:
        await conn.execute(sa.delete(PROFILE_LINE))
        await conn.execute(sa.delete(PROFILE))
        await conn.execute(sa.delete(COMPREHEND_RESULT))
    yield (
        SqlProfileStore(engine, screen=lambda _t: None),
        SqlComprehendRecords(
            engine, media_join=None, rooms_by_unit={}, persons_by_unit={}),
        engine,
    )
    await engine.dispose()


def _write_args() -> dict:
    return {
        "account": ACCOUNT,
        "ops": (AddOp(
            part="traits", kind="habit",
            content={"statement": "walks daily", "context": "weeknights",
                     "action": "walks", "rhythm": "daily", "setting": "unknown"},
            text="walks daily", mode="induced",
            citations=("41", "42", "43")),),
        "citable": ProfileCitable(
            messages={41: Said(DAY1, PERSON, ROOM),
                      42: Said(DAY1B, PERSON, ROOM),
                      43: Said(DAY2, PERSON, ROOM)},
            listed_rooms=frozenset({ROOM})),
        "now_ms": NOW, "written_by_session": "cognition:profile:9",
        "apply_decay": True,
    }


async def test_the_receipt_commits_with_the_write(store) -> None:
    profile, records, _engine = store
    result = await profile.profile_write(
        **_write_args(), expected_revision=0,
        receipt=lambda conn, payload: records.act_result_on(conn, "k", 9, payload))
    assert result.status == "ok"
    assert (await records.act_result("k", 9))["status"] == "ok"
    assert (await profile.account_state(key=ACCOUNT.key))[0] == 1
    assert len(await profile.account_heads(key=ACCOUNT.key)) == 1


async def test_a_failed_receipt_rolls_the_write_back(store) -> None:
    profile, records, _engine = store

    async def bad_receipt(conn, payload):
        raise RuntimeError("receipt write failed")

    with pytest.raises(RuntimeError, match="receipt write failed"):
        await profile.profile_write(**_write_args(), receipt=bad_receipt)
    assert (await profile.account_state(key=ACCOUNT.key))[0] == 0
    assert await profile.account_heads(key=ACCOUNT.key) == ()
    assert await records.act_result("k", 9) is None


def _session_op() -> dict:
    return {
        "op": "add", "part": "traits", "kind": "habit", "mode": "induced",
        "content": {
            "statement": "walks daily", "context": "weeknights",
            "action": "walks", "rhythm": "daily", "setting": "unknown"},
        "citations": ["message:41", "message:42", "message:43"],
    }


async def test_an_identical_call_replays_its_committed_result(store) -> None:
    """Through the real session/store: a committed write + receipt replays
    the recorded result instead of double-writing."""
    profile, _records, engine = store
    records = SqlComprehendRecords(
        engine, media_join=None, rooms_by_unit={},
        persons_by_unit={UNIT: {PERSON}})
    memory = WakeMemory(
        graph=SimpleNamespace(), records=records,
        metrics=SimpleNamespace(inc=lambda *a, **k: None),
        profile=profile, listed_rooms=lambda u, p: (ROOM,))
    fetched = GraphFetched()
    for key in (("message", "41"), ("message", "42"), ("message", "43")):
        fetched.show(Source(key, "record", UNIT, text="x"))
    state = SimpleNamespace(
        unit=UNIT, session_id=9, now_ms=NOW,
        memory_items={0: SimpleNamespace(
            work=SimpleNamespace(id="w1", input_hash="h", as_of=AS_OF),
            snapshot={
                "messages": [], "metadata": {},
                "source_keys": {"r1": ["message", "41"],
                                "r2": ["message", "42"],
                                "r3": ["message", "43"]}})},
        graph_fetched=fetched,
        records=tuple(
            SimpleNamespace(
                message_id=mid, at_ms=ts, person_id=PERSON,
                room=SimpleNamespace(conversation_id=ROOM), kind="message")
            for mid, ts in ((41, DAY1), (42, DAY1B), (43, DAY2))),
        admitted_relations={}, admitted_evidence=[], profile_accounts={})
    await memory.account_view(state, ["person", PERSON])
    first = await memory.profile(state, ["person", PERSON],
                                 [_session_op()], act_key="call-k")
    assert first["status"] == "ok", first
    assert (await records.act_result("call-k", 9))["status"] == "ok"
    # A post-commit restart replays the recorded result — no second write.
    replay = await memory.profile(state, ["person", PERSON],
                                  [_session_op()], act_key="call-k")
    assert replay["status"] == "ok"
    assert len(await profile.account_heads(key=ACCOUNT.key)) == 1
    assert (await profile.account_state(key=ACCOUNT.key))[0] == 1
