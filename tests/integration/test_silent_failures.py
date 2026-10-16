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
        ("leak_guard_trips", False, "doctrine_leak"),
        ("no_send_call", False, "no_send_call"),
        ("honcho_down", True, None),
        ("doctrine_reload_fails", True, None),
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
    # FakeMemoryStore.feed() short-circuits before appending when `fail` is set, and a
    # delivered reply always feeds the exchange when Honcho is reachable (see
    # test_tagging.py::test_the_exchange_feed_happens_exactly_once_per_delivered_reply)
    # — so this is what actually distinguishes "the fault fired" from "it didn't".
    assert h.honcho.fed == []


async def test_a_broken_doctrine_edit_uses_the_last_good_doctrine(harness_factory):
    h = await harness_factory(script=_script(), fault="doctrine_reload_fails")
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.sink.sent) == 1
    assert h.doctrine_store.reload_failures == 1


# ---- R-5's participation row ----------------------------------------------
# For the participation audience `no_send_call` is the HEALTHY default, not a
# failure: the model was offered the floor and declined it. These drive the
# real ingest -> dispatcher -> trigger -> FirePath.speak chain with only the
# model scripted, and never sleep — the window is closed by hand against the
# injected clock.

async def test_participation_silence_is_healthy_not_a_silent_failure(harness_factory):
    clock = [1_000.0]
    h = await harness_factory(
        participation_script=[ScriptedTurn(final_text="")],   # declines the floor
        participation_clock=lambda: clock[0],
    )
    await h.handler.on_event(group_event(body="did anyone book the table?"))
    await h.handler.on_event(group_event(body="not yet"))
    await h.drain()

    # Noted, not answered: nothing sent, no agent until the window CLOSES.
    assert h.sink.sent == []
    assert h.agents_constructed == 0
    assert await h.participation_trigger.close_due_windows() == 0

    clock[0] += 11                                          # settle_seconds = 10
    assert await h.participation_trigger.close_due_windows() == 1
    assert h.sink.sent == []
    assert h.metrics.get("nora_participation_silence_total", platform="signal") == 1
    # The load-bearing part (docs/runbook.md): a quiet participation turn is
    # NEVER charged to the tag audience's silent-failure budget.
    assert h.metrics.get("nora_silent_failures_total", audience="participation") == 0
    assert h.metrics.get("nora_silent_failures_total", audience="tag") == 0


async def test_participation_speaks_through_the_fire_path_when_it_has_value(harness_factory):
    """The other half of may-speak, end to end: a determination with grounded
    value delivers through `deliver.build` — the send path R-1b pins — and
    moves the spoke counter, never the silence one."""
    clock = [1_000.0]
    h = await harness_factory(
        participation_script=_script("聽日三點，我頭先啱啱訂咗"),
        participation_clock=lambda: clock[0],
    )
    await h.handler.on_event(group_event(body="so are we meeting tomorrow?"))
    await h.drain()

    clock[0] += 11
    assert await h.participation_trigger.close_due_windows() == 1
    assert h.sink.texts == ["聽日三點，我頭先啱啱訂咗"]
    assert h.metrics.get("nora_participation_spoke_total", platform="signal") == 1
    assert h.metrics.get("nora_participation_silence_total") == 0


async def test_a_room_not_opted_in_never_reaches_the_trigger(harness_factory):
    """The policy surface, end to end: `participation = true` was written for
    g1 only, so the SAME messages in g2 are ordinary ambient traffic — recorded,
    never noted, and closing windows finds nothing."""
    clock = [1_000.0]
    h = await harness_factory(
        participation_script=[ScriptedTurn(final_text="")],
        participation_clock=lambda: clock[0],
    )
    await h.handler.on_event(group_event(body="hello g2", conv="g2"))
    await h.drain()

    clock[0] += 11
    assert await h.participation_trigger.close_due_windows() == 0
    assert len(h.participation_trigger) == 0
    assert h.agents_constructed == 0
    assert h.sink.sent == []
