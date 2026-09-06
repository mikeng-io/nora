from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.testing import FakeMemoryStore
from nora.web.memory import build_memory_payload

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


def _persons(*refs: PersonRef) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index={})


async def test_returns_only_the_callers_own_entry_not_everyone_elses():
    """User-scoped (operator, 2026-08-19): a caller resolved to `miles` must
    never see `ada`'s memory, reversing the original v1 "showcase everyone
    to everyone" design (2026-08-17-nora-web-design.md §2)."""
    honcho = FakeMemoryStore(
        conclusions={"miles": ["likes climbing", "works in fintech"], "ada": ["plays guitar"]},
        cards={"miles": ["Name: Miles Novak"], "ada": ["Name: Ada"]},
    )
    persons = _persons(MILES, ADA)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert [entry["person_id"] for entry in payload] == ["miles"]
    assert payload[0]["display_name"] == "Miles"
    assert payload[0]["reachable"] is True
    assert payload[0]["episodes"] == ["likes climbing", "works in fintech"]
    assert payload[0]["identity"] == ["Name: Miles Novak"]


async def test_a_missing_person_id_returns_empty_not_everyone():
    """Fail-closed (R-5's shape): no resolved identity must never fall back
    to showing every whitelisted person's data."""
    honcho = FakeMemoryStore(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id=None,
    )

    assert payload == []


async def test_an_unrecognized_person_id_returns_empty_not_an_error():
    honcho = FakeMemoryStore(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="stranger",
    )

    assert payload == []


async def test_a_person_with_no_conclusions_or_card_is_reachable_but_empty():
    honcho = FakeMemoryStore(conclusions={}, cards={})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert payload == [
        {
            "person_id": "miles", "display_name": "Miles", "reachable": True,
            "identity": [], "episodes": [],
        },
    ]


async def test_an_unreachable_honcho_is_reported_unavailable_not_empty():
    """R-27: an unreachable Honcho must never look identical to 'nothing
    known' — FakeMemoryStore(fail=True) mirrors the live client's unreachable
    shape (tests/fakes.py's Recollection(reachable=False)). `identity`
    still comes back `[]` (peer_card's own documented ambiguity — ports.py:
    it answers `[]` for both "down" and "knows nothing"), so `reachable`
    (sourced from `representation` alone) is the only unavailability signal
    this payload carries."""
    honcho = FakeMemoryStore(fail=True)
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert payload == [
        {
            "person_id": "miles", "display_name": "Miles", "reachable": False,
            "identity": [], "episodes": [],
        },
    ]


async def test_queries_honcho_as_the_ai_peer_observing_the_caller():
    """Nora's MODEL OF the person, not the person's self-model — the same
    (observer=ai_peer, observed=target) shape nora/engagement/context.py's
    _recall_self/_recall_tagger_card already use."""
    honcho = FakeMemoryStore(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora", person_id="miles")

    assert honcho.queries == [("nora", "miles", "")]


async def test_identity_is_the_persons_own_self_scoped_peer_card_not_an_observer_pair():
    """`peer_card` takes only `peer_id` — no observer/observed pair — called
    with the PERSON's own honcho_peer_id, distinct from `representation`'s
    (ai_peer, target) shape asserted above."""
    honcho = FakeMemoryStore(cards={"miles": ["Name: Miles Novak", "Aliases: Miles"]})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert payload[0]["identity"] == ["Name: Miles Novak", "Aliases: Miles"]
