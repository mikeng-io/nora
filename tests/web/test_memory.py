from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.web.memory import build_memory_payload
from tests.fakes import FakeHoncho

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


def _persons(*refs: PersonRef) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index={})


async def test_returns_one_entry_per_whitelisted_person():
    honcho = FakeHoncho(
        conclusions={"miles": ["likes climbing", "works in fintech"], "ada": ["plays guitar"]},
        cards={"miles": ["Name: Miles Novak"], "ada": ["Name: Ada"]},
    )
    persons = _persons(MILES, ADA)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    by_id = {entry["person_id"]: entry for entry in payload}
    assert set(by_id) == {"miles", "ada"}
    assert by_id["miles"]["display_name"] == "Miles"
    assert by_id["miles"]["reachable"] is True
    assert by_id["miles"]["episodes"] == ["likes climbing", "works in fintech"]
    assert by_id["miles"]["identity"] == ["Name: Miles Novak"]
    assert by_id["ada"]["episodes"] == ["plays guitar"]


async def test_a_person_with_no_conclusions_or_card_is_reachable_but_empty():
    honcho = FakeHoncho(conclusions={}, cards={})
    persons = _persons(MILES)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert payload == [
        {
            "person_id": "miles", "display_name": "Miles", "reachable": True,
            "identity": [], "episodes": [],
        },
    ]


async def test_an_unreachable_honcho_is_reported_unavailable_not_empty():
    """R-27: an unreachable Honcho must never look identical to 'nothing
    known' — FakeHoncho(fail=True) mirrors the live client's unreachable
    shape (tests/fakes.py's Recollection(reachable=False)). `identity`
    still comes back `[]` (peer_card's own documented ambiguity — ports.py:
    it answers `[]` for both "down" and "knows nothing"), so `reachable`
    (sourced from `representation` alone) is the only unavailability signal
    this payload carries."""
    honcho = FakeHoncho(fail=True)
    persons = _persons(MILES)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert payload == [
        {
            "person_id": "miles", "display_name": "Miles", "reachable": False,
            "identity": [], "episodes": [],
        },
    ]


async def test_queries_honcho_as_the_ai_peer_observing_each_person():
    """Nora's MODEL OF the person, not the person's self-model — the same
    (observer=ai_peer, observed=target) shape nora/engagement/context.py's
    _recall_self/_recall_tagger_card already use."""
    honcho = FakeHoncho(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert honcho.queries == [("nora", "miles", "")]


async def test_identity_is_the_persons_own_self_scoped_peer_card_not_an_observer_pair():
    """`peer_card` takes only `peer_id` — no observer/observed pair — called
    with the PERSON's own honcho_peer_id, distinct from `representation`'s
    (ai_peer, target) shape asserted above."""
    honcho = FakeHoncho(cards={"miles": ["Name: Miles Novak", "Aliases: Miles"]})
    persons = _persons(MILES)

    payload = await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora")

    assert payload[0]["identity"] == ["Name: Miles Novak", "Aliases: Miles"]
