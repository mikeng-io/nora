"""Resolve graph references against one snapshot of authored identity and policy."""

from collections.abc import Collection, Iterable

from nora.domain.lineage import room_key, split_room
from nora.memory.domain.admission_proposals import RoomReference
from nora.persons import Persons
from nora.policy import Policy


class AdmissionIdentity:
    def __init__(
        self,
        persons: Persons,
        policy: Policy,
        *,
        nora_accounts: Iterable[tuple[str, str]] = (),
    ) -> None:
        self._persons = persons
        self._units = frozenset(policy.unit_workspaces())
        self._rooms = policy.rooms()
        self._nora_accounts = frozenset(nora_accounts)
        self._direct = {
            platform: persons.direct_addresses(platform)
            for platform in {room.platform for room in self._rooms}
        }

    def unit(self, ref: str) -> str | None:
        return ref if ref in self._units else None

    def room(self, ref: str, unit: str, *, platform: str | None = None) -> RoomReference | None:
        named_platform, room_id = split_room(ref)
        platform = named_platform or platform
        matches = [
            room
            for room in self._rooms
            if room_id in room.ids and (platform is None or room.platform == platform)
        ]
        local = [room for room in matches if room.unit == unit]
        focus = local or matches
        if len(focus) != 1:
            return None
        selected = focus[0]
        return RoomReference(
            room_key(selected.platform, selected.primary_id),
            selected.unit,
            not selected.ids.isdisjoint(self._direct[selected.platform]),
        )

    def person(self, ref: str) -> str | None:
        person_id = self._persons.subject_id_for(ref)
        return person_id if person_id in self._persons.by_person else None

    def principal(self, ref: str) -> str | None:
        return "nora" if ref == "nora" else self.person(ref)

    def is_nora(self, platform: str | None, sender: str | None) -> bool:
        if platform is None or not sender:
            return False
        return (platform, sender) in self._nora_accounts

    def references(
        self,
        unit: str,
        *,
        rooms: Collection[str],
        persons: Collection[str],
        principals: Collection[str],
    ) -> tuple[dict[str, RoomReference | None], dict[str, str | None]]:
        room_refs = {ref: self.room(ref, unit) for ref in rooms}
        person_refs = {ref: self.person(ref) for ref in persons}
        person_refs.update((ref, self.principal(ref)) for ref in principals)
        return room_refs, person_refs
