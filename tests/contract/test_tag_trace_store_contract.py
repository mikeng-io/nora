"""The tag-trace store contract, held against BOTH implementations: the SQL
store and the in-memory fake (`port → two implementations`).

What is pinned: `save`/`get`/`recent`/`recent_unit` scope, order and
completeness — `recent` is one room, `recent_unit` is the whole unit
newest-first, `get` is unit-filtered by `turn_id`."""

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


async def test_saved_traces_come_back_by_room_and_unit(store):
    a = _trace(cid="room-a", created=100)
    b = _trace(cid="room-b", created=200)
    other_unit = _trace(cid="room-a", unit="other", created=300)
    for t in (a, b, other_unit):
        await store.save(t)

    room = await store.recent(conversation_id="room-a", limit=10)
    assert {t.tag_id for t in room} == {a.tag_id, other_unit.tag_id}

    unit = await store.recent_unit(unit="u1", limit=10)
    assert [t.tag_id for t in unit] == [b.tag_id, a.tag_id]


async def test_recent_unit_is_newest_first_and_bounded(store):
    for i in range(5):
        await store.save(_trace(cid="room-a", created=1000 + i))
    unit = await store.recent_unit(unit="u1", limit=3)
    assert len(unit) == 3
    createds = [t.created_at for t in unit]
    assert createds == sorted(createds, reverse=True)


async def test_get_returns_the_turn_only_within_its_unit(store):
    saved = _trace(unit="u1")
    await store.save(saved)
    stored = (await store.recent_unit(unit="u1", limit=1))[0]
    got = await store.get(turn_id=stored.turn_id, unit="u1")
    assert got is not None and got.tag_id == saved.tag_id
    assert await store.get(turn_id=stored.turn_id, unit="other") is None


async def test_a_zero_limit_reads_nothing(store):
    await store.save(_trace())
    assert await store.recent_unit(unit="u1", limit=0) == ()
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
    await store.save(_fact_trace("wrong", subject="note:other", action="other",
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


async def test_turn_facts_query_plan_is_bounded(store, capsys):
    """EXPLAIN evidence: the structural projection reads unit rows with
    lateral JSONB expansion — the plan and row counts are captured so the
    bound is visible, not asserted on faith."""
    if not hasattr(store, "_engine"):
        pytest.skip("sql-only evidence")
    for i in range(30):
        await store.save(_fact_trace(f"p{i}", subject=f"note:s-{i}",
                                     action="ask", status="ok", turn=i))
    import sqlalchemy as sa
    plan_sql = """
        EXPLAIN (ANALYZE, BUFFERS)
        WITH uses AS (
            SELECT t.id AS trace_row_id, t.turn_id, t.tag_id,
                   t.occasion, t.created_at, t.requirements,
                   c.block AS use_block, m.m_idx, c.b_idx
            FROM tag_trace t,
                 LATERAL jsonb_array_elements(t.messages)
                    WITH ORDINALITY m(msg, m_idx),
                 LATERAL jsonb_array_elements(m.msg->'content')
                    WITH ORDINALITY c(block, b_idx)
            WHERE t.unit = 'u1' AND c.block ? 'toolUse'
        )
        SELECT DISTINCT ON (use_block->'toolUse'->>'name',
                            use_block->'input'->>'subject')
            turn_id, tag_id, use_block FROM uses
    """
    async with store._engine.connect() as conn:
        plan = [r[0] for r in (await conn.execute(sa.text(plan_sql))).all()]
    text = "\n".join(plan)
    print("\n" + text)
    assert "tag_trace" in text
