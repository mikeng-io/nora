"""Project current managed access into the portal's existing read contracts."""

import logging
from collections.abc import Mapping
from typing import Any

from aiohttp import web

from nora.agent.whereis import Circle
from nora.domain.managed import CheckResult, PersonLifecycle
from nora.domain.managed_ports import BindingStore
from nora.domain.models import PersonRef
from nora.people import PortalScopes
from nora.persons import Persons


async def scoped_context(request: web.Request, *, scopes: PortalScopes,
                         bindings: BindingStore, viewer: str) -> Mapping[str, Any] | None:
    try:
        return await _context(request, scopes=scopes, bindings=bindings, viewer=viewer)
    except Exception as error:  # noqa: BLE001 — uncertainty never becomes an unscoped read
        logging.getLogger(__name__).error("portal projection unavailable: %s", type(error).__name__)
        return None


async def _context(request: web.Request, *, scopes: PortalScopes,
                   bindings: BindingStore, viewer: str) -> Mapping[str, Any] | None:
    access = await scopes.snapshot(viewer)
    if isinstance(access, CheckResult):
        return None
    context: dict[str, Any] = {key: value for key, value in request.app.items()
                               if isinstance(key, str)}
    people = dict(access.people)
    if request.path.startswith("/app/v1/presence"):
        circles = await _circles(context["web_presence_circles"], viewer, bindings)
        context["web_presence_circles"] = circles
        people.update({ref.person_id: ref for circle in circles for ref in circle.people})
    target = request.match_info.get("personId")
    if target:
        canonical = await bindings.canonical_person(target)
        if canonical in people:
            people[target] = people[canonical]
    context["web_persons"] = Persons(by_person=people, index={})
    context["web_rooms_of"] = lambda pid: access.rooms if pid == viewer else frozenset()
    context["web_workspaces_of"] = lambda pid: tuple(sorted({
        access.units[u] for u in access.person_units.get(pid, ()) if u in access.units}))
    context["web_profile_scopes_of"] = lambda pid: tuple(sorted(
        (u, access.units[u]) for u in access.person_units.get(pid, ()) if u in access.units))
    return context


async def _circles(source: tuple[Circle, ...], viewer: str,
                   bindings: BindingStore) -> tuple[Circle, ...]:
    result = []
    for circle in source:
        canonical = {await bindings.canonical_person(pid) for pid in circle.person_ids}
        if viewer not in canonical:
            continue
        if None in canonical:
            raise ValueError("unresolved consented circle member")
        known = {pid for pid in canonical if pid is not None}
        people = []
        for pid in sorted(known):
            person = await bindings.person(pid)
            if (person is None or person.lifecycle is not PersonLifecycle.ACTIVE
                    or await bindings.identity_held(pid) is not False):
                raise ValueError("unavailable consented circle member")
            people.append(PersonRef(pid, person.display_name or pid))
        result.append(Circle(circle.circle_id, frozenset(known), tuple(people)))
    return tuple(result)
