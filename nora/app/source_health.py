"""Platform adapter transitions and periodic healthy probes as Record.

Makes "every source healthy for the interval" a query, not an assumption. The
presence collector records its own polls (`nora.presence.collector`); this
records the adapters, from the samples the liveness loop takes every 10 s
(`App._update_last_event_age`).

Transitions and healthy probes. A row is written the first time a platform is
observed after a start (so a restart is visible as a fresh `up` or `down`)
and whenever its state changes. A steady `up` records periodic probes. States:

| state | from `AdapterHealth` |
|---|---|
- `up`: `connected`, not `degraded`, an event within `STALE_AFTER_SECONDS`;
- `stale`: `connected` and not `degraded`, but no event for
  `STALE_AFTER_SECONDS` — a socket that says it is open and delivers
  nothing is not `up`;
- `degraded`: present, unable to act until a human intervenes;
- `down`: not `connected`;
- `unknown`: `health()` itself raised — «could not check» is not «down»
  (R-27).

`stale` is the SAME threshold the `PlatformSilent` alert fires on
(`deploy/prometheus/rules/nora.yml`, 900 s), so the Record and the alert agree
on what «silent» means. A genuinely quiet room reads `stale` too on a
platform with no heartbeat of its own (Discord stamps only its connect
edges); that is the alert's semantics as well, and it is why a later step
treats `stale` as «not proven healthy», never as «down».

A row that could not be written — including one that did not finish within
`timeout_seconds`: the sampler also publishes the liveness gauges, and a hung
database write must not freeze them — is retried on the next sample (the
state is only remembered once written), and counted
(`nora_errors_total{stage="source_health"}`).

Resolution is the sampler's 10 s: a flap shorter than that is invisible here,
as it is to the liveness gauges.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from nora.domain.ports import AdapterHealth, SourceHealthLog

log = logging.getLogger("nora.app.source_health")

#: `PlatformSilent`'s threshold (`deploy/prometheus/rules/nora.yml`).
STALE_AFTER_SECONDS = 900.0
# ponytail: a steady adapter contributes at most one healthy probe per minute.
HEALTHY_PROBE_INTERVAL_MS = 60_000


def state_of(health: AdapterHealth, *, stale_after: float = STALE_AFTER_SECONDS) -> str:
    if health.degraded:
        return "degraded"
    if not health.connected:
        return "down"
    age = health.last_event_age_seconds
    return "stale" if age is not None and age > stale_after else "up"


class AdapterHealthRecorder:
    def __init__(
        self, *, log: SourceHealthLog, metrics: Any,
        clock: Callable[[], float] = time.time,
        timeout_seconds: float = 5.0,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
    ) -> None:
        self._timeout = timeout_seconds
        self._stale_after = stale_after_seconds
        self._log = log
        self._metrics = metrics
        self._clock = clock
        self._last: dict[str, tuple[str, int]] = {}

    async def sample(self, health: AdapterHealth) -> None:
        """Record a state change or a due healthy probe."""
        await self._transition(
            health.platform, state_of(health, stale_after=self._stale_after), health.detail,
        )

    async def unknown(self, platform: str) -> None:
        """`health()` raised for `platform`."""
        await self._transition(platform, "unknown", "health() raised")

    async def _transition(self, platform: str, state: str, detail: str) -> None:
        now = int(self._clock() * 1000)
        last_state, last_at = self._last.get(platform, ("", now))
        steady = last_state == state
        if steady and (state != "up" or now - last_at < HEALTHY_PROBE_INTERVAL_MS):
            return
        try:
            ok = await asyncio.wait_for(
                self._log.record(
                    source=platform, scope="", event="poll" if steady else "transition",
                    outcome=state, at_ms=now, detail=detail,
                ),
                timeout=self._timeout,
            )
        except Exception:
            log.exception("adapter health transition not written (%s)", platform)
            ok = False
        if ok:
            self._last[platform] = state, now
        else:
            self._metrics.inc("nora_errors_total", stage="source_health", platform=platform)
