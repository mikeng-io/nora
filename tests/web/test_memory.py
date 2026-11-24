from datetime import UTC, datetime

import pytest

from nora.agent.egress import scan_reply
from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.web.memory import build_memory_payload

MILES = PersonRef(person_id="miles", name="Miles")
ADA = PersonRef(person_id="ada", name="Ada")


def _claim(id_, text, **fields):
    return {
        "id": id_, "content": text, "part": "fact", "reasoning": "told",
        "origin_room": "private room", "state": "live",
        "asserted_at": datetime(2026, 11, 1, tzinfo=UTC),
        "prediction": None, "window_end": None, "grade": None, "evidence": [],
        "disputed_at": None, **fields,
    }


class _Claims:
    def __init__(self, claims):
        self._claims = claims
        self.reads = []

    async def __call__(self, *, unit, viewer, viewer_rooms):
        self.reads.append((unit, viewer, viewer_rooms))
        return self._claims.get((unit, viewer), ())


async def _payload(profile, *, person_id="miles", **kwargs):
    return await build_memory_payload(
        persons=Persons(by_person={p.person_id: p for p in (MILES, ADA)}, index={}),
        person_id=person_id, profile=profile, screen=scan_reply, **kwargs,
    )


@pytest.mark.parametrize("person_id", [None, "stranger"])
async def test_unknown_viewer_does_not_read_person_claims(person_id):
    profile = _Claims({("friends", "miles"): (_claim("m1", "cycles"),)})
    scopes = lambda _: (("friends", "default"),)  # noqa: E731
    assert await _payload(profile, person_id=person_id, profile_scopes_of=scopes) == []
    [account] = await _payload(profile, profile_scopes_of=scopes)
    assert account["lines"][0]["id"] == "m1"
    assert profile.reads == [("friends", "miles", frozenset())]


async def test_subject_claims_stay_in_viewers_units_and_workspaces():
    profile = _Claims({
        ("friends", "miles"): (_claim("m1", "cycles to work"),),
        ("friends", "ada"): (_claim("a1", "plays guitar"),),
        ("climbers", "miles"): (_claim("m2", "climbs on weekends"),),
    })
    scopes = lambda _: (("climbers", "ridgeline"), ("friends", "home"))  # noqa: E731
    [account] = await _payload(
        profile, workspaces_of=lambda _: ("home",), profile_scopes_of=scopes,
    )
    assert account["unit"] == "friends"
    assert [(line["id"], line["text"]) for line in account["lines"]] == [("m1", "cycles to work")]
    assert profile.reads == [("friends", "miles", frozenset())]

    both = await _payload(
        profile, workspaces_of=lambda _: ("home", "ridgeline"), profile_scopes_of=scopes,
    )
    assert [account["unit"] for account in both] == ["climbers", "friends"]
    assert {line["id"] for account in both for line in account["lines"]} == {"m1", "m2"}


async def test_awaiting_weighing_claim_keeps_an_inspectable_state_and_reference():
    profile = _Claims({("friends", "miles"): (_claim(
        "m1", "maybe vegetarian", state="awaiting_weighing", part="guess",
        prediction="orders vegetables", disputed_at=datetime(2026, 11, 2, tzinfo=UTC),
    ),)})
    [account] = await _payload(profile, profile_scopes_of=lambda _: (("friends", "default"),))
    [line] = account["lines"]
    assert (line["id"], line["origin"], line["state"], line["prediction"]) == (
        "m1", "private room", "disputed", "orders vegetables",
    )
    [dispute] = account["disputes"]
    assert dispute["claim"] == "maybe vegetarian"
    assert dispute["written_at"] > line["written_at"]


async def test_a_viewer_without_listed_units_never_queries_claims():
    profile = _Claims({("friends", "miles"): (_claim("m1", "cycles"),)})
    assert await _payload(profile, profile_scopes_of=lambda _: ()) == []
    assert profile.reads == []
    [account] = await _payload(
        profile, profile_scopes_of=lambda _: (("friends", "home"),),
    )
    assert account["lines"][0]["id"] == "m1"


async def test_retired_claim_is_marked_retired_instead_of_hidden():
    profile = _Claims({("friends", "miles"): (_claim("m1", "used to cycle", state="retired"),)})
    [account] = await _payload(profile, profile_scopes_of=lambda _: (("friends", "home"),))
    [line] = account["lines"]
    assert (line["id"], line["state"]) == ("m1", "retired")
