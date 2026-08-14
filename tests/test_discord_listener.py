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
