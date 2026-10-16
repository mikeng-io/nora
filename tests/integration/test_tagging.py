import asyncio
import logging

from nora.agent.registry import ToolRegistry
from nora.domain.models import DeliveryStatus
from nora.domain.tag_trace import VALID_OUTCOMES
from nora.testing import ScriptedTurn, group_event

# The conversation every `group_event` here lands in, as the slots key
# `(platform, conversation_id)` — R-10's lock and R-30's queue are both keyed
# by it (`nora/engagement/slots.py`).
GROUP = ("signal", "g1")


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
    # The ambient path feeds the tagger's message under the tagger's peer; the
    # feedback stage feeds NORA's reply under the Nora aiPeer. Both in the g1
    # session, each under its own speaker — DEC-36 at the peer level: her reply
    # is fed, but never under a human's peer.
    assert h.honcho.fed == [("miles", "nora.000 幾點食飯", "g1"),
                            ("nora", "今晚七點半得", "g1")]
    assert not any(peer == "miles" and "今晚七點半得" in text
                   for peer, text, _ in h.honcho.fed)


async def test_a_completed_tag_leaves_a_retrievable_tag_trace(harness_factory):
    """Retention is real, end to end — a driven tag
    through the FULL pipeline (real `SqlTagTraceStore`, real Postgres) leaves
    exactly one row `recent()` can read back, and it carries a REAL outcome —
    not `LiveAgentRunner`/`FakeAgentRunner`'s `"send_reply"` placeholder left
    uncorrected. This turn actually calls `send_reply` and delivers, so
    `"send_reply"` is also the CORRECT answer here — the placeholder and the
    real value coincide on the happy path, which is exactly why the
    outcome-mapping unit tests in `tests/unit/test_engagement_pipeline.py`
    cover the paths where they diverge (recovered/silent/vetoed/timeout/
    llm_error)."""
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 幾點食飯"))
    await h.drain()

    assert h.sink.texts == ["今晚七點半得"]
    got = await h.tag_trace_store.recent(conversation_id="g1", limit=10)
    assert len(got) == 1
    assert got[0].conversation_id == "g1"
    assert got[0].outcome in VALID_OUTCOMES
    assert got[0].outcome == "send_reply"


async def test_context_is_collected_before_the_model_runs(harness_factory):
    h = await harness_factory(script=_reply_script())
    for i in range(3):
        await h.handler.on_event(group_event(body=f"m{i}", ts=i))
    await h.drain()
    await h.handler.on_event(group_event(body="nora.000 ?", ts=9))
    await h.drain()
    assert h.last_prefill is not None
    assert "m0" in h.last_prefill[1]["content"][0]["text"]


async def test_a_model_that_writes_nothing_at_all_means_strict_silence(harness_factory):
    """DEC-93 narrows R-4 deliberately: the rule is «nothing reaches the group
    unless she WROTE it».

    On the DEC-84 route `tool_choice="required"` is accepted and ignored, so
    text outside `send_reply` no longer means the model chose not to speak, and
    a complete group-addressed reply written as prose would otherwise reach
    nobody. `test_a_reply_written_without_calling_send_reply_is_RECOVERED` pins
    that side. What this test exists for: a turn that produced neither a tool
    call nor any text still says nothing. There is no path by which Nora emits
    words she did not author."""
    h = await harness_factory(script=[ScriptedTurn(final_text="")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.sink.sent == []
    assert h.metrics.get("nora_silent_failures_total", reason="no_send_call") == 1
    assert h.metrics.get("nora_recovered_replies_total") == 0
    assert [r for r in await h.store.recent(conversation_id="g1", limit=10) if r.is_nora] == []


async def test_send_reply_is_capped_at_the_budget_end_to_end(harness_factory):
    """Multi-message end-to-end (DEC-42): every send up to the cap reaches the
    group; the one past it is refused (one R-2 veto).

    The cap is read from the registry, not written here — DEC-10 keeps the
    number with the tool and DEC-32 requires exactly one copy of it. Asserting
    a literal would go red for the wrong cause whenever the cap changes: the
    behaviour under test is «the (cap+1)th is refused», which is true at any
    cap."""
    cap = ToolRegistry().budgets()["send_reply"]
    texts = [f"msg{i}" for i in range(cap + 1)]
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"text": t}) for t in texts],
        final_text=texts[0])])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.sink.texts == texts[:cap]          # the last one never reached the group
    assert h.metrics.get("nora_guard_vetoes_total", rule="R-2") == 1


