"""Connect memory admission and debrief retries to the engagement slots."""

from nora.domain.lineage import room_key, split_room
from nora.engagement.slot_keys import resolve_slot_room
from nora.engagement.slots import ConversationSlots, Key
from nora.policy import Policy


class WakeRoomSlots:
    def __init__(self, policy: Policy, slots: ConversationSlots) -> None:
        self._policy = policy
        self._slots = slots

    def resolve(self, room: str, unit: str) -> str | None:
        key = self.key(room, unit)
        return room_key(*key) if key is not None else None

    def key(self, room: str, unit: str) -> Key | None:
        candidate = resolve_slot_room(self._policy.rooms(), room, unit=unit)
        return (candidate.platform, candidate.primary_id) if candidate is not None else None

    def held(self, room: str) -> bool:
        platform, room_id = split_room(room)
        return platform is not None and self._slots.held((platform, room_id))
