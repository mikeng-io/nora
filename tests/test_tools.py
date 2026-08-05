import pytest

from nora.agent.guards import Deliverer
from nora.agent.tools import build_tools
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics
from nora.persona.loader import PersonaStore
from nora.persons import load_persons
from tests.fakes import FakeHoncho, SinkSender
from tests.test_persona_loader import write

TARGET = ConversationTarget("signal", "g1", "group")


@pytest.fixture
async def wiring(tmp_path):
    dbp = tmp_path / "messages.db"
    run_migrations(dbp)
    store = SqlAuditStore(create_sql_engine(dbp))
    for i in range(3):
        await store.append(message=NormalizedMessage("signal", "g1", "group", "+852111",
                                                      "Miles", i, f"m{i}", {}))
    # A second, unrelated conversation with its own rows — the cross-group
    # leak probe (Task 1) needs a real second group to prove read_buffer
    # cannot be steered into it by a model-supplied conversation_id.
    for i in range(3):
        await store.append(message=NormalizedMessage("signal", "g2", "group", "+852222",
                                                      "Alice", i, f"secret{i}", {}))
    persons_file = tmp_path / "identity.toml"
    persons_file.write_text(
        "[persons.miles]\nname = \"Miles\"\n[[persons.miles.signal]]\nphone = \"+852111\"\n"
    )
    pdir = tmp_path / "persona"
    pdir.mkdir()
    write(pdir, "soul.md", name="soul", order=10, body="You are Nora.")
    persona = PersonaStore(pdir, canary="C", metrics=Metrics()).get()
    sink = SinkSender()
    honcho = FakeHoncho(cards={"miles": ["likes espresso"]})
    deliverer = Deliverer(store=store, adapter=sink, target=TARGET, persona=persona,
                          canary="C", tag_id="t1", nora_sender_id="nora", metrics=Metrics())
    metrics = Metrics()
    tools = build_tools(store=store, conversation_id="g1", conversation_type="group",
                        persons=load_persons(persons_file), honcho=honcho,
                        deliverer=deliverer, metrics=metrics, context_window=40)
    try:
        yield {"tools": {t.tool_name: t for t in tools}, "sink": sink,
               "honcho": honcho, "deliverer": deliverer, "store": store, "metrics": metrics}
    finally:
        await store.aclose()


def test_every_declared_tool_is_built(wiring):
    assert set(wiring["tools"]) == {"now", "read_buffer", "read_peer_card", "send_reply"}


def test_feed_honcho_is_not_a_tool(wiring):
    assert "feed_honcho" not in wiring["tools"]


def test_every_tool_has_a_model_facing_docstring(wiring):
    for name, tool in wiring["tools"].items():
        doc = tool.__doc__ or ""
        assert len(doc.strip()) > 40, f"{name} needs a docstring written for the model"


async def test_read_buffer_returns_rows_oldest_first(wiring):
    rows = await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40)
    assert [r["body"] for r in rows] == ["m0", "m1", "m2"]


async def test_read_buffer_ignores_a_model_supplied_conversation_id_and_stays_bound(wiring):
    """Task 1 PROBE: tools are bound to "g1"; the model asks for "g2", an id
    it never saw and has no business reading. Before the fix, `read_buffer`'s
    own `conversation_id` parameter shadowed the closure's bound value and
    was passed straight to the store — this is the reproduction of exactly
    that scenario. It must come back with g1's rows, never g2's secrets."""
    rows = await _call(wiring["tools"]["read_buffer"], conversation_id="g2", limit=40)
    bodies = [r["body"] for r in rows]
    assert bodies == ["m0", "m1", "m2"]
    assert not any(b.startswith("secret") for b in bodies)


async def test_read_buffer_counts_a_conversation_id_mismatch(wiring):
    await _call(wiring["tools"]["read_buffer"], conversation_id="g2", limit=40)
    assert wiring["metrics"].get(
        "nora_tool_scope_violations_total", tool="read_buffer"
    ) == 1.0


async def test_read_buffer_does_not_count_a_matching_conversation_id(wiring):
    await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40)
    assert wiring["metrics"].get(
        "nora_tool_scope_violations_total", tool="read_buffer"
    ) == 0.0


async def test_read_buffer_returns_only_model_relevant_fields(wiring):
    rows = await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40)
    assert set(rows[0]) == {"sender", "body", "ts"}    # no raw_json, no ids


async def test_read_buffer_clamps_a_hostile_limit_before_it_reaches_the_store(wiring, monkeypatch):
    """`limit` is a model-supplied argument. SQLite renders `LIMIT -1` as
    unbounded rather than zero, so an unclamped negative limit would dump the
    entire conversation into context; `limit=0` is equally meaningless. This
    asserts what `read_buffer` actually sends downstream — independent of
    whatever the store itself does with a non-positive limit — so it fails if
    the tool-level clamp regresses even though the store has its own guard."""
    seen: list[int] = []
    real_recent = wiring["store"].recent

    async def spy(*, conversation_id, limit):
        seen.append(limit)
        return await real_recent(conversation_id=conversation_id, limit=limit)

    monkeypatch.setattr(wiring["store"], "recent", spy)
    for hostile in (-1, 0):
        rows = await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=hostile)
        assert len(rows) <= 1
    assert seen == [1, 1]


async def test_read_peer_card_resolves_the_sender_to_a_person(wiring):
    assert await _call(wiring["tools"]["read_peer_card"], sender_id="+852111") == \
        ["likes espresso"]


async def test_read_peer_card_returns_empty_for_an_unmapped_sender(wiring):
    assert await _call(wiring["tools"]["read_peer_card"], sender_id="+852999") == []
    assert wiring["honcho"].peers == []      # no peer is ever created for an unmapped sender


async def test_read_peer_card_returns_empty_when_honcho_is_down(wiring):
    wiring["honcho"].fail = True
    assert await _call(wiring["tools"]["read_peer_card"], sender_id="+852111") == []


async def test_send_reply_delegates_to_the_deliverer(wiring):
    assert await _call(wiring["tools"]["send_reply"], conversation_id="g1",
                       conversation_type="group", text="hello") is True
    assert wiring["sink"].texts == ["hello"]
    assert wiring["deliverer"].delivered is True


async def test_send_reply_is_the_only_tool_that_can_send(wiring):
    await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40)
    await _call(wiring["tools"]["read_peer_card"], sender_id="+852111")
    assert wiring["sink"].sent == []


async def test_a_tool_never_raises_into_the_agent_loop(wiring, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(wiring["store"], "recent", boom)
    assert await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40) == []


async def _call(tool, **kwargs):
    """Invoke the underlying function of a @tool-decorated closure."""
    return await tool._tool_func(**kwargs)
