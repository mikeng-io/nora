"""Discord Gateway listener.

Wraps a discord.py-shaped `Client`: registers `on_ready`/`on_resumed`/
`on_message`/`on_disconnect` and tracks connection state the way
`nora.platform.signal.listener.SignalListener` tracks `connected`/
`last_event_at` for the SSE stream.

Unlike `SignalListener`, there is no manual reconnect-with-backoff loop here:
`discord.py`'s `Client.start()` owns the Gateway handshake, heartbeat, and
RESUME-on-disconnect internally, the same way
signal-cli's own daemon owns Signal's reconnect. `on_ready`, `on_resumed` and
`on_disconnect` exist to observe that internal state, not to drive it.

**`on_resumed` IS LOAD-BEARING.** discord.py dispatches `disconnect` on EVERY
reconnect cycle — including the routine op-7 RECONNECT the Gateway issues to
rebalance sessions, which is ordinary operation and not a fault — and then
dispatches **`resumed`**, not `ready`, when the session comes back
(`discord/state.py`: `ready` is dispatched only on a full IDENTIFY). Observing
`ready` alone would latch `connected = False` on the first routine blip and
never clear it while messages kept flowing. `/health` marks the process
unhealthy when any enabled platform reports disconnected, so a healthy Nora
would serve a permanent 503: the signal that exists to make "deaf on Discord"
loud would become a constant alarm, which is an absent one.

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
        on_ready_extra: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._token = token
        self._clock = clock
        # Slash-command registration — `on_ready` only, never `on_resumed`:
        # `on_ready` fires on a full re-IDENTIFY (a fresh client, per
        # `DiscordAdapter`'s own per-attempt-client design), and Discord
        # already has the commands from that same client's earlier
        # `on_ready` by the time any `on_resumed` fires within its lifetime.
        self._on_ready_extra = on_ready_extra
        self.connected = False
        self.last_event_at: float | None = None

    async def listen(
        self,
        on_message_cb: Callable[[Any], Awaitable[None]],
        on_edit_cb: Callable[[Any], Awaitable[None]] | None = None,
        on_touch_cb: Callable[[str, Any], Awaitable[None]] | None = None,
    ) -> None:
        """`on_edit_cb` receives the EDITED message.
        It is wired to `on_raw_message_edit`, not `on_message_edit`: the
        latter fires only for a message still in discord.py's cache, so an
        edit to anything older than the cache — or sent before a reconnect —
        would vanish without a trace. The raw event carries the full message
        (`payload.message`, discord.py >= 2.4) either way."""
        async def on_ready() -> None:
            self.connected = True
            # A CONNECTED EDGE IS LIVENESS. If `last_event_at` were written only by
            # `on_message`, `nora_adapter_last_event_age_seconds{platform="discord"}`
            # would not EXIST until the first Discord message the process saw,
            # and every supervised restart rebuilds this listener. A Gateway
            # that never connects would publish no series at all, which greps
            # as «no problem» and is actually «no instrument», in the one
            # signal DEC-102 made to make a wedged Gateway alertable.
            #
            # Signal's counterpart ticks on every SSE envelope — receipts and
            # typing included — so its age means «wedged». Stamping the connect
            # edges here is what stops Discord's meaning «nobody talked» in a
            # quiet channel.
            self.last_event_at = self._clock()
            log.info("discord gateway connected")
            if self._on_ready_extra is not None:
                await self._on_ready_extra()

        async def on_resumed() -> None:
            # A RESUME is a live session again, so this is a CONNECTED edge
            # exactly as `on_ready` is — see the module docstring for why
            # observing only `ready` latched this False forever.
            self.connected = True
            self.last_event_at = self._clock()   # a connected edge — see `on_ready`
            log.info("discord gateway resumed")

        async def on_message(message: Any) -> None:
            self.last_event_at = self._clock()
            try:
                await on_message_cb(message)
            except Exception as exc:  # noqa: BLE001 — SDK-dispatch boundary; logged by type, never swallowed
                log.error("event handler raised; continuing: %s", type(exc).__name__)

        async def on_raw_message_edit(payload: Any) -> None:
            self.last_event_at = self._clock()
            if on_edit_cb is None:
                return
            try:
                await on_edit_cb(payload.message)
            except Exception as exc:  # noqa: BLE001 — SDK-dispatch boundary; logged by type
                log.error("edit handler raised; continuing: %s", type(exc).__name__)

        # REACTIONS and DELETES, raw for `on_raw_message_edit`'s reason: the cached events miss any
        # message older than the cache. `on_touch_cb` receives the event's
        # name and its raw payload; the adapter parses it.
        async def _touch(kind: str, payload: Any) -> None:
            self.last_event_at = self._clock()
            if on_touch_cb is None:
                return
            try:
                await on_touch_cb(kind, payload)
            except Exception as exc:  # noqa: BLE001 — SDK-dispatch boundary; logged by type
                log.error("%s handler raised; continuing: %s", kind, type(exc).__name__)

        async def on_raw_reaction_add(payload: Any) -> None:
            await _touch("reaction_add", payload)

        async def on_raw_reaction_remove(payload: Any) -> None:
            await _touch("reaction_remove", payload)

        async def on_raw_message_delete(payload: Any) -> None:
            await _touch("delete", payload)

        async def on_disconnect() -> None:
            self.connected = False
            log.warning("discord gateway disconnected")

        # Registration binds on `coro.__name__` (discord.py's own convention),
        # which is exactly what the closures above are named for.
        self._client.event(on_ready)
        self._client.event(on_resumed)
        self._client.event(on_message)
        self._client.event(on_raw_message_edit)
        self._client.event(on_raw_reaction_add)
        self._client.event(on_raw_reaction_remove)
        self._client.event(on_raw_message_delete)
        self._client.event(on_disconnect)
        await self._client.start(self._token)
