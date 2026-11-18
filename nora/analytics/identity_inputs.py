"""Selected identity projections for one analytics pass; Record owns historical circles."""

from collections.abc import Mapping
from dataclasses import dataclass

from nora.access import IdentityAccess
from nora.analytics.runner import Subject
from nora.analytics.units import PersonScope
from nora.domain.managed import MembershipState, PersonLifecycle
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.rollups import UnitRooms
from nora.policy import Policy


@dataclass(frozen=True)
class AnalyticsIdentity:
    subjects: tuple[Subject, ...]
    units: tuple[UnitRooms, ...]
    people: Mapping[str, PersonScope]
    opted_out: frozenset[str]


class ManagedAnalyticsInputs:
    """Publish one complete selected projection, or invalidate it on any failed refresh."""

    def __init__(self, access: IdentityAccess, policy: Policy, presence: PresenceStore) -> None:
        self._access, self._policy, self._presence = access, policy, presence
        self._current: AnalyticsIdentity | None = None

    @property
    def current(self) -> AnalyticsIdentity:
        if self._current is None:
            raise RuntimeError("analytics identity projection is unavailable")
        return self._current

    async def refresh(self) -> None:
        self._current = None
        access = self._access
        families: dict[str, tuple[str, ...]] = {}
        off: set[str] = set()
        for ref in await access.person_refs():
            if await access.identity_held(ref.person_id):
                continue
            ids = await access.identity_ids(ref.person_id)
            if ids is None or ids[0] != ref.person_id:
                raise RuntimeError("analytics identity changed during refresh")
            families[ref.person_id] = ids
            if await access.social_off(ref.person_id):
                off.update(ids)
        circles = await self._presence.recorded_circles([
            pid for ids in families.values() for pid in ids])
        people = {subject: PersonScope(subject=subject,
            circles=tuple(sorted({circle for pid in ids for circle in circles.get(pid, ())})),
            former_ids=ids[1:]) for subject, ids in families.items()}
        subjects = tuple(Subject(subject_id=p.subject,
            person_ids=families[p.subject], circle_ids=p.circles)
            for p in people.values() if p.circles)
        rooms: dict[str, set[tuple[str, str]]] = {}
        members: dict[str, set[str]] = {}
        for convo in self._policy.conversations:
            rooms.setdefault(convo.unit, set()).update((convo.platform, cid) for cid in convo.ids)
            members.setdefault(convo.unit, set())
            if convo.primary_id:
                members[convo.unit].update(p.person_id
                    for p in await access.room_refs(convo.platform, convo.primary_id))
        units = []
        for unit in sorted(rooms):
            members[unit].update(m.person_id for m in await access.members_of(
                Scope(ScopeKind.UNIT, unit)) if m.state is MembershipState.MEMBER)
            selected = set()
            for person in members[unit]:
                canonical = await access.canonical_person(person)
                if canonical in people:
                    selected.add(canonical)
                elif not await access.identity_held(person):
                    state = await access.person_lifecycle(person)
                    if state is None or state is PersonLifecycle.ACTIVE:
                        raise RuntimeError("analytics membership changed during refresh")
            accounts = {(platform, value): person
                for person in sorted(selected)
                for platform in sorted({p for p, _ in rooms[unit]})
                for value in await access.platform_ids(platform, person)}
            units.append(UnitRooms(unit=unit, rooms=tuple(sorted(rooms[unit])),
                                   members=tuple(sorted(selected)), accounts=accounts))
        self._current = AnalyticsIdentity(subjects, tuple(units), people, frozenset(off))
