"""SSE client for the instagram-bridge (`GET /events`).

Mirrors `nora.platform.whatsapp.listener` almost exactly — long-lived GET,
full-jitter exponential backoff, one bad line logged and skipped, a raising
handler never killing the stream — because the two are the same transport with
a different path, and a reader who has read one should not have to re-read the
other.

The discriminator is `kind`, not WhatsApp's `type`. Two kinds are handled HERE,
at listener level, and never reach `on_event`:

- **`heartbeat`** — the real socket-death detector, and
  it is the reason `last_event_age_seconds` means anything: a quiet room and a
  dead socket are indistinguishable without it. It updates `last_event_at` like
  any other event, because that is what it is FOR.

  It also carries `connected` and `session`, which the WhatsApp heartbeat does
  not, and this listener KEEPS BOTH. That makes a degraded session visible in the
  case startup cannot see: a session that was `authenticated` when Nora booted
  and got checkpointed at 4am. Without it, Nora's `/health` would report a
  connected adapter in a room that has gone silent because every send is
  409ing — the exact "silently degraded adapter is indistinguishable from a
  quiet room" failure.

- **`dropped`** — the bridge's drop-and-count report: while no SSE client was
  connected its bounded buffer overflowed and OLDEST events were discarded.
  Audit rows were lost upstream and nothing here can recover them, so it is
  logged at WARNING, loud, with the running total.

`reaction`, `edit`, `unsend` and `typing` are NOT handled here. They are part
of the contract and they reach `on_event`, where `parser.parse_event` turns an
`edit` into an edit, a `reaction` or an `unsend`
into a `Touch`, and answers None for the rest —
the same posture WhatsApp takes toward event types it does not ingest, and the
reason the parser stays total on its own rather than relying on
this module to pre-filter.
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
from .self_id import AUTHENTICATED

log = logging.getLogger("nora.instagram.listener")


class InstagramListener:
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
        #: The session state the most recent heartbeat reported, or None if no
        #: heartbeat has arrived yet. `None` is NOT `authenticated`: it means
        #: "nothing has said", and `InstagramAdapter.health` reads it that way
        #: rather than assuming the good case.
        self.session_state: str | None = None

    @property
    def session_degraded(self) -> bool:
        """Whether the bridge has told us, since this process started, that the
        session cannot act.

        False while `session_state` is None — a bridge that has not sent a
        heartbeat yet has not made a claim either way, and `connected` is
        already False in that window, so reporting a degradation here would
        double-count a bridge that is merely starting up.
        """
        return self.session_state is not None and self.session_state != AUTHENTICATED

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
            log.info("reconnecting to instagram-bridge in %.1fs", delay)
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
                kind = event.get("kind") if isinstance(event, dict) else None
                if kind == "heartbeat":
                    self._absorb_heartbeat(event)
                    continue
                if kind == "dropped":
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

    def _absorb_heartbeat(self, event: dict) -> None:
        """Record the session state, and say so ON THE TRANSITION only.

        Logging every heartbeat would put a line in the log every ~30s and say
        nothing; logging only the change is what makes "she went deaf at 04:12"
        findable. The state itself is what `health()` reports, so a checkpoint
        that happened after startup reaches the operator through `/health` as
        well as through this line.
        """
        state = event.get("session")
        state = state if isinstance(state, str) and state else None
        if state == self.session_state:
            return
        previous, self.session_state = self.session_state, state
        if state is None:
            return
        if state != AUTHENTICATED:
            log.error(
                "instagram session is now %r (was %r) — sends will be refused until a "
                "human clears it; Nora is DEGRADED on instagram, not merely quiet",
                state, previous,
            )
        elif previous is not None:
            log.info("instagram session recovered: %r -> %r", previous, state)
