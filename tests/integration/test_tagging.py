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
    # DEC-36, end to end: every text Honcho was handed for this tag is the
    # TAGGER's, under the tagger's peer. Nora's reply — which the group
    # demonstrably received, one assertion above — is in none of them.
    assert h.honcho.fed == [("miles", "nora.000 幾點食飯"), ("miles", "nora.000 幾點食飯")]
    assert not any("今晚七點半得" in text for _, text in h.honcho.fed)


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
    assert h.last_limits == {"turns": 8, "output_tokens": 12000, "total_tokens": 64000}
    # These three reach the SDK as one dict; `tests/test_turn_budget.py` owns
    # whether the NUMBERS are survivable (R-24), which is a property of the
    # config files rather than of this wiring.


async def test_unmapped_sender_tags_produces_a_reply_with_no_honcho_side_effects(
        harness_factory):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?", sender="+852999", name="Stranger"))
    await h.drain()
    assert len(h.sink.sent) == 1
    assert h.honcho.fed == []
    assert h.honcho.peers == []


async def test_the_tag_path_feed_happens_exactly_once_per_delivered_reply(harness_factory):
    """TWO feeds, not one, and the second is the ambient one.

    `nora.ingest.stages.memory_feed` runs for EVERY message before the engage
    gate, so it has already fed this exact body under this exact peer by the
    time `feedback.run` fires. Since DEC-36 removed the `[Nora]:` half, the two
    writes are byte-identical and the tag path's is a pure duplicate — a
    RECORDED redundancy, not an accident (DEC-36 (4)); it costs the deriver a
    second pass over one message and is left in place because removing it
    deletes a pipeline stage that session-per-conversation is expected to want
    back. Pinned as an equality so the day somebody removes it, this test says
    so rather than silently passing.
    """
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.honcho.fed == [("miles", "nora.000 ?"), ("miles", "nora.000 ?")]


async def test_an_undelivered_tag_adds_no_second_feed(harness_factory):
    """Only the ambient write survives: `feedback.run` is reached from behind
    `deliver.check_outcome`, so a turn that said nothing writes once, not twice."""
    h = await harness_factory(script=[ScriptedTurn(final_text="silent")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.honcho.fed == [("miles", "nora.000 ?")]


async def test_latency_is_recorded(harness_factory):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.metrics.observations("nora_tag_latency_seconds")) == 1


async def test_doctrine_sha256_is_logged_for_the_tag(harness_factory, caplog):
    caplog.set_level(logging.INFO, logger="nora.engagement.pipeline")
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert any("doctrine_sha256" in r.getMessage() for r in caplog.records)


async def test_a_vetoed_send_reply_leaves_the_budget_for_the_correction(harness_factory):
    """DEC-33. The recovery from a recoverable mistake must not be what gets blocked.

    The model calls `send_reply("")`, R-4 refuses it, and the model corrects
    itself in the same turn. `NATIVE_TOOLS["send_reply"].budget` is 1, so if a
    cancelled call is recorded the correction meets R-7's budget check and is
    told "you already sent your reply" — and the group hears nothing at all,
    which under R-5 is indistinguishable from a quiet Nora.

    This is an END-TO-END probe on purpose: the divergence that hid this bug
    for the life of the deployment was in `nora.testing.FakeAgentRunner`, which
    skipped the guard's after-tool hook on a veto and so could not reproduce it
    at any layer. Revert `_after_tool`'s `cancel_message` check and this goes
    red; revert the fake's hook dispatch and it goes green again while the live
    bug is still there.
    """
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"text": ""}),
                    ("send_reply", {"text": "今晚七點"})],
        final_text="今晚七點")])
    await h.handler.on_event(group_event(body="nora.000 幾點?"))
    await h.drain()

    assert h.sink.texts == ["今晚七點"]
    assert h.metrics.get("nora_guard_vetoes_total", rule="R-4") == 1
    # The correction was never refused: no R-2 veto, and no silent failure.
    assert h.metrics.get("nora_guard_vetoes_total", rule="R-2") == 0
    assert h.metrics.get("nora_silent_failures_total", reason="no_send_call") == 0
