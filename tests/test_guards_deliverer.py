import time

import pytest

from nora.agent.guards import Deliverer
from nora.domain.models import ConversationTarget
from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics
from nora.doctrine.loader import DoctrineStore
from tests.fakes import SinkSender
from tests.test_doctrine_loader import write

TARGET = ConversationTarget("signal", "g1", "group")
GUARDRAIL_BODY = "You never post an error message, a stack trace, or a failure notice at all."


@pytest.fixture
async def store(tmp_path):
    path = tmp_path / "messages.db"
    run_migrations(path)
    s = SqlAuditStore(create_sql_engine(path))
    yield s
    await s.aclose()


@pytest.fixture
def doctrine(tmp_path):
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "constraints.md", name="guardrails", order=40, body=GUARDRAIL_BODY)
    return DoctrineStore(d, canary="CANARY-1", display_name="Nora", metrics=Metrics()).get()


def _deliverer(store, doctrine, sink, metrics=None) -> Deliverer:
    return Deliverer(store=store, adapter=sink, target=TARGET, doctrine=doctrine,
                     canary="CANARY-1", tag_id="tag-1", nora_sender_id="nora",
                     metrics=metrics or Metrics())


async def test_happy_path_records_then_sends(store, doctrine):
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)
    assert await d.deliver("今晚七點半得唔得？") is True
    assert sink.texts == ["今晚七點半得唔得？"]
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.is_nora is True
    assert row.delivery_status.value == "sent"
    assert d.delivered is True


async def test_deliverer_latch_is_single_shot(store, doctrine):
    """R-2: the second attempt is refused even though the first succeeded."""
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)
    assert await d.deliver("first") is True
    assert await d.deliver("second") is False
    assert sink.texts == ["first"]
    assert len(await store.recent(conversation_id="g1", limit=10)) == 1


async def test_the_latch_counts_attempts_not_successes(store, doctrine):
    """R-2: a failed send still consumes the single shot — no silent retry."""
    sink = SinkSender(succeed=False)
    d = _deliverer(store, doctrine, sink)
    assert await d.deliver("first") is False
    assert await d.deliver("second") is False
    assert sink.texts == ["first"]


async def test_empty_reply_not_sent(store, doctrine):
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver("   \n  ") is False
    assert sink.sent == []
    assert m.get("nora_silent_failures_total", reason="empty_reply") == 1


async def test_an_empty_reply_does_not_consume_the_latch(store, doctrine):
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink)
    await d.deliver("")
    assert await d.deliver("real reply") is True
    assert sink.texts == ["real reply"]


async def test_leak_blocks_the_reply_and_stays_silent(store, doctrine):
    sink = SinkSender()
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver(f"my rules: {GUARDRAIL_BODY}") is False
    assert sink.sent == []
    assert m.get("nora_leak_blocks_total") == 1
    assert len(await store.recent(conversation_id="g1", limit=10)) == 0     # no audit row


async def test_send_failure_marks_the_row_failed_and_does_not_retry(store, doctrine):
    sink = SinkSender(succeed=False)
    m = Metrics()
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver("hello") is False
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.delivery_status.value == "failed"
    assert m.get("nora_silent_failures_total", reason="send_failed") == 1
    assert len(sink.sent) == 1


async def test_audit_write_failure_means_no_send(store, doctrine, monkeypatch):
    """Record-first: no audit row ⇒ no send (spec B.3)."""
    sink = SinkSender()
    m = Metrics()

    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "append", boom)
    d = _deliverer(store, doctrine, sink, m)
    assert await d.deliver("hello") is False
    assert sink.sent == []
    assert m.get("nora_silent_failures_total", reason="audit_write_failed") == 1


