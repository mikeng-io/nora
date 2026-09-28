"""Platform adapter health as Record — one `source_health` row per state
TRANSITION (design/cognition-loop/20-observe.md §1, migration step 1).

«From step 1, every collector poll and every platform adapter's up and down
transitions are recorded, so "every source healthy for the interval" is a
query, not an assumption.» The presence collector records its own polls
(`nora.presence.collector`); this records the adapters, from the samples the
liveness loop already takes every 10 s (`App._update_last_event_age`).

**Transitions, not samples.** A row is written the first time a platform is
observed after a start (so a restart is visible as a fresh `up` or `down`)
and whenever its state changes; a steady state writes nothing. Four states:

| state | from `AdapterHealth` |
|---|---|
| `up` | `connected` and not `degraded` |
| `degraded` | `degraded` (present, unable to act until a human intervenes) |
| `down` | not `connected` |
| `unknown` | `health()` itself raised: «could not check» is not «down» (R-27) |

A row that could not be written is retried on the next sample (the state is
only remembered once written), and counted
(`nora_errors_total{stage="source_health"}`).

Resolution is the sampler's 10 s: a flap shorter than that is invisible here,
as it is to the liveness gauges.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from nora.domain.ports import AdapterHealth, SourceHealthLog

log = logging.getLogger("nora.app.source_health")


def state_of(health: AdapterHealth) -> str:
    if health.degraded:
        return "degraded"
    return "up" if health.connected else "down"


class AdapterHealthRecorder:
    def __init__(
        self, *, log: SourceHealthLog, metrics: Any,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._log = log
        self._metrics = metrics
        self._clock = clock
        self._last: dict[str, str] = {}

    async def sample(self, health: AdapterHealth) -> None:
        """Record `health` if its state differs from the last one written."""
        await self._transition(health.platform, state_of(health), health.detail)

    async def unknown(self, platform: str) -> None:
        """`health()` raised for `platform`."""
        await self._transition(platform, "unknown", "health() raised")

    async def _transition(self, platform: str, state: str, detail: str) -> None:
        if self._last.get(platform) == state:
            return
        try:
            ok = await self._log.record(
                source=platform, scope="", event="transition", outcome=state,
                at_ms=int(self._clock() * 1000), detail=detail,
            )
        except Exception:
            log.exception("adapter health transition not written (%s)", platform)
            ok = False
        if ok:
            self._last[platform] = state
        else:
            self._metrics.inc("nora_errors_total", stage="source_health", platform=platform)
