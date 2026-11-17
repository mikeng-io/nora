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
        unit: str, subject: str, people: Sequence[str | None],
    ) -> tuple[PersonRef | None, Callable[[str | None], str]]:
        """Name referenced identities in this unit, without borrowing sibling metadata."""
        access = self._access
        if access is None or access.is_file_backend:
            return None, lambda _person: "someone"
        refs: dict[str, PersonRef] = {}
        current: dict[str, PersonRef | None] = {}
        for person in sorted({subject, *(p for p in people if p is not None)}):
            canonical = await access.canonical_person(person)
            if canonical is None:
                continue
            if canonical not in current:
                current[canonical] = await access.person_ref(
                    canonical, scope=Scope(ScopeKind.UNIT, unit))
            ref = current[canonical]
            if ref is not None and ref.person_id == canonical:
                refs[person] = refs[canonical] = ref
        ref = refs.get(subject)
        if ref is None or ref.person_id != subject:
            raise RuntimeError("profile subject changed or is unavailable")
        def name_of(person: str | None) -> str:
            found = refs.get(person) if person is not None else None
            return found.name if found is not None else "someone"

        return ref, name_of

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
