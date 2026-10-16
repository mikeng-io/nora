"""`DiscordListener` — the state-tracking wrapper around a discord.py-shaped
Client. Tests supply a fake client that records registered event coroutines
by name (mirroring discord.py's own `Client.event()`, which binds on
`coro.__name__`) instead of touching a real Gateway connection."""

from __future__ import annotations

import pytest

from nora.platform.discord.listener import DiscordListener


class FakeClient:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.started_with: str | None = None

    def event(self, coro):
        self.handlers[coro.__name__] = coro
        return coro

    async def start(self, token: str) -> None:
        self.started_with = token


@pytest.fixture
def clock():
    ticks = iter([100.0, 200.0])
    return lambda: next(ticks)


async def test_listen_starts_the_client_with_the_token():
    client = FakeClient()
    listener = DiscordListener(client, "tok")

    async def on_message(_message):
        pass

    await listener.listen(on_message)

    assert client.started_with == "tok"


async def test_on_ready_marks_the_listener_connected():
    client = FakeClient()
    listener = DiscordListener(client, "tok")

    async def on_message(_message):
        pass

    await listener.listen(on_message)
    assert listener.connected is False

    await client.handlers["on_ready"]()

    assert listener.connected is True


async def test_on_disconnect_marks_the_listener_not_connected():
    client = FakeClient()
    listener = DiscordListener(client, "tok")

    async def on_message(_message):
        pass

    await listener.listen(on_message)
    await client.handlers["on_ready"]()
    await client.handlers["on_disconnect"]()

    assert listener.connected is False


async def test_on_message_forwards_to_the_callback_and_stamps_last_event_at(clock):
    client = FakeClient()
    listener = DiscordListener(client, "tok", clock=clock)
    received = []

    async def on_message(message):
        received.append(message)

    await listener.listen(on_message)
    await client.handlers["on_message"]("raw-message-1")

    assert received == ["raw-message-1"]
    assert listener.last_event_at == 100.0


async def test_a_raising_callback_does_not_propagate():
    client = FakeClient()
    listener = DiscordListener(client, "tok")

    async def on_message(_message):
        raise RuntimeError("boom")

    await listener.listen(on_message)

    await client.handlers["on_message"]("raw-message-1")  # must not raise


async def test_connecting_counts_as_liveness_not_just_a_message():
    """`nora_adapter_last_event_age_seconds{platform="discord"}` is published
    only when `last_event_age_seconds` is not None (`nora/__main__.py`), and a
    prometheus label-child is created on first write — so a listener that
    stamped `last_event_at` ONLY in `on_message` published no series at all
    until the first Discord message the process ever saw, and every supervised
    restart rebuilds the listener and would make it vanish again.

    That is the failure the runbook tells an operator to alert on: a
    Gateway that never connects would have produced an ABSENT series, which
    greps as "no problem" and is actually "no instrument" — the thing CLAUDE.md
    forbids reading as evidence.
    """
    client = FakeClient()
    listener = DiscordListener(client, "tok", clock=lambda: 1234.0)

    async def _on(_m):
        pass

    await listener.listen(_on)
    assert listener.last_event_at is None, "nothing has happened yet"

    await client.handlers["on_ready"]()
    assert listener.last_event_at == 1234.0, (
        "connecting is liveness — otherwise the series does not exist until "
        "somebody talks"
    )


async def test_resuming_also_counts_as_liveness():
    """The same, for the edge that fires after an ordinary reconnect — which is
    the common case, since `on_ready` only fires on a full re-IDENTIFY."""
    client = FakeClient()
    listener = DiscordListener(client, "tok", clock=lambda: 99.0)

    async def _on(_m):
        pass

    await listener.listen(_on)
    await client.handlers["on_resumed"]()

    assert listener.connected is True
    assert listener.last_event_at == 99.0


async def test_on_ready_extra_is_awaited_once_the_client_is_ready():
    """The slash-command registration hook: only a full re-IDENTIFY needs it, so it lands on
    `on_ready`, never `on_resumed` — Discord already has the commands from
    the earlier `on_ready` in the same session."""
    client = FakeClient()
    calls = []

    async def extra():
        calls.append("ready")

    listener = DiscordListener(client, "tok", on_ready_extra=extra)

    async def _on(_m):
        pass

    await listener.listen(_on)
    await client.handlers["on_ready"]()

    assert calls == ["ready"]


async def test_on_ready_extra_is_not_called_again_on_resume():
    client = FakeClient()
    calls = []

    async def extra():
        calls.append("ready")

    listener = DiscordListener(client, "tok", on_ready_extra=extra)

    async def _on(_m):
        pass

    await listener.listen(_on)
    await client.handlers["on_ready"]()
    await client.handlers["on_resumed"]()

    assert calls == ["ready"]


async def test_an_edit_is_heard_on_the_raw_event_and_forwards_the_edited_message(clock):
    """`on_raw_message_edit`, not
    `on_message_edit`: the cooked event fires only for a message still in
    discord.py's cache, so an edit to anything older would vanish. The raw
    payload's `.message` is the full edited message, and that is what the
    callback receives."""
    client = FakeClient()
    listener = DiscordListener(client, "tok", clock=clock)
    received = []

    async def on_message(_message):
        pass

    async def on_edit(message):
        received.append(message)

    await listener.listen(on_message, on_edit)

    assert "on_raw_message_edit" in client.handlers
    assert "on_message_edit" not in client.handlers

    class _Payload:
        message = "edited-message-1"

    await client.handlers["on_raw_message_edit"](_Payload())
    assert received == ["edited-message-1"]
    assert listener.last_event_at == 100.0


async def test_a_raising_edit_callback_does_not_propagate():
    client = FakeClient()
    listener = DiscordListener(client, "tok")

    async def on_message(_message):
        pass

    async def on_edit(_message):
        raise RuntimeError("boom")

    await listener.listen(on_message, on_edit)

    class _Payload:
        message = "edited-message-1"

    await client.handlers["on_raw_message_edit"](_Payload())  # must not raise


async def test_raw_reactions_and_deletes_reach_the_touch_callback():
    """The RAW events are registered (the cached
    ones miss anything older than the cache) and each reaches `on_touch_cb`
    with its name, so the adapter can record it beside its message."""
    client = FakeClient()
    listener = DiscordListener(client, "tok", clock=lambda: 5.0)
    seen: list[tuple[str, object]] = []

    async def on_message(_message):
        pass

    async def on_touch(kind, payload):
        seen.append((kind, payload))

    await listener.listen(on_message, None, on_touch)
    for name in ("on_raw_reaction_add", "on_raw_reaction_remove", "on_raw_message_delete"):
        assert name in client.handlers
    await client.handlers["on_raw_reaction_add"]("p1")
    await client.handlers["on_raw_reaction_remove"]("p2")
    await client.handlers["on_raw_message_delete"]("p3")
    assert seen == [("reaction_add", "p1"), ("reaction_remove", "p2"), ("delete", "p3")]
    assert listener.last_event_at == 5.0
