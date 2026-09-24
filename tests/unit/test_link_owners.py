"""`nora.agent.link_owners` — the registry itself, and the enforcement tests
`design/social-per-network/30-link-owner-registry.md` names for PR 1
("Migration" step 1): every owner names a real tool and a real parameter on
it, the redirect shape is `nothing_found` with the exact note pinned, the
note is COMPOSED from owner and fetcher slots rather than fetcher-held
literal text, and registering a network touches no fetcher.

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
    _WEB_FETCH_INSTAGRAM_MOBILE_PAGE,
    _WEB_FETCH_INSTAGRAM_PAGE,
    LINK_OWNERS,
    LinkClaim,
    LinkOwner,
    owner_of,
    redirect,
)
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOLS
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


def _built_specs():
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=_NoPersons())
    return ToolRegistry().specs(
        store=_NoStore(), conversation_id="g1", conversation_type="group",
        persons=_NoPersons(), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room,
    )


def _built_calls() -> dict[str, object]:
    return {s.name: s.call for s in _built_specs()}


# ---- owner_of: claims and negatives -----------------------------------------

_INSTAGRAM_PAGE_URL = "https://www.instagram.com/p/SynthPost01/"
_INSTAGRAM_MOBILE_URL = "https://m.instagram.com/reel/SynthReel01/"

_CLAIMED_PAGE = (
    _INSTAGRAM_PAGE_URL,
    "https://www.instagram.com/reel/SynthReel01/?igsh=c3ludGhldGlj",
    "instagram.com/p/SynthPost01/",  # no scheme, read like a pasted link
    "https://instagram.com/some.account/",
)
_CLAIMED_MOBILE = (
    _INSTAGRAM_MOBILE_URL,
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
    assert claim.unreadable_template, "an unreadable claim carries its own explanation"


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


# ---- redirect: composed from owner + fetcher slots, never a literal --------

def test_redirect_composes_the_readable_note_from_owner_and_fetcher_slots():
    owner, claim = owner_of(_INSTAGRAM_PAGE_URL)
    link_input = NATIVE_TOOLS["web_fetch"].link_inputs[0]
    out = redirect(owner, claim, link_input)
    assert out == {"status": "nothing_found", "note": _WEB_FETCH_INSTAGRAM_PAGE}


def test_redirect_composes_the_unreadable_note_from_owner_and_fetcher_slots():
    owner, claim = owner_of(_INSTAGRAM_MOBILE_URL)
    link_input = NATIVE_TOOLS["web_fetch"].link_inputs[0]
    out = redirect(owner, claim, link_input)
    assert out == {"status": "nothing_found", "note": _WEB_FETCH_INSTAGRAM_MOBILE_PAGE}


def test_the_composed_note_matches_every_live_string():
    """The coordinator's own requirement: the four strings `web_fetch` and
    `media_fetch` returned before design 30 (`_INSTAGRAM_PAGE_LINK`,
    `_INSTAGRAM_MOBILE_PAGE_LINK`, `_WEB_FETCH_INSTAGRAM_PAGE`,
    `_WEB_FETCH_INSTAGRAM_MOBILE_PAGE`, all still imported by
    `tests/test_media_fetch.py`/`tests/unit/test_social_tool_notes.py`
    unchanged) are reproduced byte-for-byte by composing `owner.label`/
    `.tool`/`.param` and each fetcher's OWN `link_inputs` entry off
    `NATIVE_TOOLS` — never by a fetcher-held literal."""
    readable_owner, readable_claim = owner_of(_INSTAGRAM_PAGE_URL)
    unreadable_owner, unreadable_claim = owner_of(_INSTAGRAM_MOBILE_URL)
    media_link_input = NATIVE_TOOLS["media_fetch"].link_inputs[0]
    web_link_input = NATIVE_TOOLS["web_fetch"].link_inputs[0]

    cases = {
        _INSTAGRAM_PAGE_LINK: (readable_owner, readable_claim, media_link_input),
        _WEB_FETCH_INSTAGRAM_PAGE: (readable_owner, readable_claim, web_link_input),
        _INSTAGRAM_MOBILE_PAGE_LINK: (unreadable_owner, unreadable_claim, media_link_input),
        _WEB_FETCH_INSTAGRAM_MOBILE_PAGE: (unreadable_owner, unreadable_claim, web_link_input),
    }
    for expected, (owner, claim, link_input) in cases.items():
        composed = redirect(owner, claim, link_input)["note"]
        assert composed == expected, (expected, composed)


# ---- adding a network touches no fetcher (the operator's own requirement) --

def _fake_claim(text: str) -> LinkClaim | None:
    if "fakenet.example" not in text:
        return None
    return LinkClaim(network="fakenet", kind="page", readable=True)


_FAKE_OWNER = LinkOwner(
    network="fakenet", tool="fake_read", param="handle",
    label="a fakenet page", claim=_fake_claim,
)
_FAKE_URL = "https://fakenet.example/p/1"


async def test_a_registered_network_needs_no_fetcher_change(monkeypatch):
    """Register ONE new `LinkOwner` — nothing else — and both `web_fetch` and
    `media_fetch` redirect its link, naming the fake tool and parameter. This
    is the property the operator asked for directly: adding a network is one
    `LinkOwner` row, and touches neither fetcher's declaration nor its body."""
    monkeypatch.setattr("nora.agent.link_owners.LINK_OWNERS", (*LINK_OWNERS, _FAKE_OWNER))
    calls = _built_calls()

    web_result = await calls["web_fetch"](url=_FAKE_URL)
    assert web_result["status"] == "nothing_found"
    assert "fake_read" in web_result["note"]
    assert "handle" in web_result["note"]

    media_result = await calls["media_fetch"](url=_FAKE_URL)
    assert media_result["status"] == "nothing_found"
    assert "fake_read" in media_result["note"]
    assert "handle" in media_result["note"]


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
