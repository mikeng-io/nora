import pytest

from nora.agent.guards import Deliverer
from nora.agent.tools import build_tools
from nora.models import ConversationTarget, NormalizedMessage
from nora.persona.loader import PersonaStore
from nora.persons import load_persons
from nora.store import db
from tests.fakes import FakeHoncho, SinkSender
from tests.test_persona_loader import write

TARGET = ConversationTarget("signal", "g1", "group")


@pytest.fixture
async def wiring(tmp_path):
    dbp = tmp_path / "messages.db"
    await db.init(dbp)
    for i in range(3):
        await db.append(dbp, NormalizedMessage("signal", "g1", "group", "+852111",
                                               "Miles", i, f"m{i}", {}))
    persons_file = tmp_path / "persons.yaml"
    persons_file.write_text(
        "persons:\n  miles:\n    name: Miles\n    platforms:\n      signal: \"+852111\"\n"
    )
    pdir = tmp_path / "persona"
    pdir.mkdir()
    write(pdir, "soul.md", name="soul", order=10, body="You are Nora.")
    persona = PersonaStore(pdir, canary="C").get()
    sink = SinkSender()
    honcho = FakeHoncho(cards={"miles": ["likes espresso"]})
    deliverer = Deliverer(db_path=dbp, adapter=sink, target=TARGET, persona=persona,
                          canary="C", tag_id="t1", nora_sender_id="nora")
    tools = build_tools(db_path=dbp, conversation_id="g1", conversation_type="group",
                        persons=load_persons(persons_file), honcho=honcho,
                        deliverer=deliverer, context_window=40)
    return {"tools": {t.tool_name: t for t in tools}, "sink": sink,
            "honcho": honcho, "deliverer": deliverer, "dbp": dbp}


def test_exactly_three_tools_are_built(wiring):
    assert set(wiring["tools"]) == {"read_buffer", "read_peer_card", "send_reply"}


def test_feed_honcho_is_not_a_tool(wiring):
    assert "feed_honcho" not in wiring["tools"]


def test_every_tool_has_a_model_facing_docstring(wiring):
    for name, tool in wiring["tools"].items():
        doc = tool.__doc__ or ""
        assert len(doc.strip()) > 40, f"{name} needs a docstring written for the model"


async def test_read_buffer_returns_rows_oldest_first(wiring):
    rows = await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40)
    assert [r["body"] for r in rows] == ["m0", "m1", "m2"]


async def test_read_buffer_returns_only_model_relevant_fields(wiring):
    rows = await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40)
    assert set(rows[0]) == {"sender", "body", "ts"}    # no raw_json, no ids


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

    monkeypatch.setattr(db, "recent", boom)
    assert await _call(wiring["tools"]["read_buffer"], conversation_id="g1", limit=40) == []


async def _call(tool, **kwargs):
    """Invoke the underlying function of a @tool-decorated closure."""
    return await tool._tool_func(**kwargs)
