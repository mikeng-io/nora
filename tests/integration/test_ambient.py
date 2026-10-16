import asyncio

from nora.testing import ScriptedTurn, group_event


async def test_every_message_is_appended_to_the_audit(harness):
    for i in range(10):
        await harness.handler.on_event(group_event(body=f"m{i}"))
    await harness.drain()
    assert len(await harness.store.recent(conversation_id="g1", limit=100)) == 10


async def test_ambient_reaches_the_agent_only_through_participation_gating(harness):
    """R-1, clause 2: ambient traffic reaches no agent, except through the participation
    occasion.

    An untagged message in an OPERATOR-OPTED-IN room may reach the agent, once per
    quiescence-closed window, through `FirePath.speak`. Ordinary ambient traffic, in rooms
    with no relevance_gate opt-in, constructs ZERO agents and sends nothing, and this test
    pins exactly that. The exception's own gating is pinned beside it, below and in
    `tests/integration/test_silent_failures.py`'s participation rows.
    """
    for i in range(10):
        await harness.handler.on_event(group_event(body=f"m{i}"))
    await harness.drain()
    assert harness.agents_constructed == 0
    assert harness.sink.sent == []


async def test_an_opted_in_rooms_closed_window_is_the_only_ambient_exception(harness_factory):
    """The revised clause's exception, pinned end to end: an untagged message
    in a room the operator opted in (`relevance_gate = true` in policy.toml)
    does NOT construct an agent when it arrives — it is only NOTED. The one
    evaluation runs when the quiescence window closes, through the fire path,
    exactly once per window; this determination declined the floor, so the
    room heard nothing."""
    clock = [1_000.0]
    h = await harness_factory(
        participation_script=[ScriptedTurn(final_text="")],
        participation_clock=lambda: clock[0],
    )
    await h.handler.on_event(group_event(body="anyone seen the timetable?"))
    await h.drain()

    assert h.agents_constructed == 0          # noted, not yet evaluated
    assert h.sink.sent == []

    clock[0] += 11                            # settle_seconds = 10: window closes
    assert await h.participation_trigger.close_due_windows() == 1
    assert h.agents_constructed == 1          # the ONE evaluation for the window
    assert h.sink.sent == []                  # which declined the floor

    clock[0] += 120                           # never twice for the same window
    assert await h.participation_trigger.close_due_windows() == 0
    assert h.agents_constructed == 1


async def test_mapped_senders_are_fed_to_honcho(harness):
    await harness.handler.on_event(group_event(body="hi", sender="+852111"))
    await harness.drain()
    assert harness.honcho.fed == [("miles", "hi", "g1")]


async def test_unmapped_senders_are_never_fed(harness):
    await harness.handler.on_event(group_event(body="hi", sender="+852999"))
    await harness.drain()
    assert harness.honcho.fed == []
    assert harness.honcho.peers == []
    assert len(await harness.store.recent(conversation_id="g1", limit=10)) == 1  # still audited


async def test_a_dm_event_is_dropped_entirely(harness):
    event = group_event(body="hi")
    event["params"]["envelope"]["dataMessage"]["groupInfo"] = None
    await harness.handler.on_event(event)
    await harness.drain()
    assert await harness.store.recent(conversation_id="g1", limit=10) == []


async def test_self_tag_ignored(harness):
    """R-12: Nora's own account can never fire the trigger."""
    await harness.handler.on_event(group_event(body="nora.000 hello", sender=harness.own_account))
    await harness.drain()
    assert harness.agents_constructed == 0
    assert harness.sink.sent == []


async def test_a_db_failure_drops_the_message_not_the_listener(harness, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(harness.store, "append", boom)
    await harness.handler.on_event(group_event(body="hi"))     # must not raise
    await harness.drain()
    assert harness.metrics.get("nora_errors_total", stage="ambient") == 1


async def test_a_honcho_failure_does_not_block_ingest(harness):
    harness.honcho.fail = True
    await harness.handler.on_event(group_event(body="hi"))
    await harness.drain()
    assert len(await harness.store.recent(conversation_id="g1", limit=10)) == 1


async def test_ingest_does_not_wait_on_a_slow_honcho(harness):
    """Ingest SPAWNS the memory feed; it never awaits it. Proven on the condition, not the
    clock: the fake feed blocks on an event nobody sets until after `on_event` has
    returned, so an ingest that awaited the feed would never return and `wait_for` would
    time out. A wall-clock bound would fail under full-suite load while passing alone."""
    entered = False
    release = asyncio.Event()

    async def slow_feed(peer_id, text, conversation_id=None, metadata=None):
        """PRODUCTION'S SIGNATURE (#74). It was `(peer_id, text)` while
        `memory_feed` has called `feed(peer_id=, text=, conversation_id=,
        metadata=)` since 2026-08-07 — so the fake raised `TypeError`
        immediately, inside the spawned task, and the elapsed-time assertion
        below was satisfied by a call that never slept. The test passed while
        proving the opposite of its name, and turned red only when the
        background task's log landed inside this test's capture.
        """
        nonlocal entered
        entered = True
        await release.wait()
        return True

    harness.honcho.feed = slow_feed
    # An ingest that awaited the feed would block on `release` forever; the
    # timeout is a hang guard, not a latency bound.
    await asyncio.wait_for(harness.handler.on_event(group_event(body="hi")), timeout=10)
    # THE POSITIVE CONTROL: with a wrong signature the fake raises in microseconds, so
    # returning promptly would be true of a call that never blocked and the test would
    # measure nothing. Asserting the fake was actually entered is what makes the return a
    # statement about the SPAWN rather than about an exception.
    #
    # After one loop tick, not before: the feed is SPAWNED, so it has not started when
    # `on_event` returns.
    await asyncio.sleep(0)
    assert entered, "the slow feed must have been reached, or the return proves nothing"
    release.set()


async def test_debounce_window(harness_factory):
    """R-9: a second tag inside the window is dropped before any agent work."""
    harness = await harness_factory(script=[ScriptedTurn()])
    await harness.handler.on_event(group_event(body="nora.000 one"))
    await harness.drain()
    await harness.handler.on_event(group_event(body="nora.000 two"))
    await harness.drain()
    assert harness.agents_constructed == 1
    assert harness.metrics.get("nora_tags_debounced_total") == 1


async def test_debounce_is_independent_per_conversation(harness_factory):
    harness = await harness_factory(script=[ScriptedTurn()])
    await harness.handler.on_event(group_event(body="nora.000 one", conv="g1"))
    await harness.handler.on_event(group_event(body="nora.000 one", conv="g2"))
    await harness.drain()
    assert harness.agents_constructed == 2


async def test_message_counter_is_labelled_by_platform(harness):
    await harness.handler.on_event(group_event(body="hi"))
    await harness.drain()
    assert harness.metrics.get("nora_messages_total", platform="signal") == 1
