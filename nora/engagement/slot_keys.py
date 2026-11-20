"""Resolve per-room slot keys against the policy's current room topology."""

from collections.abc import Iterable

from nora.domain.lineage import split_room
from nora.policy import Conversation


def resolve_slot_room(
    rooms: Iterable[Conversation], ref: str, *, unit: str | None = None,
) -> Conversation | None:
    platform, room = split_room(ref)
    matches = tuple(candidate for candidate in rooms
                    if (unit is None or candidate.unit == unit)
                    and room in candidate.ids and platform in (None, candidate.platform))
    return matches[0] if len(matches) == 1 else None
