"""The tag-trace store contract, held against BOTH implementations: the SQL
store and the in-memory fake (`port → two implementations`).

What is pinned: `save`/`get`/`recent`/`turn_facts` scope, order and
completeness — `recent` is one room, `turn_facts` reduces the unit's
current identities, `get` is unit-filtered by `turn_id`."""

import uuid

import pytest

from nora.domain.tag_trace import TagTrace
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.testing.tag_traces import FakeTagTraceStore


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s = SqlTagTraceStore(create_sql_engine(pg_url))
        yield s
        await s._engine.dispose()
    else:
        yield FakeTagTraceStore()


def _trace(tag_id=None, cid="room-a", unit="u1", turn_id=None, created=1000,
           occasion="tag") -> TagTrace:
    return TagTrace(
        tag_id=tag_id or uuid.uuid4().hex, conversation_id=cid,
        messages=({"role": "user", "content": [{"text": "hi"}]},),
        transcript=(), outcome="send_reply", created_at=created,
        unit=unit, occasion=occasion, turn_id=turn_id,
    )


async def test_saved_traces_come_back_by_room(store):
    a = _trace(cid="room-a", created=100)
    b = _trace(cid="room-b", created=200)
    other_unit = _trace(cid="room-a", unit="other", created=300)
    for t in (a, b, other_unit):
        await store.save(t)

    room = await store.recent(conversation_id="room-a", limit=10)
    assert {t.tag_id for t in room} == {a.tag_id, other_unit.tag_id}


async def test_recent_is_newest_first_and_bounded(store):
    for i in range(5):
        await store.save(_trace(cid="room-a", created=1000 + i))
    unit = await store.recent(conversation_id="room-a", limit=3)
    assert len(unit) == 3
    createds = [t.created_at for t in unit]
    assert createds == sorted(createds, reverse=True)


async def test_get_returns_the_turn_only_within_its_unit(store):
    saved = _trace(unit="u1")
    await store.save(saved)
    stored = (await store.recent(conversation_id="room-a", limit=1))[0]
    got = await store.get(turn_id=stored.turn_id, unit="u1")
    assert got is not None and got.tag_id == saved.tag_id
    assert await store.get(turn_id=stored.turn_id, unit="other") is None


async def test_a_zero_limit_reads_nothing(store):
    await store.save(_trace())
    assert await store.recent(conversation_id="room-a", limit=0) == ()


def _fact_trace(tag_id, cid="room-a", unit="u1", created=1000, name="remind",
                subject="note:x", action="ask", status="ok", turn=1):
    """One trace holding one named toolUse/toolResult exchange."""
    messages = (
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": f"c-{tag_id}", "name": name,
                         "input": {"subject": subject, "action": action}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": f"c-{tag_id}", "status": "success",
                            "content": [{"json": {"status": status}}]}}]},
    )
    return TagTrace(
        tag_id=tag_id, conversation_id=cid, messages=messages,
        transcript=(), outcome="send_reply", created_at=created,
        unit=unit, occasion="tag", turn_id=turn,
    )


async def test_turn_facts_pair_structurally_and_reduce_latest_per_key(store):
    """The structural projection: pairing is by toolUseId, latest result per
    (name,subject,action) decides — prose that merely MENTIONS a key can
    never discharge it, and a different key's ok leaves it open."""
    await store.save(_fact_trace("old", subject="note:gone", action="ask-time",
                                 status="unresolved", turn=1))
    await store.save(_fact_trace("wrong", subject="note:other", action="ask-time",
                                 status="ok", turn=2))
    facts = await store.turn_facts(unit="u1", tool_names=("remind",))
    assert [i.arguments["subject"] for i in facts.open_items] == ["note:gone"]


async def test_turn_facts_same_trace_later_result_wins(store):
    """Inside one trace, the later block's result is the latest — ordinal
    ordering, not trace timestamp."""
    messages = (
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "old", "name": "remind",
                         "input": {"subject": "n", "action": "a"}},
             "toolUse_2_unused": None}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "old", "status": "success",
                            "content": [{"json": {"status": "unresolved"}}]}}]},
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "new", "name": "remind",
                         "input": {"subject": "n", "action": "a"}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "new", "status": "success",
                            "content": [{"json": {"status": "ok"}}]}}]},
    )
    await store.save(TagTrace(
        tag_id="t", conversation_id="room-a", messages=messages,
        transcript=(), outcome="send_reply", created_at=1000, unit="u1",
        occasion="tag", turn_id=7))
    facts = await store.turn_facts(unit="u1", tool_names=("remind",))
    assert facts.open_items == ()


