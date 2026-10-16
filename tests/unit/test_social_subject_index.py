"""`PersonsSubjectIndex`'s own rules.

The answers it shares with the fake are in
`tests/contract/test_social_subject_index_contract.py`. What is here is what
only the production index can get wrong, because only it reads the two real
files: a `persons = "*"` room, a friend with two Instagram accounts, and the
kind-blind reverse index. Every id is synthetic.
"""

from __future__ import annotations

from nora.infra.social import PersonsSubjectIndex
from nora.persons import load_persons
from nora.policy import Conversation, Policy


def _persons(tmp_path):
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.jordan]\nname = "Jordan"\n'
        '[[persons.jordan.instagram]]\nfbid = "100000000000001"\nigid = "10000001"\n'
        # Two accounts: a personal/work pair, told apart by `label` only.
        '[persons.robin]\nname = "Robin"\n'
        '[[persons.robin.instagram]]\n'
        'label = "personal"\nfbid = "100000000000002"\nigid = "10000002"\n'
        '[[persons.robin.instagram]]\n'
        'label = "work"\nfbid = "100000000000003"\nigid = "10000003"\n'
        # In identity.toml, in NO listed room: only in the open one below.
        '[persons.sam]\nname = "Sam"\n'
        '[[persons.sam.instagram]]\nfbid = "100000000000004"\nigid = "10000004"\n'
    )
    return load_persons(p)


def _room(persons: frozenset[str] | None, room_id: str) -> Conversation:
    return Conversation(platform="signal", ids=frozenset({room_id}), persons=persons)


def _policy(*rooms: Conversation) -> Policy:
    return Policy(conversations=rooms)


async def test_a_star_room_does_not_make_anyone_tier_zero(tmp_path):
    """`persons = "*"` admits anyone and asserts nobody's membership.
    Counting it would make everyone in identity.toml Tier 0 the moment the
    operator opened one room — a reach decision nobody made."""
    index = PersonsSubjectIndex(
        _persons(tmp_path),
        _policy(_room(frozenset({"jordan"}), "listed"), _room(None, "open")),
    )
    assert await index.is_tier_zero(person_id="sam") is False
    # Positive control: the SAME index says yes for a person a room lists,
    # so the `False` above is the star rule and not an index that says no.
    assert await index.is_tier_zero(person_id="jordan") is True


async def test_a_policy_with_only_star_rooms_has_no_tier_zero_at_all(tmp_path):
    index = PersonsSubjectIndex(_persons(tmp_path), _policy(_room(None, "open")))
    assert await index.is_tier_zero(person_id="jordan") is False


async def test_two_accounts_are_returned_as_two_never_a_pick(tmp_path):
    """Nothing in a `person_id` says which of a friend's accounts was meant,
    and reading the wrong one reads a different human, so the index hands back BOTH
    and the router answers `ambiguous`. Collapsing them to one here, or to
    `None`, is what this pins against."""
    index = PersonsSubjectIndex(
        _persons(tmp_path), _policy(_room(frozenset({"jordan", "robin"}), "g")),
    )
    assert await index.igids_for(person_id="robin") == frozenset({"10000002", "10000003"})
    assert await index.igids_for(person_id="jordan") == frozenset({"10000001"})


async def test_no_account_on_file_is_the_empty_set_not_none(tmp_path):
    """Empty is «no account on file» (`nothing_found`); `None` is «I could not
    read the index» (`unavailable`). They are different sentences."""
    index = PersonsSubjectIndex(_persons(tmp_path), _policy())
    assert await index.igids_for(person_id="nobody") == frozenset()


async def test_the_forward_lookup_never_answers_the_fbid(tmp_path):
    index = PersonsSubjectIndex(_persons(tmp_path), _policy())
    assert await index.igids_for(person_id="jordan") == frozenset({"10000001"})
    assert "100000000000001" not in (await index.igids_for(person_id="jordan") or ())


async def test_each_of_two_accounts_maps_back_to_its_owner(tmp_path):
    """The link path: a link names ONE account, so the reverse lookup answers
    the owner of either — where the forward lookup is `ambiguous`."""
    index = PersonsSubjectIndex(_persons(tmp_path), _policy())
    assert await index.person_for_igid(igid="10000002") == "robin"
    assert await index.person_for_igid(igid="10000003") == "robin"


async def test_an_unmapped_igid_belongs_to_nobody(tmp_path):
    index = PersonsSubjectIndex(_persons(tmp_path), _policy())
    assert await index.person_for_igid(igid="19999999") is None


async def test_an_fbid_is_not_accepted_as_an_igid_in_reverse(tmp_path):
    """`Persons.resolve` reads the kind-blind index, where the messaging fbid
    resolves to its owner too. Handed to the link path it would authorize a
    story read on an id that is not an igid at all."""
    persons = _persons(tmp_path)
    assert persons.resolve("instagram", "100000000000001") is not None   # the trap is real
    index = PersonsSubjectIndex(persons, _policy())
    assert await index.person_for_igid(igid="100000000000001") is None
