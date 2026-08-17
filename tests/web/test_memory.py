from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.web.memory import build_memory_payload
from tests.fakes import FakeHoncho

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


def _persons(*refs: PersonRef) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index={})


async def test_returns_one_entry_per_whitelisted_person():
    honcho = FakeHoncho(conclusions={
        "miles": ["likes climbing", "works in fintech"],
        "ada": ["plays guitar"],
    })
    persons = _persons(MILES, ADA)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    by_id = {entry["person_id"]: entry for entry in payload}
    assert set(by_id) == {"miles", "ada"}
    assert by_id["miles"]["display_name"] == "Miles"
    assert by_id["miles"]["reachable"] is True
    assert by_id["miles"]["conclusions"] == ["likes climbing", "works in fintech"]
    assert by_id["ada"]["conclusions"] == ["plays guitar"]


async def test_a_person_with_no_conclusions_is_reachable_but_empty():
    honcho = FakeHoncho(conclusions={})
    persons = _persons(MILES)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert payload == [
        {"person_id": "miles", "display_name": "Miles", "reachable": True, "conclusions": []},
    ]


async def test_an_unreachable_honcho_is_reported_unavailable_not_empty():
    """R-27: an unreachable Honcho must never look identical to 'nothing
    known' — FakeHoncho(fail=True) mirrors the live client's unreachable
    shape (tests/fakes.py's Recollection(reachable=False))."""
    honcho = FakeHoncho(fail=True)
    persons = _persons(MILES)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert payload == [
        {"person_id": "miles", "display_name": "Miles", "reachable": False, "conclusions": []},
    ]


async def test_queries_honcho_as_the_ai_peer_observing_each_person():
    """Nora's MODEL OF the person, not the person's self-model — the same
    (observer=ai_peer, observed=target) shape nora/engagement/context.py's
    _recall_self/_recall_tagger_card already use."""
    honcho = FakeHoncho(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert honcho.queries == [("nora", "miles", "")]
