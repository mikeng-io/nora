"""Run pending graph debriefs after their engagement slot attempts are spent."""

import logging
from typing import Protocol

from nora.app.wake_rooms import WakeRoomSlots
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages import graph_debrief
from nora.memory import DebriefObligation

log = logging.getLogger(__name__)


class RetryStore(Protocol):
    async def pending_retries(self) -> tuple[DebriefObligation, ...]: ...
    async def record_retry_failure(self, row: DebriefObligation) -> None: ...


class WakeDebriefRetry:
    def __init__(
        self, wiring: graph_debrief.Wiring, store: RetryStore, rooms: WakeRoomSlots,
        slots: ConversationSlots,
    ) -> None:
        self._wiring = wiring
        self._store = store
        self._rooms = rooms
        self._slots = slots

    async def poll(self) -> None:
        for row in await self._store.pending_retries():
            try:
                await self._retry(row)
            except Exception:
                log.exception("Wake debrief retry failed")

    async def _retry(self, row: DebriefObligation) -> None:
        key = self._rooms.key(row.room, row.unit)
        if key is None:
            await self._store.record_retry_failure(row)
            return
        if not self._slots.try_acquire(key):
            return
        try:
            await graph_debrief.run_retry(self._wiring, row)
            await self._store.record_retry_failure(row)
        finally:
            self._slots.release_and_dispatch(key)
