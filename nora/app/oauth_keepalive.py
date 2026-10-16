"""Keep every OAuth login Nora generates media with alive, on a schedule (DEC-308).

Every OAuth-authenticated provider for image or video generation is refreshed
proactively on a schedule, not lazily, so a use never meets an expired login
(a refresh token left idle can be refused, `invalid_grant`, with nothing
logged).

ONE component, any number of lanes. A lane registers with `register` and
satisfies `KeptLane`: its name, how early it refreshes before its access
token expires, the current link (when it was refreshed, when it expires), and
a `refresh` coroutine that takes the SAME locks a live call does (so the
keeper and a call never both spend a rotating token). The registrants
are the image lanes `xai` and `openai_codex`; an OAuth video provider
registers the same way. MiniMax CN (video) authenticates with an API key and
has nothing to keep alive.

Per lane, a loop that:

- refreshes at the earlier of `refreshed_at + interval` and `expires_at -
  skew`, a little early by a random jitter so restarts do not align;
- retries a transient failure (`down`, `rate_limited`, `cloudflare`) with a
  capped exponential backoff;
- on a terminal one (the issuer refused the refresh token, or the state file
  is unwritable) stops hammering: it tries once per interval and stays loud
  (an ERROR each time, and the refused gauge the alert reads);
- is supervised: an exception is logged with its traceback, counted, and the
  loop restarts after a backoff, never killing the daemon and never dying
  silently.

Metrics are timestamps, not rare counters (Prometheus loses a rare counter
across restarts): the alert reads the last-success timestamp, which the boot
publishes from the state file.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from ..obs.metrics import Metrics

log = logging.getLogger("nora.app.oauth_keepalive")


class KeptLink(Protocol):
    """What the keeper reads of a lane's current link. Never a token."""

    @property
    def refreshed_at(self) -> float: ...  # epoch seconds; 0 = unknown/never

    @property
    def expires_at(self) -> float: ...  # epoch seconds; 0 = unknown


class LaneOutcome(Protocol):
    """A refresh outcome as the lane reports it to its observer."""

    @property
    def lane(self) -> str: ...

    @property
    def ok(self) -> bool: ...

    @property
    def reason(self) -> str: ...

    @property
    def refreshed_at(self) -> float: ...

    @property
    def expires_at(self) -> float: ...


class KeptLane(Protocol):
    """The registration protocol. `nora.infra.image_gen.oauth_lane.
    RotatingOAuthLane` satisfies it; so must any future OAuth lane."""

    @property
    def lane(self) -> str: ...

    @property
    def skew_seconds(self) -> float: ...

    @property
    def terminal_reasons(self) -> frozenset[str]: ...

    def snapshot(self) -> KeptLink | None: ...

    async def refresh(self, *, unless_refreshed_since: float) -> KeptLink | str: ...

    def set_observer(self, observer: Callable[[Any], None] | None) -> None: ...


@dataclass(frozen=True)
class KeepaliveTiming:
    """The loop's clock. Only `interval_seconds` is configured (per lane);
    the rest are constants with their reasons."""

    interval_seconds: float
    # Never plan a refresh sooner than this after one: a lane whose access
    # token lives less than its skew would otherwise spin.
    min_delay_seconds: float = 60.0
    # Transient retry: 1, 2, 4 ... minutes, capped at 30, well inside every
    # lane's one-hour expiry skew.
    retry_base_seconds: float = 60.0
    retry_cap_seconds: float = 1800.0
    # Jitter pulls a refresh EARLIER (never later, so the interval is a
    # ceiling), by up to 10% of the wait and at most 15 minutes.
    jitter_fraction: float = 0.1
    jitter_cap_seconds: float = 900.0
    # A crashed loop restarts after 1, 2, 4 ... minutes, capped at 30.
    restart_base_seconds: float = 60.0
    restart_cap_seconds: float = 1800.0


