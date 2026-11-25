"""Resolve graph references through the selected identity backend and room topology."""

from collections.abc import Collection, Iterable

from nora.access import IdentityAccess
from nora.domain.lineage import room_key, split_room
from nora.memory.domain.admission_ports import AdmissionUnavailableError
from nora.memory.domain.admission_proposals import RoomReference
from nora.persons import Persons
from nora.policy import Policy


class AdmissionIdentity:
    def __init__(
        self,
        persons: Persons | None,
        policy: Policy,
        *,
        access: IdentityAccess | None = None,
        nora_accounts: Iterable[tuple[str, str]] = (),
    ) -> None:
        if access is None:
            if persons is None:
                raise ValueError("admission requires a selected identity backend")
            access = IdentityAccess.for_files(persons, policy)
        self._access = access
        self._units = frozenset(policy.unit_workspaces())
        self._rooms = policy.rooms()
        self._nora_accounts = frozenset(nora_accounts)

    def resolve_admission_unit(self, ref: str) -> str | None:
        return ref if ref in self._units else None

    async def room(
        self, ref: str, unit: str, *, platform: str | None = None,
    ) -> RoomReference | None:
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
        try:
            direct = await self._access.direct_addresses(selected.platform)
        except Exception as exc:
            raise AdmissionUnavailableError(commit_uncertain=False) from exc
        return RoomReference(
            room_key(selected.platform, selected.primary_id),
            selected.unit,
            not selected.ids.isdisjoint(direct),
        )

    async def identity(self, ref: str) -> str | None:
        try:
            person = await self._access.person(ref)
        except Exception as exc:
            raise AdmissionUnavailableError(commit_uncertain=False) from exc
        return person.person_id if person is not None else None

    def is_nora(self, platform: str | None, sender: str | None) -> bool:
        if platform is None or not sender:
            return False
        return (platform, sender) in self._nora_accounts

    async def references(
        self,
        unit: str,
        *,
        rooms: Collection[str],
        identities: Collection[str],
        principals: Collection[str],
    ) -> tuple[dict[str, RoomReference | None], dict[str, str | None]]:
        room_refs = {ref: await self.room(ref, unit) for ref in rooms}
        identity_refs = {ref: await self.identity(ref) for ref in set(identities)}
        for ref in set(principals):
            identity_refs[ref] = "nora" if ref == "nora" else await self.identity(ref)
        return room_refs, identity_refs