async def test_send_to_other_group_cannot_escape_the_tagged_conversation(harness_factory):
    """R-3 end-to-end, asserting the invariant rather than the mechanism.

    The rule is "a reply may only reach the conversation that produced the
    tag". It holds by construction (DEC-21): `send_reply` calls
    `deliverer.deliver(text)` and `Deliverer` holds its target bound from
    construction, and the schema has no `conversation_id` argument at all
    (DEC-24). A veto on a mismatched argument could be satisfied by a model
    echoing the right string, and a model that cannot reproduce a base64 group
    id verbatim could never reply; with no argument there is no string to echo.

    So the probe is a model emitting an OUT-OF-SCHEMA `conversation_id`, the
    only form the attempt can take. Strands drops it during
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
    assert h.handler.slots.held(GROUP) is False


async def test_lock_released_on_timeout(harness_factory):
    h = await harness_factory(script=_reply_script(), delay=5.0, invocation_timeout=0.2)
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.handler.slots.held(GROUP) is False
    assert h.sink.sent == []
    assert h.metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1


async def test_concurrent_tags_same_conversation(harness_factory):
    """R-10 + R-29: at most ONE turn runs at a time for a conversation (the
    lock), but the second tag is QUEUED and answered after the first —
    not dropped. Three people tagging at once each get a reply."""
    h = await harness_factory(script=_reply_script(), delay=0.3, debounce=0)
    await h.handler.on_event(group_event(body="nora.000 one"))
    await asyncio.sleep(0.05)
    # First tag is in flight; the second arrives while it runs.
    assert h.handler.slots.held(GROUP) is True
    await h.handler.on_event(group_event(body="nora.000 two"))
    # R-10 still holds: exactly one in-flight turn — the second is QUEUED behind
    # it, not running concurrently. (Only `g1` is ever tagged here. It is not
    # also asserted that the second tag is ALREADY queued:
    # `Dispatcher.on_message` SPAWNS the engagement run, so at this instant it
    # may not have reached admission yet — the drained assertions below are what
    # prove it queued.)
    assert h.handler.slots.held(GROUP) is True

    await h.drain()

    # R-29: both replies went out, none dropped, the queue drained empty.
    assert len(h.sink.sent) == 2
    assert h.handler.slots.held(GROUP) is False
    assert len(h.handler.slots) == 0
    assert h.metrics.get("nora_tags_queued_total") == 1
    assert h.metrics.get("nora_tags_dropped_total", reason="in_flight") == 0


async def test_limits_passed_through(harness_factory):
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.last_limits == {"turns": 20, "output_tokens": 12000, "total_tokens": 1024000}
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
    time `feedback.run` fires. The two writes are byte-identical (DEC-36
    removed the `[Nora]:` half) and the tag path's is a pure duplicate — a
    RECORDED redundancy, not an accident (DEC-36 (4)); it costs the deriver a
    second pass over one message and is left in place because removing it
    deletes a pipeline stage that session-per-conversation is expected to want
    back. Pinned as an equality so the day somebody removes it, this test says
    so rather than silently passing.
    """
    h = await harness_factory(script=_reply_script())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.honcho.fed == [("miles", "nora.000 ?", "g1"), ("nora", "今晚七點半得", "g1")]


async def test_an_undelivered_tag_adds_no_second_feed(harness_factory):
    """Only the ambient write survives: `feedback.run` is reached from behind
    `deliver.check_outcome`, so a turn that said nothing writes once, not twice."""
    h = await harness_factory(script=[ScriptedTurn(final_text="")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert h.honcho.fed == [("miles", "nora.000 ?", "g1")]


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

    This is an END-TO-END probe on purpose: `nora.testing.FakeAgentRunner` must
    dispatch the guard's after-tool hook on a veto, or the bug cannot be
    reproduced at any layer. Revert `_after_tool`'s `cancel_message` check and this goes
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
