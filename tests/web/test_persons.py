"""The identity roster and person profiles."""

from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.web.persons import build_person_profile_payload, build_persons_roster_payload

MILES = PersonRef(person_id="miles_novak", name="Miles Novak", honcho_peer_id="miles_novak")


def _persons(*refs: PersonRef) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index={})


def test_roster_lists_everyone_sorted_by_display_name():
    persons = _persons(
        PersonRef(person_id="zack", name="Zack", honcho_peer_id="zack"),
        PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada"),
    )

    roster = build_persons_roster_payload(persons=persons)

    assert roster == [
        {"personId": "ada", "displayName": "Ada"},
        {"personId": "zack", "displayName": "Zack"},
    ]


async def test_profile_is_none_for_an_unrecognized_person():
    persons = _persons(MILES)

    profile = await build_person_profile_payload(
        persons=persons, person_id="stranger",
    )

    assert profile is None


async def test_profile_contains_the_known_identity():
    profile = await build_person_profile_payload(
        persons=_persons(MILES), person_id="miles_novak",
    )
    assert profile == {"personId": "miles_novak", "displayName": "Miles Novak"}
