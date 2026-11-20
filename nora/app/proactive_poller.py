"""Claim due intentions after acquiring the destination's engagement slot."""

import asyncio
import logging
from collections.abc import Callable, Collection
from datetime import datetime, timedelta
from typing import Protocol

from nora.access import IdentityAccess
from nora.domain.models import ConversationTarget
from nora.engagement.slot_keys import resolve_slot_room
from nora.engagement.slots import ConversationSlots
from nora.memory import DebriefObligation, DueIntention, ProactiveClaim
from nora.policy import Policy

log = logging.getLogger(__name__)


class ClaimStore(Protocol):
    async def recover(self, now: datetime, bound: timedelta) -> None: ...
    async def due_intentions(self, now: datetime) -> tuple[DueIntention, ...]: ...
    async def claim(self, due: DueIntention, now: datetime) -> ProactiveClaim | None: ...
    async def settle(self, claim: ProactiveClaim, now: datetime) -> str | None: ...
    async def stage_debrief(self, claim: ProactiveClaim, row: DebriefObligation) -> bool: ...


class ProactivePass(Protocol):
    async def retry_pending(self, unit: str, target: ConversationTarget) -> None: ...
    async def run(self, claim: ProactiveClaim, target: ConversationTarget | None) -> None: ...


class ProactiveLanding:
    def __init__(self, policy: Policy, access: IdentityAccess, platforms: Collection[str]) -> None:
        self._policy = policy
        self._access = access
        self._platforms = platforms

    async def resolve(self, due: DueIntention) -> ConversationTarget | None:
        if due.origin_room is not None:
            candidate = resolve_slot_room(self._policy.rooms(), due.origin_room, unit=due.unit)
            if candidate is None:
                return None
            direct = not candidate.ids.isdisjoint(
                await self._access.direct_addresses(candidate.platform),
            )
            return ConversationTarget(candidate.platform, candidate.primary_id,
                                      "dm" if direct else "group")
        for platform in sorted(self._platforms):
            target = await self._access.direct_target(platform, due.owed_to)
            if target is not None:
                conversation = self._policy.conversation(platform, target.conversation_id)
                if conversation is not None and conversation.unit == due.unit:
                    return target
        return None


class ProactivePoller:
    def __init__(
        self, store: ClaimStore, slots: ConversationSlots, landing: ProactiveLanding,
        proactive: ProactivePass, clock: Callable[[], datetime], *,
        tick_seconds: float, timeout_seconds: float,
    ) -> None:
        self._store = store
        self._slots = slots
        self._landing = landing
        self._proactive = proactive
        self._clock = clock
        self._tick_seconds = tick_seconds
        self._bound = timedelta(seconds=timeout_seconds)

    async def run(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                log.exception("Proactive poll failed")
            await asyncio.sleep(self._tick_seconds)

    async def poll(self) -> None:
        now = self._clock()
        await self._store.recover(now, self._bound)
        for due in await self._store.due_intentions(now):
            try:
                await self._process(due)
            except Exception:
                log.exception("Proactive occurrence failed")

    async def _process(self, due: DueIntention) -> None:
        target = await self._landing.resolve(due)
        key = (target.platform, target.conversation_id) if target else None
        if key is not None and not self._slots.try_acquire(key):
            return
        try:
            if target is not None:
                await self._proactive.retry_pending(due.unit, target)
            claim = await self._store.claim(due, self._clock())
            if claim is not None:
                await self._proactive.run(claim, target)
        finally:
            if key is not None:
                self._slots.release_and_dispatch(key)