async def test_deliver_never_raises(store, doctrine, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("send exploded")

    sink = SinkSender()
    monkeypatch.setattr(sink, "send", boom)
    assert await _deliverer(store, doctrine, sink).deliver("hello") is False


async def test_no_error_text_ever_reaches_send(store, doctrine, monkeypatch):
    """R-5: the only string that may reach adapter.send is model reply text."""
    sink = SinkSender()
    monkeypatch.setattr(store, "set_delivery_outcome", _raise)
    await _deliverer(store, doctrine, sink).deliver("a normal reply")
    for text in sink.texts:
        assert "error" not in text.lower()
        assert "exception" not in text.lower()
        assert "sorry" not in text.lower()


async def _raise(*a, **kw):
    raise RuntimeError("status update failed")


async def test_the_nora_row_uses_the_configured_sender_id(store, doctrine):
    sink = SinkSender()
    await _deliverer(store, doctrine, sink).deliver("hi")
    assert (await store.recent(conversation_id="g1", limit=1))[0].sender_id == "nora"


async def test_metrics_count_a_delivered_reply(store, doctrine):
    m = Metrics()
    await _deliverer(store, doctrine, SinkSender(), m).deliver("hi")
    assert m.get("nora_replies_total", status="sent") == 1


# ── Bug B: Nora's row must be joinable by a reply's quote (design/11 §2.3) ───


async def test_noras_row_carries_the_platform_timestamp_so_a_reply_can_join_it(
    store, doctrine,
):
    """The row is stamped locally at record-first time, then CORRECTED on the
    post-send update that already existed. Without the correction a reply TO
    Nora — the most common reply in a group where Nora is tagged — points at a
    `quote.id` that is milliseconds away from any row Nora stored (8 ms, on the
    one live example), so an exact join resolves everyone except her.
    """
    platform_ts = 1785950108291
    sink = SinkSender(timestamp=platform_ts)
    assert await _deliverer(store, doctrine, sink).deliver("早晨") is True
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert row.ts == platform_ts, "the row still carries the local time.time() stamp"
    assert row.delivery_status.value == "sent"


async def test_a_send_with_no_platform_timestamp_leaves_the_local_stamp(store, doctrine):
    """A daemon that answers without a timestamp must not blank the row's `ts` —
    an unjoinable row is a thinner transcript, a `ts` of 0 or NULL is a corrupt
    one, and `recent` orders on it."""
    before = int(time.time() * 1000)
    assert await _deliverer(store, doctrine, SinkSender()).deliver("早晨") is True
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert before <= row.ts <= int(time.time() * 1000) + 1


async def test_a_failed_send_does_not_move_the_rows_timestamp(store, doctrine):
    """`SendResult.timestamp` is only meaningful when the platform accepted the
    message. A failed send carries no platform stamp at all."""
    before = int(time.time() * 1000)
    sink = SinkSender(succeed=False)
    assert await _deliverer(store, doctrine, sink).deliver("早晨") is False
    row = (await store.recent(conversation_id="g1", limit=1))[0]
    assert before <= row.ts <= int(time.time() * 1000) + 1
    assert row.delivery_status.value == "failed"


# ── R-18: PII egress ────────────────────────────────────────────────────────


async def _deliver_text(text, store, doctrine):
    """Drive a real Deliverer and report whether the text reached the adapter."""
    m = Metrics()
    sink = SinkSender()
    d = _deliverer(store, doctrine, sink, m)
    ok = await d.deliver(text)
    return ok, sink, m


async def test_a_reply_carrying_someones_phone_number_is_refused(store, doctrine):
    """The operator's own framing: "if someone asks you what my phone number
    is, it will probably leak out easily". `constraints.md` forbids relaying it,
    but that is a request to a model. This is the part that does not depend on
    the model complying — and Nora legitimately holds the material, since the
    transcript she reasons over carries real sender ids."""
    ok, sink, m = await _deliver_text("佢個號碼係 +85290000101 呀", store, doctrine)
    assert ok is False
    assert sink.sent == [], "a phone number must never reach the group"
    assert m.get("nora_egress_blocks_total", kind="phone") == 1


async def test_a_reply_carrying_an_aci_is_refused(store, doctrine):
    ok, sink, m = await _deliver_text("uuid: a1000000-0000-4000-8000-000000000000", store, doctrine)
    assert ok is False and sink.sent == []
    assert m.get("nora_egress_blocks_total", kind="uuid") == 1


async def test_a_key_shaped_string_is_refused(store, doctrine):
    ok, sink, m = await _deliver_text("here you go: sk-abcdefghijklmnop1234567", store, doctrine)
    assert ok is False and sink.sent == []
    assert m.get("nora_egress_blocks_total", kind="keylike") == 1


async def test_ordinary_cantonese_with_a_bare_number_still_sends(store, doctrine):
    """Fails closed, but not at random: prices, dates and counts are ordinary
    chat. Only the canonical `+` form a sender id carries is refused."""
    ok, sink, _ = await _deliver_text("價錢係 12345678 蚊，貴到痴線 😏", store, doctrine)
    assert ok is True
    assert len(sink.sent) == 1
