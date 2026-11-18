"""Selected identity names and social consent for background input assembly."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from nora.domain.management import Scope, ScopeKind
from nora.domain.models import PersonRef
from nora.domain.profile_inputs import PersonSourceRevision
from nora.domain.social_stream import PersonSocialItem

if TYPE_CHECKING:
    from nora.access import IdentityAccess


class BackgroundIdentity:
    """The selected identity view held by one background adaptor."""

    def __init__(self, access: IdentityAccess | None) -> None:
        self._access = access

    @property
    def managed(self) -> bool:
        return self._access is not None and not self._access.is_file_backend

    async def names(
        self, *,
        unit: str, subject: str | None, people: Sequence[str | None], scope: Scope | None = None,
    ) -> tuple[PersonRef | None, Callable[[str | None], str]]:
        """Name referenced identities in this unit, without borrowing sibling metadata."""
        access = self._access
        if access is None or access.is_file_backend:
            return None, lambda _person: "someone"
        refs: dict[str, PersonRef] = {}
        current: dict[str, PersonRef | None] = {}
        for person in sorted({p for p in (subject, *people) if p is not None}):
            canonical = await access.canonical_person(person)
            if canonical is None:
                continue
            if canonical not in current:
                current[canonical] = await access.person_ref(
                    canonical, scope=scope or Scope(ScopeKind.UNIT, unit))
            ref = current[canonical]
            if ref is not None and ref.person_id == canonical:
                refs[person] = refs[canonical] = ref
        ref = refs.get(subject) if subject is not None else None
        if subject is not None and (ref is None or ref.person_id != subject):
            raise RuntimeError("profile subject changed or is unavailable")
        def name_of(person: str | None) -> str:
            found = refs.get(person) if person is not None else None
            return found.name if found is not None else "someone"

        return ref, name_of

    async def members(self, rooms: Sequence[tuple[str, str]]) -> tuple[str, ...] | None:
        """Current selected room members; None delegates file mode to its existing mapping."""
        if not self.managed:
            return None
        if self._access is None:
            raise RuntimeError("selected identity is unavailable")
        people: set[str] = set()
        for platform, room in sorted(set(rooms)):
            people.update(p.person_id for p in await self._access.room_refs(platform, room))
        return tuple(sorted(people))

    async def social_ids(self, person: str) -> tuple[str, ...]:
        if self._access is None:
            return (person,)
        ids = await self._access.identity_ids(person)
        if ids is None:
            raise RuntimeError("identity family is unavailable")
        return () if await self._access.social_off(person) else ids

    async def collective_batch(
        self, rooms: Sequence[tuple[str, str]], batch: Sequence[Any],
    ) -> Sequence[Any]:
        """Held social evidence obeys current membership and consent before rendering."""
        if self._access is None:
            return batch
        members = await self.members(rooms)
        allowed: dict[str, bool] = {}
        out = []
        for item in batch:
            if isinstance(item, PersonSourceRevision) and item.domain == "social":
                person = item.subject
                if person is None:
                    continue
                if person not in allowed:
                    canonical = await self._access.canonical_person(person)
                    allowed[person] = (canonical is not None and
                        (members is None or canonical in members) and
                        not await self._access.social_off(person))
                if not allowed[person]:
                    continue
            out.append(item)
        return out

    async def require_collective_social(
        self, rooms: Sequence[tuple[str, str]], items: Sequence[Any],
    ) -> None:
        if items and len(await self.collective_batch(rooms, items)) != len(items):
            raise RuntimeError("social eligibility changed during collective input")

    async def social_batch(
        self, person: str, batch: Sequence[Any],
    ) -> Sequence[Any]:
        """Exclude opted-out social data, including previously acquired history batches."""
        access = self._access
        def social(item: Any) -> bool:
            return isinstance(item, PersonSocialItem) or (
                isinstance(item, PersonSourceRevision) and item.domain == "social")

        if access is None or not any(social(item) for item in batch):
            return batch
        if not await access.social_off(person):
            return batch
        return [item for item in batch if not social(item)]

    async def require_social(
        self, person: str, items: Sequence[Any],
    ) -> None:
        """Revalidate social consent after evidence awaits, before registering model input."""
        access = self._access
        if items and access is not None and await access.social_off(person):
            raise RuntimeError("social preference changed during background input")
