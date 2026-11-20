"""Resolve and check explicitly scripted room slots in admission tests."""

from collections.abc import Collection, Mapping


class FakeRoomSlots:
    def __init__(self, rooms: Mapping[tuple[str, str], str], held: Collection[str] = ()) -> None:
        self._rooms = rooms
        self._held = held

    def resolve(self, room: str, unit: str) -> str | None:
        return self._rooms.get((unit, room))

    def held(self, room: str) -> bool:
        return room in self._held