async def test_turn_facts_closed_history_never_forces_the_cap(store):
    """Two hundred CLOSED attempts — each unresolved then ok — leave the
    read complete and empty; only CURRENT open identities count."""
    for i in range(1, 202):
        await store.save(_fact_trace(
            f"u{i}", subject=f"note:s-{i}", action="ask",
            status="unresolved", created=i, turn=i))
        await store.save(_fact_trace(
            f"k{i}", subject=f"note:s-{i}", action="ask",
            status="ok", created=10_000 + i, turn=1000 + i))
    await store.save(_fact_trace(
        "open", subject="note:open", action="ask", status="unresolved",
        created=99_999, turn=9999))
    facts = await store.turn_facts(unit="u1", tool_names=("remind",))
    assert not facts.capped
    assert [i.arguments["subject"] for i in facts.open_items] == ["note:open"]


async def test_turn_facts_text_json_result_and_units_isolate(store):
    """A `{text: <json-string>}` result parses the same as `{json:...}`; a
    second unit's facts never leak."""
    messages = (
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "c", "name": "remind",
                         "input": {"subject": "n", "action": "a"}}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "c", "status": "success",
                            "content": [{"text": '{"status": "unresolved"}'}]}}]},
    )
    await store.save(TagTrace(
        tag_id="t", conversation_id="room-a", messages=messages,
        transcript=(), outcome="send_reply", created_at=1, unit="u1",
        occasion="tag", turn_id=1))
    await store.save(_fact_trace("other-unit", unit="u2", status="unresolved"))
    facts = await store.turn_facts(unit="u1", tool_names=("remind",))
    assert len(facts.open_items) == 1
    facts2 = await store.turn_facts(unit="u2", tool_names=("remind",))
    assert len(facts2.open_items) == 1
    assert facts.open_items[0].tag_id != facts2.open_items[0].tag_id


async def test_turn_facts_call_ids_are_scoped_to_their_trace(store):
    from dataclasses import replace

    old = _fact_trace("older", subject="note:still-open", status="unresolved", created=1)
    new = _fact_trace("newer", subject="note:another", status="ok", created=2)
    for trace in (old, new):
        messages = tuple(
            {**msg, "content": [
                {key: {**value, "toolUseId": "reused"}}
                for block in msg["content"] for key, value in block.items()
            ]} for msg in trace.messages
        )
        await store.save(replace(trace, messages=messages))
    facts = await store.turn_facts(unit="u1", tool_names=("remind",))
    assert [i.arguments["subject"] for i in facts.open_items] == ["note:still-open"]


async def test_turn_facts_keep_competing_accepted_conclusions(store):
    await store.save(_fact_trace("first", name="decision_conclude", subject="dinner",
                                 action="", status="ok", created=1))
    await store.save(_fact_trace("second", name="decision_conclude", subject="dinner",
                                 action="", status="ok", created=2))
    facts = await store.turn_facts(unit="u1", tool_names=("decision_conclude",))
    assert {i.tag_id for i in facts.recent_items} == {"first", "second"}


async def test_turn_facts_last_result_for_a_call_wins(store):
    from dataclasses import replace

    trace = _fact_trace("retry", status="unresolved")
    result = trace.messages[-1]["content"][0]["toolResult"]
    await store.save(replace(trace, messages=(*trace.messages, {
        "role": "user", "content": [{"toolResult": {
            **result, "content": [{"json": {"status": "ok"}}],
        }}],
    })))
    facts = await store.turn_facts(unit="u1", tool_names=("remind",))
    assert facts.open_items == ()


async def test_turn_facts_malformed_status_stays_unresolved(store):
    await store.save(_fact_trace("malformed", status="not-a-status"))
    facts = await store.turn_facts(unit="u1", tool_names=("remind",))
    assert len(facts.open_items) == 1


async def test_turn_facts_bounds_database_transfer_after_reduction(store):
    if not hasattr(store, "_engine"):
        pytest.skip("sql-only transfer measurement")
    import sqlalchemy as sa

    for i in range(1000):
        await store.save(_fact_trace(f"closed-{i}", subject=f"note:{i}", created=i))
    for i in range(3):
        await store.save(_fact_trace(f"open-{i}", subject=f"note:open-{i}",
                                     status="unresolved", created=100 + i))
        await store.save(_fact_trace(f"conclusion-{i}", name="decision_conclude",
                                     action="", subject="same subject", created=200 + i))
    async with store._engine.connect() as conn:
        await conn.execute(sa.text("ANALYZE tag_trace"))
    captured = []

    def observed(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters, cursor.rowcount))

    engine = store._engine.sync_engine
    sa.event.listen(engine, "after_cursor_execute", observed)
    try:
        facts = await store.turn_facts(unit="u1", tool_names=("remind", "decision_conclude"),
                                       open_cap=1, recent_cap=2)
    finally:
        sa.event.remove(engine, "after_cursor_execute", observed)
    assert facts.capped and len(facts.open_items) == 1 and len(facts.recent_items) == 2
    assert len(captured) == 1
    statement, parameters, transferred = captured[0]
    assert transferred == 4  # one extra open row measures the remainder
    async with store._engine.connect() as conn:
        plan = await conn.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters)
        print("\nPRODUCTION turn_facts plan:\n" + "\n".join(row[0] for row in plan))
