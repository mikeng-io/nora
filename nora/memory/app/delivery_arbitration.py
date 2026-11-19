"""Recheck a scheduled delivery against current policy and recorded outcomes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nora.domain.lineage import room_key, split_room
from nora.memory.domain.delivery_arbitration import DeliveryHistory
from nora.policy import Policy


@dataclass(frozen=True, slots=True)
class DeliveryRecheck:
    status: Literal["deliver", "withhold", "unavailable"]
    reason: str | None = None


class DeliveryArbitration:
    def __init__(self, history: DeliveryHistory, policy: Policy) -> None:
        self._history = history
        self._policy = policy

    async def recheck(
        self, intention_id: str, destination: str, now: datetime,
    ) -> DeliveryRecheck:
        """Check recorded outcomes and current eligibility at the send boundary."""
        platform, conversation_id = split_room(destination)
        if platform is None:
            return DeliveryRecheck("unavailable", "unresolved_destination")
        try:
            room = self._policy.conversation(platform, conversation_id)
            if room is None:
                return DeliveryRecheck("withhold", "room_not_allowed")
            canonical = room_key(platform, room.primary_id)
            evidence = await self._history.read(room.unit, intention_id, canonical)
            current_room = self._policy.conversation(platform, conversation_id)
            if (
                current_room is None or current_room.unit != room.unit
                or current_room.primary_id != room.primary_id
                or not self._policy.proactive_allowed(
                    platform=platform, conversation_id=conversation_id,
                )
            ):
                return DeliveryRecheck("withhold", "room_not_allowed")
        except Exception:  # noqa: BLE001 — any incomplete pre-send read withholds delivery.
            return DeliveryRecheck("unavailable", "read_failed")
        if evidence is None or not evidence.current:
            return DeliveryRecheck("withhold", "not_current")
        if evidence.origin_room is not None and evidence.origin_room != canonical:
            return DeliveryRecheck("withhold", "wrong_destination")
        if evidence.prior is not None:
            return DeliveryRecheck("withhold", evidence.prior)
        return DeliveryRecheck("deliver")
