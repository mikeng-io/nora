"""Selected managed account ownership at the presence write and refresh boundaries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nora.domain.managed import PersonLifecycle, Verdict
from nora.domain.managed_ports import BindingStore, IdentityRegistry


class ManagedPresenceAccounts:
    """Resolve provider member IDs without registration, grants or file fallback."""

    def __init__(self, bindings: BindingStore, registry: IdentityRegistry) -> None:
        self._bindings = bindings
        self._registry = registry

    async def _owner(self, member_id: str) -> str | None:
        account = await self._bindings.account_for("life360", "member_id", member_id)
        if account is None:
            return None
        person = await self._bindings.canonical_person(account.person_id)
        if person is None or await self._bindings.identity_held(person):
            return None
        if await self._bindings.person_lifecycle(person) is not PersonLifecycle.ACTIVE:
            return None
        state = await self._registry.identity_state(external_id=person)
        if state.verdict is Verdict.UNAVAILABLE:
            raise RuntimeError("managed presence identity is unavailable")
        return person if state.verdict is Verdict.ALLOWED else None

    @asynccontextmanager
    async def recording(self, member_id: str) -> AsyncIterator[str | None]:
        """Keep managed mutations serialized through the dependent fix write."""
        async with self._bindings.mutation():
            yield await self._owner(member_id)

    @asynccontextmanager
    async def refreshing(
        self, person_id: str, members: tuple[tuple[str, str], ...],
    ) -> AsyncIterator[tuple[str, str] | None]:
        """Keep ownership and eligibility current through the provider request."""
        async with self._bindings.mutation():
            canonical = await self._bindings.canonical_person(person_id)
            target = None
            if canonical is not None:
                for circle_id, member_id in members:
                    if await self._owner(member_id) == canonical:
                        target = (circle_id, member_id)
                        break
            yield target
