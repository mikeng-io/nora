import pytest

from nora.agent.guards import Deliverer
from nora.models import ConversationTarget
from nora.obs.metrics import Metrics
from nora.persona.loader import PersonaStore
from nora.store import db
from tests.fakes import SinkSender
from tests.test_persona_loader import write

TARGET = ConversationTarget("signal", "g1", "group")
GUARDRAIL_BODY = "You never post an error message, a stack trace, or a failure notice at all."


@pytest.fixture
async def dbp(tmp_path):
    path = tmp_path / "messages.db"
    await db.init(path)
    return path


@pytest.fixture
def persona(tmp_path):
    d = tmp_path / "persona"
    d.mkdir()
    write(d, "guardrails.md", name="guardrails", order=40, body=GUARDRAIL_BODY)
    return PersonaStore(d, canary="CANARY-1", display_name="Nora").get()


def _deliverer(dbp, persona, sink, metrics=None) -> Deliverer:
    return Deliverer(db_path=dbp, adapter=sink, target=TARGET, persona=persona,
                     canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
                     metrics=metrics or Metrics())


async def test_happy_path_records_then_sends(dbp, persona):
    sink = SinkSender()
    d = _deliverer(dbp, persona, sink)
    assert await d.deliver("今晚七點半得唔得？") is True
    assert sink.texts == ["今晚七點半得唔得？"]
    row = (await db.recent(dbp, "g1", 1))[0]
    assert row["is_nora"] == 1
    assert row["delivery_status"] == "sent"
    assert d.delivered is True


async def test_deliverer_latch_is_single_shot(dbp, persona):
    """R-2: the second attempt is refused even though the first succeeded."""
    sink = SinkSender()
    d = _deliverer(dbp, persona, sink)
    assert await d.deliver("first") is True
    assert await d.deliver("second") is False
    assert sink.texts == ["first"]
    assert len(await db.recent(dbp, "g1", 10)) == 1


async def test_the_latch_counts_attempts_not_successes(dbp, persona):
    """R-2: a failed send still consumes the single shot — no silent retry."""
    sink = SinkSender(succeed=False)
    d = _deliverer(dbp, persona, sink)
    assert await d.deliver("first") is False
    assert await d.deliver("second") is False
    assert sink.texts == ["first"]


async def test_empty_reply_not_sent(dbp, persona):
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(dbp, persona, sink, m)
    assert await d.deliver("   \n  ") is False
    assert sink.sent == []
    assert m.get("nora_silent_failures_total", reason="empty_reply") == 1


async def test_an_empty_reply_does_not_consume_the_latch(dbp, persona):
    sink = SinkSender()
    d = _deliverer(dbp, persona, sink)
    await d.deliver("")
    assert await d.deliver("real reply") is True
    assert sink.texts == ["real reply"]


async def test_leak_blocks_the_reply_and_stays_silent(dbp, persona):
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(dbp, persona, sink, m)
    assert await d.deliver(f"my rules: {GUARDRAIL_BODY}") is False
    assert sink.sent == []
    assert m.get("nora_leak_blocks_total") == 1
    assert len(await db.recent(dbp, "g1", 10)) == 0     # no audit row for a blocked reply


async def test_send_failure_marks_the_row_failed_and_does_not_retry(dbp, persona):
    sink = SinkSender(succeed=False)
    m = Metrics()
    d = _deliverer(dbp, persona, sink, m)
    assert await d.deliver("hello") is False
    row = (await db.recent(dbp, "g1", 1))[0]
    assert row["delivery_status"] == "failed"
    assert m.get("nora_silent_failures_total", reason="send_failed") == 1
    assert len(sink.sent) == 1


async def test_audit_write_failure_means_no_send(dbp, persona, monkeypatch):
    """Record-first: no audit row ⇒ no send (spec B.3)."""
    sink = SinkSender()
    m = Metrics()

    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "append", boom)
    d = _deliverer(dbp, persona, sink, m)
    assert await d.deliver("hello") is False
    assert sink.sent == []
    assert m.get("nora_silent_failures_total", reason="audit_write_failed") == 1


async def test_deliver_never_raises(dbp, persona, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("send exploded")

    sink = SinkSender()
    monkeypatch.setattr(sink, "send", boom)
    assert await _deliverer(dbp, persona, sink).deliver("hello") is False


async def test_no_error_text_ever_reaches_send(dbp, persona, monkeypatch):
    """R-5: the only string that may reach adapter.send is model reply text."""
    sink = SinkSender()
    monkeypatch.setattr(db, "set_delivery_status", _raise)
    await _deliverer(dbp, persona, sink).deliver("a normal reply")
    for text in sink.texts:
        assert "error" not in text.lower()
        assert "exception" not in text.lower()
        assert "sorry" not in text.lower()


async def _raise(*a, **kw):
    raise RuntimeError("status update failed")


async def test_the_nora_row_uses_the_configured_sender_id(dbp, persona):
    sink = SinkSender()
    await _deliverer(dbp, persona, sink).deliver("hi")
    assert (await db.recent(dbp, "g1", 1))[0]["sender_id"] == "nora"


async def test_metrics_count_a_delivered_reply(dbp, persona):
    m = Metrics()
    await _deliverer(dbp, persona, SinkSender(), m).deliver("hi")
    assert m.get("nora_replies_total", status="sent") == 1
