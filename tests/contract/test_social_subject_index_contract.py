"""One suite, every implementation of `SocialSubjectIndex`.

The port has two implementations, `PersonsSubjectIndex` and
`FakeSocialSubjectIndex`, so both are held to the same answers here (AGENTS.md:
a port never gains a second implementation without a shared contract suite).
Every router test that drives the tier ladder through the fake is only
evidence about production if the fake answers the way production does, and
this file is what says it does.

Each arm is built over the SAME world, from its own inputs: `jordan` holds one
igid and is listed in a room; `sam` holds one igid and is in no room; nobody
holds `19999999`. Every id is synthetic.
"""

from __future__ import annotations

import inspect

import pytest

from nora.domain.ports import SocialSubjectIndex
from nora.infra.social import PersonsSubjectIndex
from nora.persons import load_persons
from nora.policy import Conversation, Policy
from nora.testing import FakeSocialSubjectIndex

_JORDAN, _SAM, _NOBODY = "10000001", "10000004", "19999999"


def _persons_arm(tmp_path) -> PersonsSubjectIndex:
    p = tmp_path / "identity.toml"
    p.write_text(
        '[persons.jordan]\nname = "Jordan"\n'
        f'[[persons.jordan.instagram]]\nfbid = "100000000000001"\nigid = "{_JORDAN}"\n'
        '[persons.sam]\nname = "Sam"\n'
        f'[[persons.sam.instagram]]\nfbid = "100000000000004"\nigid = "{_SAM}"\n'
    )
    policy = Policy(conversations=(
        Conversation(platform="signal", ids=frozenset({"g"}), persons=frozenset({"jordan"})),
    ))
    return PersonsSubjectIndex(load_persons(p), policy)


class _BrokenPersons:
    """A person index that cannot be read at all — every lookup raises."""

    def instagram_igids(self, person_id):
        raise RuntimeError("index unreadable")

    def resolve(self, platform, sender_id):
        raise RuntimeError("index unreadable")


class _BrokenPolicy:
    @property
    def conversations(self):
        raise RuntimeError("policy unreadable")


@pytest.fixture(params=["fake", "persons"])
def index(request, tmp_path):
    if request.param == "fake":
        return FakeSocialSubjectIndex(
            {"jordan": _JORDAN, "sam": _SAM}, out_of_reach=frozenset({"sam"}),
        )
    return _persons_arm(tmp_path)


@pytest.fixture(params=["fake", "persons"])
def broken(request):
    if request.param == "fake":
        return FakeSocialSubjectIndex({"jordan": _JORDAN}, unavailable=True)
    return PersonsSubjectIndex(_BrokenPersons(), _BrokenPolicy())  # type: ignore[arg-type]


def test_both_satisfy_the_port():
    assert isinstance(FakeSocialSubjectIndex(), SocialSubjectIndex)
    assert issubclass(PersonsSubjectIndex, SocialSubjectIndex)


def test_every_method_is_keyword_only():
    """`person_id` and `igid` are both strings, and the reverse lookup's whole
    purpose is keeping the two apart; a positional call that swapped them
    would typecheck."""
    for impl in (SocialSubjectIndex, PersonsSubjectIndex, FakeSocialSubjectIndex):
        for name, arg in (
            ("igids_for", "person_id"), ("is_tier_zero", "person_id"),
            ("person_for_igid", "igid"),
        ):
            params = inspect.signature(getattr(impl, name)).parameters
            assert params[arg].kind is inspect.Parameter.KEYWORD_ONLY, (impl, name)


async def test_a_listed_person_resolves_and_is_in_reach(index):
    assert await index.igids_for(person_id="jordan") == frozenset({_JORDAN})
    assert await index.is_tier_zero(person_id="jordan") is True


async def test_a_person_in_no_room_resolves_and_is_out_of_reach(index):
    """`refused`, not `ambiguous`: Nora knows exactly who this is."""
    assert await index.igids_for(person_id="sam") == frozenset({_SAM})
    assert await index.is_tier_zero(person_id="sam") is False


async def test_an_unknown_person_resolves_to_nothing_and_is_out_of_reach(index):
    # EMPTY, not `None`: the index was read and holds nothing for them.
    assert await index.igids_for(person_id="nobody") == frozenset()
    assert await index.is_tier_zero(person_id="nobody") is False


async def test_the_reverse_lookup_names_the_owner_or_nobody(index):
    assert await index.person_for_igid(igid=_JORDAN) == "jordan"
    assert await index.person_for_igid(igid=_SAM) == "sam"
    assert await index.person_for_igid(igid=_NOBODY) is None


async def test_an_unreadable_index_fails_closed_and_never_raises(broken):
    """A broken index costs a read that should have happened; it never grants
    one that should not have."""
    # `None` — «could not read» — never the empty set, which would claim the
    # index was read and holds no account.
    assert await broken.igids_for(person_id="jordan") is None
    assert await broken.is_tier_zero(person_id="jordan") is False
    assert await broken.person_for_igid(igid=_JORDAN) is None
