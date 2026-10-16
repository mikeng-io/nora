"""`nora.agent.room` — the scope the three room-scoped tools resolve inside.

Every fixture here is shaped like the live `Ridgeline` group as `listGroups`
returns it: three member records, two of which are the same human's two Signal
accounts, and one of which is Nora herself.
"""

from __future__ import annotations

import pytest

from nora.agent.room import Room, RoomView
from nora.persons import load_persons
from nora.testing import FakeRosterSource

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="

# Nora's own account, as it appears in the group's member list.
NORA = frozenset({"+85290000100", "a1000000-0000-4000-8000-000000000000"})
MILES_PERSONAL = frozenset({"+85290000101", "a1000001-0000-4000-8000-000000000001"})
MILES_BUSINESS = frozenset({"+85290000103", "a1000002-0000-4000-8000-000000000002"})
GRANT = frozenset({"+85290000104"})
STRANGER = frozenset({"+85260000000"})

IDENTITY = """
[persons.miles_novak]
first_name = "Miles"
last_name  = "Novak"
aliases = ["俊爺", "Miles"]
[[persons.miles_novak.signal]]
label = "personal"
phone = "+85290000101"
uuid  = "a1000001-0000-4000-8000-000000000001"
[[persons.miles_novak.signal]]
label = "business"
phone = "+85290000103"
uuid  = "a1000002-0000-4000-8000-000000000002"

[persons.grant_carver]
first_name = "Grant"
last_name  = "Carver"
aliases = ["明明", "Chi"]
[[persons.grant_carver.signal]]
phone = "+85290000104"

# In the address book, NOT in the group. Every "invisible" assertion below is
# about this person.
[persons.tyler_cortez]
first_name = "Tyler"
last_name  = "Cortez"
aliases = ["阿豹", "Tyler"]
[[persons.tyler_cortez.signal]]
phone = "+85290000102"
"""


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _room(persons, *, members=(MILES_PERSONAL, MILES_BUSINESS, NORA), **kw):
    return Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: tuple(members)}),
        persons=persons, self_ids=NORA, **kw,
    )


async def test_the_room_is_the_platforms_members_resolved_to_people(persons):
    view = await _room(persons, members=(MILES_PERSONAL, GRANT, NORA)).members()
    assert view.available is True
    assert [p.name for p in view.people] == ["Grant Carver", "Miles Novak"]


async def test_two_accounts_of_one_person_are_one_person_in_the_room(persons):
    """DEC-14's baseline case, and the live group's actual shape: Miles holds
    two Signal registrations and both are members. Counting them as two people
    would report a group of three humans that contains two."""
    view = await _room(persons, members=(MILES_PERSONAL, MILES_BUSINESS, NORA)).members()
    assert [p.name for p in view.people] == ["Miles Novak"]
    assert view.unnamed == 0


async def test_nora_is_in_the_room_but_is_not_a_member_of_it(persons):
    """Her own account is in `listGroups`' member list and resolves to nobody
    in identity.toml, so without `self_ids` she would be reported as one
    account with no name — a stranger in the room, who is her."""
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    assert [p.name for p in view.people] == ["Miles Novak"]
    assert view.unnamed == 0


async def test_a_member_who_maps_to_nobody_is_counted_never_named(persons):
    """R-18: the only thing Nora holds for an unmapped member is a phone number
    or an ACI. A count is the whole of what can honestly be said about them —
    and it is also the operator's tell that identity.toml is incomplete."""
    view = await _room(persons, members=(MILES_PERSONAL, STRANGER, NORA)).members()
    assert [p.name for p in view.people] == ["Miles Novak"]
    assert view.unnamed == 1


async def test_an_unreadable_roster_is_unavailable_not_an_empty_room(persons):
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(fail=True), persons=persons, self_ids=NORA,
    )
    view = await room.members()
    assert view.available is False
    assert view.people == ()


async def test_no_roster_source_at_all_is_also_unavailable(persons):
    """`nora.app.offline` wires no roster source — there is no daemon to ask.
    Answering "nobody is here" for a wiring gap would make a misconfiguration
    indistinguishable from a quiet group (R-5)."""
    room = Room(
        platform="signal", conversation_id=GROUP, roster_source=None, persons=persons,
    )
    assert (await room.members()).available is False


async def test_a_source_that_raises_degrades_instead_of_killing_the_turn(persons):
    class Exploding:
        platform = "signal"

        async def roster(self, *, conversation_id):
            raise RuntimeError("daemon wedged")

    room = Room(
        platform="signal", conversation_id=GROUP, roster_source=Exploding(),
        persons=persons,
    )
    assert (await room.members()).available is False


async def test_the_roster_is_read_once_per_tag_however_many_tools_run(persons):
    """`group_members` then two `lookup_person` calls is three tool calls and
    must stay ONE daemon round trip — a tag already pays a model round trip per
    tool call and cannot afford a network read on each as well."""
    source = FakeRosterSource(groups={GROUP: (MILES_PERSONAL, NORA)})
    room = Room(
        platform="signal", conversation_id=GROUP, roster_source=source,
        persons=persons, self_ids=NORA,
    )
    await room.members()
    await room.scope()
    await room.members()
    assert source.calls == [GROUP]


