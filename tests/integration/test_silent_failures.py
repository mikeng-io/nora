import pytest

from nora.testing import ScriptedTurn, group_event

FORBIDDEN = ("error", "sorry", "exception", "traceback", "failed", "unavailable",
             "system", "config", "api key")


def _script(text="reply"):
    return [ScriptedTurn(tool_calls=[("send_reply", {"text": text})], final_text=text)]


@pytest.mark.parametrize(
    "fault,expect_send,reason",
    [
        ("llm_error", False, "llm_error"),
        ("llm_timeout", False, "invocation_timeout"),
        ("empty_reply", False, "empty_reply"),
        ("db_append_fails", False, "audit_write_failed"),
        ("leak_guard_trips", False, "persona_leak"),
        ("no_send_call", False, "no_send_call"),
        ("honcho_down", True, None),
        ("persona_reload_fails", True, None),
        ("context_read_fails", True, None),
        ("send_fails", True, "send_failed"),
    ],
)
async def test_silent_failure_matrix(harness_factory, fault, expect_send, reason):
    """R-5 / spec B.3: the group never sees an error, whatever breaks."""
    h = await harness_factory(script=_script(), fault=fault)
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert bool(h.sink.sent) is expect_send, fault
    for text in h.sink.texts:
        low = text.lower()
        for word in FORBIDDEN:
            assert word not in low, f"{fault}: leaked {word!r} to the group"
    if reason:
        assert h.metrics.get("nora_silent_failures_total", reason=reason) >= 1, fault


async def test_no_audit_row_when_nothing_was_delivered(harness_factory):
    h = await harness_factory(script=_script(), fault="llm_error")
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert [r for r in await h.store.recent(conversation_id="g1", limit=10) if r.is_nora] == []


async def test_a_reply_still_happens_with_a_thinner_context(harness_factory):
    h = await harness_factory(script=_script(), fault="context_read_fails")
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.sink.sent) == 1
    assert h.metrics.get("nora_errors_total", stage="context") == 1


async def test_a_reply_still_happens_with_no_peer_card(harness_factory):
    h = await harness_factory(script=_script(), fault="honcho_down")
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.sink.sent) == 1
    # A reply happening is not, on its own, proof Honcho was actually down: Honcho is
    # never load-bearing for delivery either way. `fed == []` is the observable half —
    # FakeHoncho.feed() short-circuits before appending when `fail` is set, and a
    # delivered reply always feeds the exchange when Honcho is reachable (see
    # test_tagging.py::test_the_exchange_feed_happens_exactly_once_per_delivered_reply)
    # — so this is what actually distinguishes "the fault fired" from "it didn't".
    assert h.honcho.fed == []


async def test_a_broken_persona_edit_uses_the_last_good_persona(harness_factory):
    h = await harness_factory(script=_script(), fault="persona_reload_fails")
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.sink.sent) == 1
    assert h.persona_store.reload_failures == 1
