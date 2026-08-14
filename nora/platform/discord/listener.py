"""Discord Gateway listener (design/discord-integration/01).

Wraps a discord.py-shaped `Client`: registers `on_ready`/`on_resumed`/
`on_message`/`on_disconnect` and tracks connection state the way
`nora.platform.signal.listener.SignalListener` tracks `connected`/
`last_event_at` for the SSE stream.

Unlike `SignalListener`, there is no manual reconnect-with-backoff loop here:
`discord.py`'s `Client.start()` owns the Gateway handshake, heartbeat, and
RESUME-on-disconnect internally (design/discord-integration/01), the same way
signal-cli's own daemon owns Signal's reconnect. `on_ready`, `on_resumed` and
`on_disconnect` exist to observe that internal state, not to drive it.

**`on_resumed` IS LOAD-BEARING, and its absence was a live defect.** discord.py
dispatches `disconnect` on EVERY reconnect cycle — including the routine op-7
RECONNECT the Gateway issues to rebalance sessions, which is ordinary operation
and not a fault — and then dispatches **`resumed`**, not `ready`, when the
session comes back (`discord/state.py`: `ready` is dispatched only on a full
IDENTIFY). Registering `ready` alone therefore latched `connected = False` on
the first routine blip and never cleared it, while messages kept flowing and
Nora kept answering. `/health` marks the process unhealthy when any enabled
platform reports disconnected, so a perfectly healthy Nora served a permanent
503 and the container sat `unhealthy` — the signal that exists to make "deaf on
Discord" loud instead became a constant alarm, which is an absent one.

Built against a narrow structural Protocol rather than `discord.Client`
directly, so a test can supply a fake that records registered coroutines by
name instead of opening a real Gateway connection.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

log = logging.getLogger("nora.discord.listener")


class _ClientLike(Protocol):
    # `coro` is `Any`, not `Callable[..., Awaitable[None]]`: the real
    # `discord.Client.event` is generic (`def event(self, coro: CoroT, /) ->
    # CoroT` for a bound TypeVar), and a concrete Protocol type here is
    # narrower than that generic signature in a way mypy's structural check
    # rejects even though every real call site is a plain coroutine function.
    # Positional-only (`/`) still matches discord.py's own convention.
    def event(self, coro: Any, /) -> Any: ...

    async def start(self, token: str) -> None: ...


class DiscordListener:
    def __init__(
        self,
        client: _ClientLike,
        token: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._token = token
        self._clock = clock
        self.connected = False
        self.last_event_at: float | None = None

    async def listen(self, on_message_cb: Callable[[Any], Awaitable[None]]) -> None:
        async def on_ready() -> None:
            self.connected = True
            log.info("discord gateway connected")

        async def on_resumed() -> None:
            # A RESUME is a live session again, so this is a CONNECTED edge
            # exactly as `on_ready` is — see the module docstring for why
            # observing only `ready` latched this False forever.
            self.connected = True
            log.info("discord gateway resumed")

        async def on_message(message: Any) -> None:
            self.last_event_at = self._clock()
            try:
                await on_message_cb(message)
            except Exception:
                log.exception("event handler raised; continuing")

        async def on_disconnect() -> None:
            self.connected = False
            log.warning("discord gateway disconnected")

        # Registration binds on `coro.__name__` (discord.py's own convention),
        # which is exactly what the four closures above are named for.
        self._client.event(on_ready)
        self._client.event(on_resumed)
        self._client.event(on_message)
        self._client.event(on_disconnect)
        await self._client.start(self._token)