# ---- name resolution, and the scope it happens inside -----------------------


async def test_an_alias_resolves_a_person_who_is_in_the_room(persons):
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    assert [p.person_id for p in view.find("俊爺")] == ["miles_novak"]


async def test_a_person_in_identity_toml_but_not_in_this_room_is_not_found(persons):
    """The load-bearing one. `tyler_cortez` and his alias 阿豹 are in the same
    identity.toml the room was resolved against — `Persons.resolve` would find
    him instantly — and he is invisible here because the index `find` runs
    against is built from THIS room's members alone."""
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    assert view.find("阿豹") == ()
    assert view.find("Tyler") == ()
    assert view.find("Tyler Cortez") == ()


async def test_a_person_who_exists_nowhere_is_indistinguishable_from_one_who_is_elsewhere(
    persons,
):
    """Resolve-then-filter would leak membership through this difference. The
    two calls must return the identical value, not merely both be falsy."""
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    assert view.find("阿豹") == view.find("Nobody At All") == ()


async def test_names_match_by_full_name_first_name_last_name_and_alias(persons):
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    for spelling in ("Miles Novak", "miles novak", "Miles", "Novak", "俊爺", " Miles "):
        assert [p.person_id for p in view.find(spelling)] == ["miles_novak"], spelling


async def test_a_name_wrapped_in_the_brackets_a_message_used_still_matches(persons):
    """「阿豹」 is how a name appears in a Cantonese message; asking the model to
    strip its own quotes is a rule it will sometimes forget."""
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    assert [p.person_id for p in view.find("「俊爺」")] == ["miles_novak"]


async def test_matching_is_exact_not_substring(persons):
    """Substring matching would make 「東」 resolve 阿豹 and 阿豹妹 both. A
    confident wrong answer about somebody in the room is worse than «I'm not
    sure who you mean»."""
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    assert view.find("Mik") == ()
    assert view.find("Miles Novak is here") == ()


async def test_two_people_sharing_a_nickname_come_back_as_both(persons, tmp_path):
    """Silently picking the first would attach one person's memory to the
    other's name."""
    path = tmp_path / "twins.toml"
    path.write_text(
        '[persons.a]\nname = "Ah Ming"\naliases = ["阿星"]\n'
        '[[persons.a.signal]]\nphone = "+85211111111"\n'
        '[persons.b]\nname = "Ming Chai"\naliases = ["阿星"]\n'
        '[[persons.b.signal]]\nphone = "+85222222222"\n'
    )
    view = await _room(
        load_persons(path),
        members=(frozenset({"+85211111111"}), frozenset({"+85222222222"})),
    ).members()
    assert {p.person_id for p in view.find("阿星")} == {"a", "b"}


async def test_an_empty_name_matches_nobody(persons):
    view = await _room(persons, members=(MILES_PERSONAL, NORA)).members()
    assert view.find("") == ()
    assert view.find("   ") == ()


# ---- the degraded scope ------------------------------------------------------


async def test_scope_falls_back_to_the_tagger_when_the_roster_is_unreadable(persons):
    """Nora received the tagger's message IN this conversation, so their
    membership is established by construction — a strictly narrower set than
    the real room, which is why it cannot resolve anyone outside it."""
    miles = persons.by_person["miles_novak"]
    room = Room(
        platform="signal", conversation_id=GROUP, roster_source=FakeRosterSource(fail=True),
        persons=persons, tagger=miles, self_ids=NORA,
    )
    view = await room.scope()
    assert view.available is True
    assert view.degraded is True
    assert [p.person_id for p in view.find("俊爺")] == ["miles_novak"]
    # And still nobody else, including somebody who IS in identity.toml.
    assert view.find("阿豹") == ()


async def test_the_degraded_scope_is_never_used_for_group_members(persons):
    """«Miles is the only person here» is a confident lie about a room Nora
    could not see. `members()` stays unavailable however well `scope()` copes."""
    room = Room(
        platform="signal", conversation_id=GROUP, roster_source=FakeRosterSource(fail=True),
        persons=persons, tagger=persons.by_person["miles_novak"], self_ids=NORA,
    )
    assert (await room.members()).available is False


async def test_an_unmapped_tagger_leaves_nothing_to_fall_back_to(persons):
    room = Room(
        platform="signal", conversation_id=GROUP, roster_source=FakeRosterSource(fail=True),
        persons=persons, tagger=None,
    )
    assert (await room.scope()).available is False


def test_a_view_that_is_available_and_empty_is_a_real_state():
    """Not every empty room is a broken one — the two axes are independent and
    a caller must be able to build both."""
    assert RoomView(available=True).people == ()
    assert RoomView(available=False).available is False
