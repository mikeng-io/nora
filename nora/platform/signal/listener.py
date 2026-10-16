"""SSE client for signal-cli.

Long-lived GET with full-jitter exponential backoff. One bad event is logged and
skipped; the stream is never killed by a single message or a raising callback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable

import aiohttp

log = logging.getLogger("nora.signal.listener")


class SignalListener:
    def __init__(
        self,
        base_url: str,
        *,
        max_backoff: float = 60.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/v1/events"
        self._max_backoff = max_backoff
        self._sleep = sleeper
        self._clock = clock
        self.last_event_at: float | None = None
        self.connected = False

    def _next_backoff(self, attempt: int) -> float:
        """Full jitter: uniform(0, min(cap, base * 2**attempt)) — avoids thundering herd."""
        # Reconnect jitter, not a cryptographic use of randomness.
        return random.uniform(0.0, min(self._max_backoff, 1.0 * (2**attempt)))  # noqa: S311

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        attempt = 0
        while True:
            try:
                await self._stream(on_event)
                attempt = 0                     # a clean close resets the backoff ladder
            except asyncio.CancelledError:
                raise
            except Exception as exc:            # noqa: BLE001 — reconnect, always
                log.warning("SSE connection failed (%s); reconnecting", type(exc).__name__)
            finally:
                self.connected = False
            delay = self._next_backoff(attempt)
            attempt = min(attempt + 1, 6)
            log.info("reconnecting to signal-cli in %.1fs", delay)
            await self._sleep(delay)

    async def _stream(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(self._url, headers={"Accept": "text/event-stream"}) as resp,
        ):
            resp.raise_for_status()
            self.connected = True
            log.info("SSE connected to %s", self._url)
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith(":"):
                    # signal-cli's keep-alive comment (about every 18s). It is
                    # the only evidence a quiet room's socket is alive, so it
                    # advances `last_event_at` — the one clock `PlatformSilent`
                    # reads — and it is not a message, so it goes no further.
                    self.last_event_at = self._clock()
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    log.warning("skipping unparseable SSE line")
                    continue
                self.last_event_at = self._clock()
                try:
                    await on_event(event)
                except Exception:
                    log.exception("event handler raised; continuing")
