"""Durable room commands retain exclusive conversation slots."""
import asyncio

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagementRequest
from nora.domain.tag_trace import TagTrace
from nora.testing import FakeAgentRunner
from tests.integration.test_bridge_queue import (
    CONV,
    PLATFORM,
    _claim_kind,
    _envelope,
    _managed_room,
    _register_event,
    _runner,
)
from tests.integration.test_bridge_queue import bridge as bridge


async def test_a_durable_room_command_dispatches_exactly_one_queued_turn(
        bridge, redis_client, harness_factory):
    """Exactly-once on the real seam: the worker's `finally` is the ONE
    release for the slot it `try_acquire`d. A stage release on top of it
    pops a second queued turn and runs two follow-ups overlapped — the
    double-release the round-two review rejected. The first queued turn is
    parked mid-invoke so an overlap would be visible, not just racy."""
    parked, release_first = asyncio.Event(), asyncio.Event()

    class _GatedEcho(FakeAgentRunner):
        """Replies `reply-to-<body>` through the real send_reply per tag —
        distinct per invoke — and parks the FIRST queued tag mid-turn so a
        second dispatched under it would be observed."""
        async def invoke(self, *, tools, guard, ctx, **_kwargs):
            if ctx.message.body == "nora first":
                parked.set()
                await release_first.wait()
            tools_by_name = {t.tool_name: t for t in tools}
            args = {"text": f"reply-to-{ctx.message.body}"}
            if guard.evaluate("send_reply", args) is None:
                result = tools_by_name["send_reply"](**args)
                if hasattr(result, "__await__"):
                    await result
                guard.record("send_reply")
            return "end_turn", "", TagTrace(
                tag_id=ctx.tag_id,
                conversation_id=ctx.message.conversation_id,
                messages=(), transcript=(), outcome="send_reply",
                created_at=0)

    management, _bindings = await _managed_room()
    harness = await harness_factory(
        room_commands=management, script=_GatedEcho(),
        # Two queued tags arrive back to back — the debounce window would
        # shed the second; this test is about the slot, not the limiter.
        debounce=0)
    store, _, stream = bridge
    env = _envelope("!!!member register", 1014,
                    mentions=frozenset({"+00000000009"}))
    await _register_event(store, redis_client, stream, env)
    runner = _runner(store, redis_client, harness,
                     room_command=management.is_command)
    await runner._execute(await store.claim_next(owner="w-test", lease_seconds=60))
    turn = await _claim_kind(store, "turn")
    assert turn is not None and turn.payload["occasion"] == "room_command"

    slots = harness._engagement._slots
    key = (PLATFORM, CONV)
    real_durable = harness._engagement.run_durable
    try:
        async def enqueue_then_run(*a, **kw):
            # The slot is already held by _dispatch here — these tags take
            # the real QUEUED path and wait as factories.
            for tag_id, body in (("q1", "nora first"), ("q2", "nora second")):
                await harness._engagement.run(EngagementRequest(
                    message=NormalizedMessage(
                        platform=PLATFORM, conversation_id=CONV,
                        conversation_type="group", sender_id="+00000000002",
                        sender_name="Sender", timestamp=1015, body=body,
                        raw={}, message_id=f"q-{tag_id}"),
                    decision=EngageDecision(engage=True, reason="trigger_text"),
                    tag_id=tag_id))
            return await real_durable(*a, **kw)

        harness._engagement.run_durable = enqueue_then_run
        execute = asyncio.create_task(runner._execute(turn))
        await asyncio.wait_for(parked.wait(), timeout=15)
        # The first follow-up is mid-turn — still in flight — and the
        # second is STILL QUEUED: the worker's one release dispatched
        # exactly one. A double release would already have started it.
        assert slots.depth(key) == 1
        assert "reply-to-nora second" not in harness.sink.texts
        release_first.set()
        await execute
        await harness.drain()
    finally:
        harness._engagement.run_durable = real_durable
    assert [text for _, text in harness.sink.sent] == [
        "registered — they're a member of this room now",
        "reply-to-nora first", "reply-to-nora second"]
    assert not slots.held(key) and slots.depth(key) == 0

