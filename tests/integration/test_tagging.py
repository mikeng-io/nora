import asyncio
import logging

from nora.domain.models import DeliveryStatus
from tests.integration.harness import ScriptedTurn, group_event


def _reply_script(text="今晚七點半得", conv="g1"):
    return [ScriptedTurn(tool_calls=[("send_reply", {"conversation_id": conv,
                                                     "conversation_type": "group",
                                                     "text": text})], final_text=text)]


async def test_happy_tag_produces_one_reply_one_row_one_feed(harness_factory, tmp_path):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 幾點食飯"))
    await h.drain()

    assert h.sink.texts == ["今晚七點半得"]
    nora_rows = [r for r in await h.store.recent(conversation_id="g1", limit=10) if r.is_nora]
    assert len(nora_rows) == 1
    assert nora_rows[0].delivery_status is DeliveryStatus.SENT
    exchange = [f for f in h.honcho.fed if "[Nora]" in f[1]]
    assert len(exchange) == 1
    assert exchange[0][0] == "miles"


async def test_context_is_collected_before_the_model_runs(harness_factory):
    h = await harness_factory(script=_reply_script())
    for i in range(3):
        await h.handler.on_event(group_event(body=f"m{i}", ts=i))
    await h.drain()
    await h.handler.on_event(group_event(body="nora.000 ?", ts=9))
    await h.drain()
    assert h.last_prefill is not None
    assert "m0" in h.last_prefill[1]["content"][0]["text"]


async def test_model_never_calls_send_reply_means_strict_silence(harness_factory):
    """Q5: final text without a send_reply call is dropped."""
    h = await harness_factory(script=[ScriptedTurn(final_text="I would have said this")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.sink.sent == []
    assert h.metrics.get("nora_silent_failures_total", reason="no_send_call") == 1
    assert [r for r in await h.store.recent(conversation_id="g1", limit=10) if r.is_nora] == []


async def test_second_send_reply_is_cancelled(harness_factory):
    """R-2 end-to-end: one send, one veto."""
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"conversation_id": "g1", "conversation_type": "group",
                                    "text": "first"}),
                    ("send_reply", {"conversation_id": "g1", "conversation_type": "group",
                                    "text": "second"})],
        final_text="first")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.sink.texts == ["first"]
    assert h.metrics.get("nora_guard_vetoes_total", rule="R-2") == 1


async def test_send_to_other_group_is_cancelled(harness_factory):
    """R-3 end-to-end."""
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"conversation_id": "gEVIL", "conversation_type": "group",
                                    "text": "leak"})], final_text="")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.sink.sent == []
    assert h.metrics.get("nora_guard_vetoes_total", rule="R-3") == 1


async def test_llm_error_stays_silent(harness_factory):
    h = await harness_factory(script=_reply_script(), raises=RuntimeError("omniroute down"))
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.sink.sent == []
    assert h.metrics.get("nora_silent_failures_total", reason="llm_error") == 1


async def test_lock_released_on_llm_error(harness_factory):
    """R-10: a failed tag must not wedge the conversation forever."""
    h = await harness_factory(script=_reply_script(), raises=RuntimeError("boom"))
    await h.handler.on_event(group_event(body="nora.000 one"))
    await h.drain()
    assert h.handler._in_flight == set()


async def test_lock_released_on_timeout(harness_factory):
    h = await harness_factory(script=_reply_script(), delay=5.0, invocation_timeout=0.2)
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.handler._in_flight == set()
    assert h.sink.sent == []
    assert h.metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1


async def test_concurrent_tags_same_conversation(harness_factory):
    """R-10: the second tag is dropped, not queued."""
    h = await harness_factory(script=_reply_script(), delay=0.3, debounce=0)
    await h.handler.on_event(group_event(body="nora.000 one"))
    await asyncio.sleep(0.05)
    await h.handler.on_event(group_event(body="nora.000 two"))
    await h.drain()
    assert len(h.sink.sent) == 1
    assert h.metrics.get("nora_tags_dropped_total", reason="in_flight") == 1


async def test_limits_passed_through(harness_factory):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.last_limits == {"turns": 4, "output_tokens": 400, "total_tokens": 24000}


async def test_unmapped_sender_tags_produces_a_reply_with_no_honcho_side_effects(
        harness_factory):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?", sender="+852999", name="Stranger"))
    await h.drain()
    assert len(h.sink.sent) == 1
    assert h.honcho.fed == []
    assert h.honcho.peers == []


async def test_the_exchange_feed_happens_exactly_once_per_delivered_reply(harness_factory):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len([f for f in h.honcho.fed if "[Nora]" in f[1]]) == 1


async def test_no_exchange_feed_when_nothing_was_delivered(harness_factory):
    h = await harness_factory(script=[ScriptedTurn(final_text="silent")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert [f for f in h.honcho.fed if "[Nora]" in f[1]] == []


async def test_latency_is_recorded(harness_factory):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.metrics.observations("nora_tag_latency_seconds")) == 1


async def test_persona_sha256_is_logged_for_the_tag(harness_factory, caplog):
    caplog.set_level(logging.INFO, logger="nora.engagement.pipeline")
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert any("persona_sha256" in r.getMessage() for r in caplog.records)
