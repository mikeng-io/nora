"""Admit reflection passes on a clock without running their models in the poll loop."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from nora.app.wake_pass import wake_lane
from nora.config import CognitionConfig
from nora.domain.lineage import room_key
from nora.memory import WakeClaim, WakeFailed, WakeGroup, WakeOutcome, WakePass, WakePolicy
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, root_span
from nora.policy import Policy

log = logging.getLogger(__name__)


class WakeClaimStore(Protocol):
    async def groups(self, now: datetime) -> tuple[WakeGroup, ...]: ...
    async def recover(self, now: datetime, bound: timedelta) -> None: ...
    async def claim(
        self, group: WakeGroup, reading_room: str, now: datetime, policy: WakePolicy,
    ) -> WakeClaim | None: ...
    async def settle(self, claim: WakeClaim, outcome: WakeOutcome, now: datetime) -> int: ...


class DebriefRetry(Protocol):
    async def poll(self) -> None: ...


class ScheduledReview(Protocol):
    def poll(self, now: datetime) -> None: ...
    def cancel(self) -> None: ...
    async def drain(self) -> None: ...


class WakePoller:
    def __init__(
        self, store: WakeClaimStore, runner: WakePass, debrief: DebriefRetry, policy: Policy,
        metrics: Metrics, clock: Callable[[], datetime], *, config: CognitionConfig,
        review: ScheduledReview | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._debrief = debrief
        self._policy = policy
        self._metrics = metrics
        self._clock = clock
        self._review = review
        self._tick_seconds = config.tick_seconds
        self._timeout = config.session_timeout_seconds
        self._admission = WakePolicy(
            config.quiet_seconds, config.cue_quiet_seconds, config.batch_max,
            config.max_age_seconds,
            config.cooldown_seconds, config.max_concurrent_sessions, config.backoff_max_seconds,
        )
        self._running: dict[str, asyncio.Task[None]] = {}

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.poll()
                except Exception:
                    log.exception("Wake poll failed")
                await asyncio.sleep(self._tick_seconds)
        finally:
            if self._review is not None:
                self._review.cancel()
            for task in self._running.values():
                task.cancel()
            await self.drain()

    async def drain(self) -> None:
        await asyncio.gather(*self._running.values(), return_exceptions=True)
        if self._review is not None:
            await self._review.drain()

    async def recover(self) -> None:
        await self._recover_at(self._clock())

    async def _recover_at(self, now: datetime) -> None:
        await self._store.recover(now, timedelta(seconds=self._timeout))

    async def poll(self) -> None:
        now = self._clock()
        if self._review is not None:
            self._review.poll(now)
        await self._recover_at(now)
        homes = self._policy.home_rooms()
        for group in await self._store.groups(now):
            room = group.room
            if room is None:
                home = homes.get(group.unit)
                if home is None:
                    self._metrics.inc("nora_errors_total", stage="wake_no_home_room")
                    continue
                room = room_key(home.platform, home.primary_id)
            lane = wake_lane(group.unit)
            if (lane in self._running
                    or len(self._running) >= self._admission.max_concurrent_sessions):
                continue
            claim = await self._store.claim(group, room, now, self._admission)
            if claim is not None:
                self._running[lane] = asyncio.create_task(self._pass(claim))
        await self._debrief.poll()

    async def _pass(self, claim: WakeClaim) -> None:
        pass_id = claim.pass_id
        bind_tag_id(pass_id)
        try:
            with root_span(
                "wake", links=(), parent=None, **{TRACE_KIND: "generation", "nora.tag_id": pass_id},
            ):
                await self._run_and_settle(claim)
        except Exception:
            log.exception("Wake settlement failed")
        finally:
            clear_tag_id()
            self._running.pop(wake_lane(claim.group.unit))

    async def _run_and_settle(self, claim: WakeClaim) -> None:
        outcome: WakeOutcome = WakeFailed("cancelled")
        try:
            async with asyncio.timeout(self._timeout):
                outcome = await self._runner.run(claim)
        except TimeoutError:
            outcome = WakeFailed("timeout")
        except Exception:
            log.exception("Wake pass failed")
            outcome = WakeFailed("error")
        finally:
            annotate(**{"nora.outcome": outcome.cause if outcome.state == "failed" else "closed"})
            parked = await self._store.settle(claim, outcome, self._clock())
            if parked:
                self._metrics.inc("nora_wake_stimulus_parked_total", value=parked)