class OAuthKeepalive:
    """The one keeper. `register` each lane, then `start`."""

    def __init__(
        self,
        metrics: Metrics,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._metrics = metrics
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._lanes: list[tuple[KeptLane, KeepaliveTiming]] = []

    def register(self, lane: KeptLane, timing: KeepaliveTiming) -> None:
        """Take `lane` on: its outcomes (from the keeper AND from live calls)
        now move the lane's gauges, and the boot publishes what its state
        file already says, so a restart does not blank the alert's input."""
        lane.set_observer(self._observe)
        self._lanes.append((lane, timing))
        name = lane.lane
        self._metrics.gauge(
            "nora_oauth_refresh_interval_seconds", timing.interval_seconds, lane=name,
        )
        # `nora_oauth_refresh_refused` is NOT reset here: it is published by
        # the first outcome, so a restart neither clears a refusal a live call
        # saw nor resolves-and-refires the alert on every boot.
        link = lane.snapshot()
        # 0 when the lane has never refreshed under this login: the alert
        # reads that as "too long ago", which is right if the first refresh
        # (due immediately) then fails.
        self._metrics.gauge(
            "nora_oauth_last_refresh_timestamp_seconds",
            link.refreshed_at if link is not None else 0.0, lane=name,
        )
        self._metrics.gauge(
            "nora_oauth_access_expiry_timestamp_seconds",
            link.expires_at if link is not None else 0.0, lane=name,
        )
        log.info(
            "oauth keepalive: lane %s registered, interval %.0fs", name, timing.interval_seconds,
        )

    @property
    def lanes(self) -> tuple[str, ...]:
        return tuple(lane.lane for lane, _ in self._lanes)

    def start(self, spawn: Callable[[Coroutine[Any, Any, None]], object]) -> None:
        for lane, timing in self._lanes:
            spawn(self.run(lane, timing))

    # -- metrics --------------------------------------------------------------

    def _observe(self, event: LaneOutcome) -> None:
        name = event.lane
        if event.ok:
            gauge = self._metrics.gauge
            gauge("nora_oauth_last_refresh_timestamp_seconds", event.refreshed_at, lane=name)
            gauge("nora_oauth_access_expiry_timestamp_seconds", event.expires_at, lane=name)
            self._metrics.gauge("nora_oauth_refresh_refused", 0, lane=name)
            return
        self._metrics.inc("nora_oauth_refresh_failures_total", lane=name, reason=event.reason)
        terminal = next(
            (t.terminal_reasons for t, _ in self._lanes if t.lane == name), frozenset(),
        )
        if event.reason in terminal:
            self._metrics.gauge("nora_oauth_refresh_refused", 1, lane=name)

    # -- the loop -------------------------------------------------------------

    def _plan(
        self, link: KeptLink | None, lane: KeptLane, timing: KeepaliveTiming, floor: float,
    ) -> float:
        """Seconds until the next refresh: the earlier of the interval and
        the expiry skew, pulled earlier by jitter, never under `floor`."""
        if link is None or link.refreshed_at <= 0:
            return floor
        due = link.refreshed_at + timing.interval_seconds
        if link.expires_at > 0:
            # At most half the token's life early: a token that lives no
            # longer than the skew would otherwise be refreshed every
            # `min_delay_seconds`, ~1,400 rotations a day.
            life = link.expires_at - link.refreshed_at
            due = min(due, link.expires_at - min(lane.skew_seconds, life / 2))
        # Never further out than one interval: a refreshed_at in the future
        # (a clock jump) must not park the keeper past it.
        span = min(due - self._clock(), timing.interval_seconds)
        if span <= 0:
            return floor
        jitter = self._rand() * min(span * timing.jitter_fraction, timing.jitter_cap_seconds)
        return max(span - jitter, floor)

    async def _loop(self, lane: KeptLane, timing: KeepaliveTiming) -> None:
        link = lane.snapshot()
        delay = self._plan(link, lane, timing, floor=0.0)
        failures = 0
        while True:
            # Planned from `link`: a link minted after it (a call, another
            # process) is adopted by `refresh`, never spent again.
            since = link.refreshed_at if link is not None else 0.0
            await self._sleep(delay)
            outcome = await lane.refresh(unless_refreshed_since=since)
            if not isinstance(outcome, str):
                failures = 0
                link = outcome
                delay = self._plan(link, lane, timing, floor=timing.min_delay_seconds)
                log.info("oauth keepalive: lane %s refreshed; next in %.0fs", lane.lane, delay)
            elif outcome in lane.terminal_reasons:
                failures = 0
                delay = timing.interval_seconds
                log.error(
                    "oauth keepalive: lane %s cannot refresh (%s) and will not heal by "
                    "retrying; trying again in %.0fs. It needs the operator: a new "
                    "login for `auth`, a writable ./state for `state_unwritable`.",
                    lane.lane, outcome, delay,
                )
            else:
                failures += 1
                delay = min(
                    timing.retry_base_seconds * 2 ** (failures - 1), timing.retry_cap_seconds,
                )
                log.warning(
                    "oauth keepalive: lane %s refresh failed (%s), attempt %d; retrying in %.0fs",
                    lane.lane, outcome, failures, delay,
                )

    async def run(self, lane: KeptLane, timing: KeepaliveTiming) -> None:
        """`_loop`, supervised: it never ends while the process runs."""
        delay = timing.restart_base_seconds
        while True:
            try:
                await self._loop(lane, timing)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "oauth keepalive: lane %s loop crashed; restarting in %.0fs",
                    lane.lane, delay,
                )
                self._metrics.inc("nora_oauth_keepalive_restarts_total", lane=lane.lane)
                await self._sleep(delay)
                delay = min(delay * 2, timing.restart_cap_seconds)
