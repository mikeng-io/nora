"""`nora.agent.link_owners` — the registry itself, and the enforcement tests
`design/social-per-network/30-link-owner-registry.md` names for PR 1
("Migration" step 1): every owner names a real tool and a real parameter on
it, the redirect shape is `nothing_found` with the exact note pinned, the
note is COMPOSED from owner and fetcher slots rather than fetcher-held
literal text, and registering a network touches no fetcher.

**PR 2** ("Migration" step 2) adds X and LinkedIn: `LINK_OWNERS` now has
three rows, and this file's matrix grows an X and a LinkedIn section beside
Instagram's. The negatives carry every host the design's "What live traffic
shows" and "Types" sections name as one a registry must never claim: X's and
LinkedIn's own CDN hosts, `t.co` and `lnkd.in` (neither is its network's own
page — the one live `lnkd.in` link led off-LinkedIn), a lookalike host
(`x.com.evil.example`), a reserved X path (`/explore`), and a LinkedIn post
link (recognised by the parser, but `linkedin_read` cannot read one from its
link, so it is never claimed — design 30, "Design" §"Types"). PR 3 ("The
owners use the registry") is not built here: `x_read`/`linkedin_read`/
`instagram_read` keep their own hand-written cross-network branches.
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

# ---- X (PR 2): the parser's own host allowlist already accepts every
# mobile/alternate spelling (`x_links.X_HOSTS`), so every one of these is
# `readable=True` with no separate mobile shape.
_X_POST_URL = "https://x.com/synth_user01/status/9000000000000000001"
_X_PROFILE_URL = "https://twitter.com/synth_user02"
_CLAIMED_X = (
    _X_POST_URL,
    _X_PROFILE_URL,
    "https://mobile.x.com/synth_user03/status/9000000000000000003",
    "https://mobile.twitter.com/synth_user04",
    "https://www.x.com/synth_user05",
    "x.com/synth_user01/status/9000000000000000001",  # no scheme
)

# ---- LinkedIn (PR 2): a person or company link is readable; `m.linkedin.com`
# is claimed but not readable (the same shape as Instagram's mobile host);
# a post is never claimed at all (below, in `_UNCLAIMED`).
_LINKEDIN_PERSON_URL = "https://www.linkedin.com/in/synth-person-01"
_LINKEDIN_COMPANY_URL = "https://linkedin.com/company/synth-company-01"
_LINKEDIN_MOBILE_URL = "https://m.linkedin.com/in/synth-person-03"
_CLAIMED_LINKEDIN = (
    _LINKEDIN_PERSON_URL,
    _LINKEDIN_COMPANY_URL,
    "https://hk.linkedin.com/in/synth-person-02",  # a two-letter country subdomain
    "linkedin.com/in/synth-person-01",  # no scheme
)
_CLAIMED_LINKEDIN_MOBILE = (
    _LINKEDIN_MOBILE_URL,
    "m.linkedin.com/company/synth-company-04",
)

_UNCLAIMED = (
    "https://scontent.cdninstagram.com/v/t51/synthetic.jpg",  # the item's own CDN file
    "https://x.com/pic.jpg",
    "https://lnkd.in/abc123",  # a shortener: not its network's own page
    "https://example.com/a",
    "https://www.instagram.com/explore/",  # an Instagram host, not a page kind
    "https://t.co/synthShort1",  # X's own shortener, a different host
    "https://pbs.twimg.com/media/synthetic.jpg",  # X's own CDN file
    "https://x.com.evil.example/synth_user01",  # a lookalike host, not x.com
    "https://x.com/explore",  # a reserved X page, not an account (non-goals)
    "https://media.licdn.com/dms/image/synthetic.jpg",  # LinkedIn's own CDN file
    # A LinkedIn post: recognised by the parser, never claimed (linkedin_read
    # cannot read one from its link; web_fetch may).
    "https://www.linkedin.com/posts/synth-person-01_synthetic-activity-9000000000000000001/",
    "https://m.linkedin.com/posts/synth-person-01_synthetic-activity-9000000000000000002/",
)


@pytest.mark.parametrize("url", _CLAIMED_PAGE)
def test_an_instagram_page_link_is_claimed_and_readable(url):
    found = owner_of(url)
    assert found is not None, url
    owner, claim = found
    assert owner.network == "instagram"
    assert owner.tool == "instagram_read"
    assert owner.param == "link"
    assert claim.readable is True


@pytest.mark.parametrize("url", _CLAIMED_MOBILE)
def test_a_mobile_instagram_page_link_is_claimed_and_not_readable(url):
    found = owner_of(url)
    assert found is not None, url
    _owner, claim = found
    assert claim.readable is False
    assert claim.unreadable_template, "an unreadable claim carries its own explanation"


@pytest.mark.parametrize("url", _CLAIMED_X)
def test_an_x_link_is_claimed_and_readable(url):
    found = owner_of(url)
    assert found is not None, url
    owner, claim = found
    assert owner.network == "x"
    assert owner.tool == "x_read"
    assert owner.param == "link"
    assert claim.readable is True


@pytest.mark.parametrize("url", _CLAIMED_LINKEDIN)
def test_a_linkedin_person_or_company_link_is_claimed_and_readable(url):
    found = owner_of(url)
    assert found is not None, url
    owner, claim = found
    assert owner.network == "linkedin"
    assert owner.tool == "linkedin_read"
    assert owner.param == "link"
    assert claim.readable is True


@pytest.mark.parametrize("url", _CLAIMED_LINKEDIN_MOBILE)
def test_a_mobile_linkedin_link_is_claimed_and_not_readable(url):
    found = owner_of(url)
    assert found is not None, url
    _owner, claim = found
    assert claim.readable is False
    assert claim.unreadable_template, "an unreadable claim carries its own explanation"


@pytest.mark.parametrize("url", _UNCLAIMED)
def test_a_non_page_url_is_claimed_by_no_owner(url):
    assert owner_of(url) is None, url


def test_no_url_in_the_matrices_is_claimed_by_two_owners():
    """Each url in every positive matrix — Instagram, X and LinkedIn — is
    claimed by exactly one `LinkOwner`: the three networks' hosts never
    overlap, so "first match wins" never has to decide anything."""
    for url in (
        _CLAIMED_PAGE + _CLAIMED_MOBILE + _CLAIMED_X
        + _CLAIMED_LINKEDIN + _CLAIMED_LINKEDIN_MOBILE
    ):
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


def test_redirect_composes_the_x_and_linkedin_notes():
    """X and LinkedIn have no historical literal (unlike Instagram's four
    back-compat strings): their notes only ever exist composed, so this pins
    them by construction rather than a fetcher-held string."""
    media_link_input = NATIVE_TOOLS["media_fetch"].link_inputs[0]
    web_link_input = NATIVE_TOOLS["web_fetch"].link_inputs[0]

    x_owner, x_claim = owner_of(_X_POST_URL)
    assert redirect(x_owner, x_claim, web_link_input) == {
        "status": "nothing_found",
        "note": "That link is an X link. Open it with x_read using this link.",
    }
    assert redirect(x_owner, x_claim, media_link_input) == {
        "status": "nothing_found",
        "note": "That link is an X link, not a picture or video file. Open it "
                "with x_read using this link, then look at the media it returns.",
    }

    li_owner, li_claim = owner_of(_LINKEDIN_PERSON_URL)
    assert redirect(li_owner, li_claim, web_link_input) == {
        "status": "nothing_found",
        "note": "That link is a LinkedIn link. Open it with linkedin_read "
                "using this link.",
    }
    assert redirect(li_owner, li_claim, media_link_input) == {
        "status": "nothing_found",
        "note": "That link is a LinkedIn link, not a picture or video file. "
                "Open it with linkedin_read using this link, then look at "
                "the media it returns.",
    }

    mobile_owner, mobile_claim = owner_of(_LINKEDIN_MOBILE_URL)
    assert redirect(mobile_owner, mobile_claim, web_link_input) == {
        "status": "nothing_found",
        "note": "That is a mobile LinkedIn page link, and it can't be opened "
                "directly. Ask for the www.linkedin.com link to the same "
                "profile or company.",
    }
    assert redirect(mobile_owner, mobile_claim, media_link_input) == {
        "status": "nothing_found",
        "note": "That is a mobile LinkedIn page link, not a picture or video "
                "file, and it can't be read directly. Ask for the "
                "www.linkedin.com link to the same profile or company.",
    }


# ---- an unreadable claim must carry a real note (ADR 0015 §3) --------------

def test_an_unreadable_claim_with_no_template_fails_at_construction():
    """An empty `note` satisfies `test_every_status_carries_a_note.py`'s
    static check (it only looks for the KEY), so the only place left to
    catch it is `LinkClaim` itself: `readable=False` with no
    `unreadable_template` must never build."""
    with pytest.raises(ValueError, match="unreadable_template"):
        LinkClaim(network="instagram", kind="mobile_page", readable=False)


def test_an_unreadable_templates_unknown_slot_fails_at_construction():
    """`redirect()` only ever fills `{not_clause}`/`{verb}`; a template
    naming anything else used to raise a bare `KeyError` at `redirect()`'s
    own `.format()` call — reachable only the day a model actually hit that
    claim. Constructing the `LinkClaim` is the earliest point this can be
    caught, and it is cheap enough to always run."""
    with pytest.raises(ValueError, match="unknown"):
        LinkClaim(
            network="instagram", kind="mobile_page", readable=False,
            unreadable_template="needs {a_slot_nobody_fills}",
        )


def test_a_readable_claim_needs_no_template():
    LinkClaim(network="instagram", kind="page", readable=True)


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
