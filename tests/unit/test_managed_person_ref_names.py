"""A managed person's `PersonRef` keeps the display name as `name` and nothing else.

`PersonRef` is also built positionally, so a stale extra argument would not raise: it
would land in `first_name`. The display name is distinct from the id here so a swap shows.
"""

from nora.agent.whereis import Circle
from nora.domain.managed import ManagedPerson, PersonLifecycle
from nora.domain.models import PersonRef
from nora.people.app.portal_access import PortalScopes
from nora.testing.managed import FakeBindingStore
from nora.web.managed_scope import _circles

_ID = "quill"
_DISPLAY = "Quillon Wright"


def _store(display_name: str) -> FakeBindingStore:
    store = FakeBindingStore()
    store.people[_ID] = ManagedPerson(
        person_id=_ID, lifecycle=PersonLifecycle.ACTIVE, display_name=display_name)
    return store


async def test_portal_scope_person_is_named_by_display_name_only():
    people: dict[str, PersonRef] = {}
    await PortalScopes(_store(_DISPLAY), None, None)._person(_ID, people)
    assert people[_ID] == PersonRef(person_id=_ID, name=_DISPLAY)
    assert (people[_ID].name, people[_ID].first_name) == (_DISPLAY, "")


async def test_portal_scope_person_without_a_display_name_is_named_by_id():
    people: dict[str, PersonRef] = {}
    await PortalScopes(_store(""), None, None)._person(_ID, people)
    assert (people[_ID].name, people[_ID].first_name) == (_ID, "")


async def test_consented_circle_member_is_named_by_display_name_only():
    source = (Circle("circle-1", frozenset({_ID}), ()),)
    (circle,) = await _circles(source, _ID, _store(_DISPLAY))
    assert circle.people == (PersonRef(person_id=_ID, name=_DISPLAY),)
    assert (circle.people[0].name, circle.people[0].first_name) == (_DISPLAY, "")
