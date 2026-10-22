"""SSE client for the whatsapp-bridge (`GET /events`).

Mirrors `nora.platform.signal.listener` almost exactly — long-lived GET,
full-jitter exponential backoff, one bad line logged and skipped, a raising
handler never killing the stream — because the two are the same transport
with a different path, and a reader who has read one should not have to
re-read the other.

Two WhatsApp-specific event types are handled HERE, at listener level, and
never reach `on_event` (the parser answers None for them too, but they are
this module's job to notice):

- `heartbeat` — the bridge's ~30s liveness pulse. It updates
  `last_event_at` like any other event, because that is what it is FOR:
  Nora `/health`'s per-platform `last_event_age_seconds` is the real
  socket-death detector, and in a quiet room a heartbeat is the only thing
  keeping that measurement honest.
- `dropped` — the bridge's drop-and-count report: while no SSE client was
  connected, its bounded buffer overflowed and OLDEST events were discarded
  (the bridge reports the loss rather than dropping silently). Audit rows
  were lost upstream and nothing here can recover them, so it is logged at
  WARNING, loud, with the running total.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable

import aiohttp

from ...infra.service_auth import service_auth_headers

log = logging.getLogger("nora.whatsapp.listener")


class WhatsAppListener:
    def __init__(
        self,
        base_url: str,
        *,
        max_backoff: float = 60.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/events"
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
            log.info("reconnecting to whatsapp-bridge in %.1fs", delay)
            await self._sleep(delay)

    async def _stream(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(self._url, headers={
                **service_auth_headers(), "Accept": "text/event-stream",
            }) as resp,
        ):
            resp.raise_for_status()
            self.connected = True
            log.info("SSE connected to %s", self._url)
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
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
                # ANY successfully parsed event — heartbeats explicitly
                # included; see the module docstring.
                self.last_event_at = self._clock()
                event_type = event.get("type") if isinstance(event, dict) else None
                if event_type == "heartbeat":
                    continue
                if event_type == "dropped":
                    log.warning(
                        "bridge dropped inbound events while no SSE client was "
                        "connected (dropped_total=%s) — audit rows were lost upstream",
                        event.get("dropped_total"),
                    )
                    continue
                try:
                    await on_event(event)
                except Exception:
                    log.exception("event handler raised; continuing")
