"""A request's portal scope comes from current managed permits and Nora topology."""

import logging
from dataclasses import dataclass
from types import MappingProxyType

from nora.domain.managed import (
    CheckResult,
    PersonLifecycle,
    Verdict,
    check_unavailable,
    split_room_scope_id,
)
from nora.domain.managed_ports import BindingStore, TopologySource
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import PersonRef
from nora.people.app.managed_authority import AuthorityService


@dataclass(frozen=True)
class PortalAccess:
    people: MappingProxyType[str, PersonRef]
    rooms: frozenset[tuple[str, str]]
    units: MappingProxyType[str, str]
    person_units: MappingProxyType[str, frozenset[str]]


class PortalScopes:
    def __init__(self, bindings: BindingStore, topology: TopologySource,
                 authority: AuthorityService) -> None:
        self._bindings = bindings
        self._topology = topology
        self._authority = authority

    async def snapshot(self, viewer: str) -> PortalAccess | CheckResult:
        try:
            return await self._snapshot(viewer)
        except Exception as error:  # noqa: BLE001 — never substitute a file roster for missing authority
            logging.getLogger(__name__).error("portal scope unavailable: %s", type(error).__name__)
            return check_unavailable("portal-scope-unavailable")

    async def _snapshot(self, viewer: str) -> PortalAccess:
        scopes = await self._topology.scopes()
        rooms: set[tuple[str, str]] = set()
        units: dict[str, str] = {}
        visible: set[Scope] = set()
        parents: dict[Scope, Scope | None] = {}
        for scope in scopes:
            if scope.kind is ScopeKind.WORKSPACE:
                continue
            parents[scope] = await self._topology.parent_of(scope)
            result = await self._authority.check(scope=scope, person_id=viewer, permit="view")
            if result.verdict not in {Verdict.ALLOWED, Verdict.DENIED}:
                raise ValueError("scope authority is unavailable")
            if not result.allowed:
                continue
            visible.add(scope)
            if scope.kind is ScopeKind.ROOM:
                rooms.add(split_room_scope_id(scope.id))
                parent = parents[scope]
                if parent is not None:
                    if parent.kind is not ScopeKind.UNIT or parent not in scopes:
                        raise ValueError("invalid room containment")
                    visible.add(parent)
        for unit_scope in tuple(visible):
            if unit_scope.kind is not ScopeKind.UNIT:
                continue
            workspace = await self._topology.parent_of(unit_scope)
            if (workspace is None or workspace.kind is not ScopeKind.WORKSPACE
                    or workspace not in scopes):
                raise ValueError("invalid unit containment")
            units[unit_scope.id] = workspace.id
        people: dict[str, PersonRef] = {}
        person_units: dict[str, set[str]] = {viewer: set(units)}
        await self._person(viewer, people)
        for scope in visible:
            parent = parents.get(scope)
            unit = scope.id if scope.kind is ScopeKind.UNIT else (
                parent.id if parent is not None else None)
            for member in await self._bindings.members_of(scope):
                canonical = await self._bindings.canonical_person(member.person_id)
                if canonical is None:
                    raise ValueError("unresolved scoped member")
                await self._person(canonical, people)
                if unit is not None and canonical in people:
                    person_units.setdefault(canonical, set()).add(unit)
        return PortalAccess(MappingProxyType(people), frozenset(rooms), MappingProxyType(units),
            MappingProxyType({pid: frozenset(value) for pid, value in person_units.items()}))

    async def _person(self, person_id: str, people: dict[str, PersonRef]) -> None:
        if person_id in people:
            return
        person = await self._bindings.person(person_id)
        if person is None:
            raise ValueError("missing scoped person")
        if await self._bindings.identity_held(person_id) is not False:
            raise ValueError("identity publication incomplete")
        if person.lifecycle is PersonLifecycle.ACTIVE:
            people[person_id] = PersonRef(person_id, person.display_name or person_id)
