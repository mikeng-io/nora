import asyncio
import logging

from nora.domain.models import DeliveryStatus
from nora.testing import ScriptedTurn, group_event


def _reply_script(text="今晚七點半得"):
    return [ScriptedTurn(tool_calls=[("send_reply", {"text": text})], final_text=text)]


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
        tool_calls=[("send_reply", {"text": "first"}),
                    ("send_reply", {"text": "second"})],
        final_text="first")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.sink.texts == ["first"]
    assert h.metrics.get("nora_guard_vetoes_total", rule="R-2") == 1


async def test_send_to_other_group_cannot_escape_the_tagged_conversation(harness_factory):
    """R-3 end-to-end, asserting the invariant rather than the mechanism.

    The rule is "a reply may only reach the conversation that produced the
    tag". It used to be enforced by VETOING a mismatched `conversation_id`
    argument; DEC-21 made it hold by construction instead (`send_reply` calls
    `deliverer.deliver(text)` and `Deliverer` holds its target bound from
    construction), and DEC-24 then deleted the argument from the schema
    entirely. That is strictly stronger at each step: the original check could
    be satisfied by a model echoing the right string, and now there is no
    string to echo.

    It also had to change. Vetoing meant a model that cannot reproduce a base64
    group id verbatim could never reply at all — on the first real tag,
    `deepseek-v4-flash` supplied a wrong id three times and the turn died
    `no_send_call`.

    So the probe is now a model emitting an OUT-OF-SCHEMA `conversation_id`,
    which is the only form the attempt can still take. Strands drops it during
    validation, the reply goes to the tagged conversation anyway, and the
    attempt is counted — the observation DEC-24 deliberately keeps alive so
    `nora_tool_scope_violations_total` is not a counter pinned at zero.
    """
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"conversation_id": "gEVIL", "text": "leak"})],
        final_text="")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert len(h.sink.sent) == 1, "the reply must still be delivered"
    target = h.sink.sent[0][0]
    assert getattr(target, "conversation_id", target) != "gEVIL", (
        "a model-supplied conversation must never become the send target"
    )
    assert h.sink.texts == ["leak"], (
        "the out-of-schema argument must be dropped, not turned into a TypeError"
    )
    assert h.metrics.get("nora_tool_scope_violations_total", tool="send_reply") == 1


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
    assert h.last_limits == {"turns": 8, "output_tokens": 4000, "total_tokens": 24000}


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
