import asyncio

from nora.testing import ScriptedTurn, group_event


async def test_every_message_is_appended_to_the_audit(harness):
    for i in range(10):
        await harness.handler.on_event(group_event(body=f"m{i}"))
    await harness.drain()
    assert len(await harness.store.recent(conversation_id="g1", limit=100)) == 10


async def test_ambient_never_constructs_agent(harness):
    """R-1: ambient traffic never reaches the Agent SDK."""
    for i in range(10):
        await harness.handler.on_event(group_event(body=f"m{i}"))
    await harness.drain()
    assert harness.agents_constructed == 0
    assert harness.sink.sent == []


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
    async def slow_feed(peer_id, text):
        await asyncio.sleep(1.0)
        return True

    harness.honcho.feed = slow_feed
    started = asyncio.get_running_loop().time()
    await harness.handler.on_event(group_event(body="hi"))
    assert asyncio.get_running_loop().time() - started < 0.2


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
