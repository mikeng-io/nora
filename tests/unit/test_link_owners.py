"""`nora.agent.link_owners` — the registry itself, and the two enforcement
tests `design/social-per-network/30-link-owner-registry.md` names for PR 1
("Migration" step 1): every owner names a real tool and a real parameter on
it, and the redirect shape is `nothing_found` with the exact note pinned.

PR 1 registers Instagram only (`LINK_OWNERS` has one row); X and LinkedIn are
PRs 2 and 3, so this file's matrix stays Instagram-scoped on purpose — the
negatives below (a CDN host, a shortener-shaped host, a plain page) are the
ones design 30's "What live traffic shows" names as hosts a registry must
never claim.
"""

from __future__ import annotations

import inspect
import typing

import pytest

from nora.agent.link_owners import (
    _INSTAGRAM_MOBILE_PAGE_LINK,
    _INSTAGRAM_PAGE_LINK,
    LINK_OWNERS,
    LinkInput,
    owner_of,
    redirect,
)
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore
from tests.fakes import SinkSender


class _NoStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _NoPersons:
    by_person: typing.ClassVar[dict] = {}

    def resolve(self, platform, sender_id):
        return None


def _built_calls() -> dict[str, object]:
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=_NoPersons())
    specs = ToolRegistry().specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_NoPersons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )
    return {s.name: s.call for s in specs}


# ---- owner_of: claims and negatives -----------------------------------------

_CLAIMED_PAGE = (
    "https://www.instagram.com/p/SynthPost01/",
    "https://www.instagram.com/reel/SynthReel01/?igsh=c3ludGhldGlj",
    "instagram.com/p/SynthPost01/",  # no scheme, read like a pasted link
    "https://instagram.com/some.account/",
)
_CLAIMED_MOBILE = (
    "https://m.instagram.com/reel/SynthReel01/",
    "m.instagram.com/p/SynthPost01/",
)
_UNCLAIMED = (
    "https://scontent.cdninstagram.com/v/t51/synthetic.jpg",  # the item's own CDN file
    "https://x.com/pic.jpg",
    "https://lnkd.in/abc123",  # a shortener: not its network's own page
    "https://example.com/a",
    "https://www.instagram.com/explore/",  # an Instagram host, not a page kind
)


@pytest.mark.parametrize("url", _CLAIMED_PAGE)
def test_a_page_link_is_claimed_and_readable(url):
    found = owner_of(url)
    assert found is not None, url
    owner, claim = found
    assert owner.network == "instagram"
    assert owner.tool == "instagram_read"
    assert owner.param == "link"
    assert claim.readable is True


@pytest.mark.parametrize("url", _CLAIMED_MOBILE)
def test_a_mobile_page_link_is_claimed_and_not_readable(url):
    found = owner_of(url)
    assert found is not None, url
    _owner, claim = found
    assert claim.readable is False


@pytest.mark.parametrize("url", _UNCLAIMED)
def test_a_non_page_url_is_claimed_by_no_owner(url):
    assert owner_of(url) is None, url


def test_no_url_in_the_two_matrices_is_claimed_by_two_owners():
    """Trivial with one row today; the shape this locks stays true when X and
    LinkedIn register (PRs 2/3): each url in the positive matrix is claimed by
    exactly one `LinkOwner`."""
    for url in _CLAIMED_PAGE + _CLAIMED_MOBILE:
        matches = [o for o in LINK_OWNERS if o.claim(url) is not None]
        assert len(matches) == 1, (url, matches)


# ---- redirect: the exact, pinned note ---------------------------------------

def test_redirect_picks_the_readable_note():
    link_input = LinkInput(
        "url", page_note=_INSTAGRAM_PAGE_LINK, mobile_note=_INSTAGRAM_MOBILE_PAGE_LINK,
    )
    _owner, claim = owner_of("https://www.instagram.com/p/SynthPost01/")
    out = redirect(link_input, claim)
    assert out == {"status": "nothing_found", "note": _INSTAGRAM_PAGE_LINK}


def test_redirect_picks_the_unreadable_note():
    link_input = LinkInput(
        "url", page_note=_INSTAGRAM_PAGE_LINK, mobile_note=_INSTAGRAM_MOBILE_PAGE_LINK,
    )
    _owner, claim = owner_of("https://m.instagram.com/p/SynthPost01/")
    out = redirect(link_input, claim)
    assert out == {"status": "nothing_found", "note": _INSTAGRAM_MOBILE_PAGE_LINK}


# ---- the signature test (design 30, "Tests and enforcement") ---------------

@pytest.mark.parametrize("owner", LINK_OWNERS, ids=lambda o: o.network)
def test_every_owner_names_a_built_tool_and_a_real_parameter(owner):
    """Renaming `instagram_read`'s `link` — the shape design 30 names for
    `x_read`'s, once it registers in PR 2/3 — must turn this red: a redirect
    naming a parameter that does not exist on the tool it points at is a
    dead end no test built from the fetcher's side would ever catch."""
    calls = _built_calls()
    assert owner.tool in calls, f"{owner.network}: {owner.tool!r} is not a built tool"
    signature = inspect.signature(calls[owner.tool])
    assert owner.param in signature.parameters, (
        f"{owner.network}: {owner.tool} has no parameter named {owner.param!r}"
    )
