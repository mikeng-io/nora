"""`nora.agent.link_owners` — the registry itself, and its enforcement tests:
every owner names a real tool and a real parameter on
it, the redirect shape is `nothing_found` with the exact note pinned, the
note is COMPOSED from owner and fetcher slots rather than fetcher-held
literal text, and registering a network touches no fetcher.

The matrix has an Instagram, an X and a LinkedIn section. The negatives
carry every host a registry must never claim: X's and LinkedIn's own CDN
hosts, `t.co` and `lnkd.in` (neither is its network's own page), a lookalike
host (`x.com.evil.example`), a reserved X path (`/explore`), and a LinkedIn
post link (recognised by the parser, but `linkedin_read` cannot read one
from its link, so it is never claimed).

The three-by-three matrix at the bottom of this file covers `x_read`,
`linkedin_read` and `instagram_read`, which call `owner_of` (through
`nora.agent.tools._other_link_owner`, which filters out a claim on the asking
tool's OWN network — Instagram's and LinkedIn's unreadable mobile shape is a
claim on their OWN network too) from the exact spot their own `parse_link`
returned `None`.
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
from nora.testing.external_files import FakeExternalFilesSource, FakeFile
from tests.fakes import SinkSender


class _NoStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _NoPersons:
    by_person: typing.ClassVar[dict] = {}

    def resolve(self, platform, sender_id):
        return None


def _built_specs(*, files_wired: bool = True):
    """Production's specs. `files_wired`: an external-files source is wired,
    so `file_fetch` claims shared file links (unwired, it claims none)."""
    room = Room(platform="signal", conversation_id="g1", roster_source=None, persons=_NoPersons())
    source = FakeExternalFilesSource(files={
        "1SynthDocLink000000000000": FakeFile("Plan", parts=("Day one",)),
    }) if files_wired else None
    return ToolRegistry(external_files=source).specs(
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

# ---- X: the parser's own host allowlist already accepts every
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

# ---- LinkedIn: a person or company link is readable; `m.linkedin.com`
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
    "https://x.com/explore",  # a reserved X page, not an account
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
    """The four strings `web_fetch` and
    `media_fetch` return for an Instagram link (`_INSTAGRAM_PAGE_LINK`,
    `_INSTAGRAM_MOBILE_PAGE_LINK`, `_WEB_FETCH_INSTAGRAM_PAGE`,
    `_WEB_FETCH_INSTAGRAM_MOBILE_PAGE`, all imported by
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


# ---- an unreadable claim must carry a real note (ADR 0015) -----------------

def test_an_unreadable_claim_with_no_template_fails_at_construction():
    """An empty `note` satisfies `test_every_status_carries_a_note.py`'s
    static check (it only looks for the KEY), so the only place left to
    catch it is `LinkClaim` itself: `readable=False` with no
    `unreadable_template` must never build."""
    with pytest.raises(ValueError, match="unreadable_template"):
        LinkClaim(network="instagram", kind="mobile_page", readable=False)


def test_an_unreadable_templates_unknown_slot_fails_at_construction():
    """`redirect()` only ever fills `{not_clause}`/`{verb}`; a template
    naming anything else would raise a bare `KeyError` at `redirect()`'s
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


# ---- adding a network touches no fetcher -----------------------------------

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
    is the property: adding a network is one
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


# ---- the signature test ----------------------------------------------------

@pytest.mark.parametrize("owner", LINK_OWNERS, ids=lambda o: o.network)
def test_every_owner_names_a_built_tool_and_a_real_parameter(owner):
    """Renaming `instagram_read`'s `link` must turn this red: a redirect
    naming a parameter that does not exist on the tool it points at is a
    dead end no test built from the fetcher's side would ever catch."""
    calls = _built_calls()
    assert owner.tool in calls, f"{owner.network}: {owner.tool!r} is not a built tool"
    signature = inspect.signature(calls[owner.tool])
    assert owner.param in signature.parameters, (
        f"{owner.network}: {owner.tool} has no parameter named {owner.param!r}"
    )


# ---- the three-by-three matrix ---------------------------------------------
#
# Each owner is handed each other network's link; the matrix reddens when any
# owner falls back to its own hand branch.
# Each owner tool's OWN canonical link, so the six off-diagonal cells (every
# owner asked about every OTHER network) exercise the real production
# closures built with no backend wired at all — the redirect happens before
# `social_source`/`linkedin_source` is ever consulted, exactly as the
# unwired `test_every_owner_names_a_built_tool_and_a_real_parameter` above
# relies on.
_OWNER_CANONICAL_LINK = {
    "instagram_read": _INSTAGRAM_PAGE_URL,
    "x_read": _X_POST_URL,
    "linkedin_read": _LINKEDIN_PERSON_URL,
    # A shared document link. `file_fetch`
    # reaches its own link through the wrapper's self-skip, and every other
    # owner's link through the wrapper's redirect (it declares `link_inputs`).
    "file_fetch": "https://docs.google.com/document/d/1SynthDocLink000000000000/edit",
}
_TOOL_FOR_NETWORK = {owner.network: owner.tool for owner in LINK_OWNERS}
assert set(_OWNER_CANONICAL_LINK) == set(_TOOL_FOR_NETWORK.values())

_CROSS_NETWORK_CASES = [
    (asking_tool, target_tool)
    for asking_tool in _OWNER_CANONICAL_LINK
    for target_tool in _TOOL_FOR_NETWORK.values()
    if target_tool != asking_tool
]
assert len(_CROSS_NETWORK_CASES) == 12, _CROSS_NETWORK_CASES


@pytest.mark.parametrize(
    ("asking_tool", "target_tool"), _CROSS_NETWORK_CASES,
    ids=[f"{a}-given-{t}-link" for a, t in _CROSS_NETWORK_CASES],
)
async def test_every_owner_redirects_every_other_owners_canonical_link(asking_tool, target_tool):
    """Each of `x_read`'s, `linkedin_read`'s and `instagram_read`'s `owner_of`
    call must stay: a hand-written "not mine" branch in its place turns the
    two cases where it is `asking_tool` red — either the status stays
    `unavailable` or the note never names the right tool."""
    calls = _built_calls()
    target_url = _OWNER_CANONICAL_LINK[target_tool]

    out = await calls[asking_tool](link=target_url)

    assert out["status"] == "nothing_found", (asking_tool, target_tool, out)
    assert f"with {target_tool} using this link" in out["note"], out["note"]


async def test_every_owner_still_reads_its_own_canonical_link():
    """The positive control for the matrix above: the redirect only fires for
    ANOTHER network's link. Each owner's own canonical link is not a claim
    `_other_link_owner` returns at all (it is the tool's OWN `parse_link`
    succeeding, before `owner_of` is ever consulted), so none of these are
    `nothing_found`."""
    calls = _built_calls()
    for tool, url in _OWNER_CANONICAL_LINK.items():
        out = await calls[tool](link=url)
        assert out["status"] != "nothing_found", (tool, out)


async def test_an_owner_redirects_an_unreadable_claim_with_owner_link_input():
    """A cross-network redirect can land on an UNREADABLE claim too — `x_read`
    handed a mobile Instagram link, which `instagram_read` itself refuses. The
    note is composed with `OWNER_LINK_INPUT` (`verb="read"`, no `not_clause`/
    `then`): an owner is never asking about media, and it reads, so this is
    the one slot the unreadable template needs filled from an owner's own
    call, distinct from `web_fetch`'s `verb="opened"` or `media_fetch`'s
    `verb="read", not_clause=", not a picture or video file"`."""
    calls = _built_calls()
    out = await calls["x_read"](link=_INSTAGRAM_MOBILE_URL)

    assert out["status"] == "nothing_found"
    assert out["note"] == (
        "That is a mobile Instagram page link, and it can't be read directly. "
        "Ask for the www.instagram.com link to the same post, reel, story or "
        "profile."
    )


async def test_a_hostile_mobile_link_to_its_own_network_is_not_a_self_redirect():
    """The self-network guard's own positive control, from the owner's side
    (`_HOSTILE`'s `m.instagram.com` row in `tests/unit/test_instagram_read_links.py`
    is the negative: `instagram_read` given ITS OWN unreadable mobile shape
    must not redirect to itself). `linkedin_read` given its own
    `m.linkedin.com` is the same shape for the second network with one."""
    calls = _built_calls()
    out = await calls["linkedin_read"](link="https://m.linkedin.com/in/synth-person-03")

    assert out["status"] != "nothing_found", out


# ---- an owner with nothing wired claims nothing ----------------------------

_FILE_LINK = "https://docs.google.com/document/d/1SynthDocLink000000000000/edit"


def test_an_unwired_owner_claims_nothing():
    assert owner_of(_FILE_LINK) is not None
    assert owner_of(_FILE_LINK, unwired=frozenset({"file_fetch"})) is None


@pytest.mark.parametrize("tool", ["x_read", "linkedin_read", "instagram_read"])
async def test_with_no_files_source_a_file_link_is_not_redirected_to_file_fetch(tool):
    """While `[external_files]` is off or unwired, no tool points the model at
    `file_fetch` (it could only answer «unavailable»)."""
    calls = {s.name: s.call for s in _built_specs(files_wired=False)}
    out = await calls[tool](link=_FILE_LINK)
    assert "file_fetch" not in str(out)


async def test_with_no_files_source_a_fetcher_runs_on_a_file_link():
    from nora.agent.tools import _link_owner_wrapped

    ran: list[str] = []

    async def fetch(url: str = "") -> dict:
        ran.append(url)
        return {"status": "ok"}

    link_inputs = NATIVE_TOOLS["web_fetch"].link_inputs
    unwired = _link_owner_wrapped("web_fetch", fetch, link_inputs, Metrics(),
                                  frozenset({"file_fetch"}))
    assert await unwired(url=_FILE_LINK) == {"status": "ok"}
    assert ran == [_FILE_LINK]
    wired = _link_owner_wrapped("web_fetch", fetch, link_inputs, Metrics())
    redirected = await wired(url=_FILE_LINK)
    assert redirected["status"] == "nothing_found" and "file_fetch" in redirected["note"]
