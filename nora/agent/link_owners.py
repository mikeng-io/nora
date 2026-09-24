"""One registry of link owners, consulted by every generic fetcher through a
declared wrapper (`design/social-per-network/30-link-owner-registry.md`).

**PR 1** ("Migration" step 1 in that node): the registry, with the Instagram
row only, and behaviour identical to what `web_fetch` and `media_fetch`
already did inline. `_instagram_page_kind` and the four historical note
constants move here, unchanged, from `nora.agent.tools` — but the constants
are no longer what a redirect returns; see below.

Placement (the node's "Design" §"Placement"): the network parsers stay in
`nora.domain`, framework-free and shared with the Instagram adapter; this
module is `nora.agent` because it names TOOLS (`instagram_read`, its `link`
parameter) — agent vocabulary the domain must never know. `nora.agent.tools`
is the only importer: its `build_tool_specs` post-pass (§"Where the lookup
runs", option C — a declared wrapper, applied once where tools are built)
reads `owner_of`/`redirect` from here and nothing else does, so adding a
network is one `LinkOwner` row and never an edit to a fetcher's body.

**The note is composed from two independent slot sets, never a literal per
fetcher.** A redirect's wording has exactly two sources, and a value from one
never leaks into the other:

- **The owner's** (`LinkOwner.label`/`.tool`/`.param`, `LinkClaim.
  unreadable_template`): what the link IS and who reads it. Only the network
  knows why ITS unreadable shape can't be read (Instagram's `mobile_page` is
  a host `parse_link` refuses on purpose), so that template lives on the
  claim, not the fetcher.
- **The fetcher's** (`LinkInput.not_clause`/`.then`/`.verb`): what kind of
  tool is asking — `media_fetch` came here to look at MEDIA, so its note
  says so and points at what to do with the redirect's result; `web_fetch`
  reads a page, so its note is silent on both.

`redirect()` below is the one place these two combine, and it is a template
substitution, not a lookup table: `LinkInput` carries no network name and no
network text, so a second network (X, LinkedIn — PRs 2/3) is one `LinkOwner`
row PLUS, for a network whose link can be unreadable, one `unreadable_template`
on its `LinkClaim` — never a new field on `web_fetch`'s or `media_fetch`'s own
declaration. `tests/unit/test_link_owners.py` proves this with a fake
registered owner: both fetchers redirect its link, with no fetcher change.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.instagram_links import parse_link as _parse_instagram_link


def _with_scheme(link: str) -> str:
    """A pasted link as a network parser reads it: a link given without a
    scheme (`instagram.com/p/...`) gains `https://`. Nothing else changes.

    Deliberately duplicated from `nora.agent.tools._with_scheme` rather than
    imported: `nora.agent.tools` imports THIS module (for `owner_of`/
    `redirect`/`LINK_OWNERS`), so the reverse import would cycle. The other
    copy stays in `tools.py` for its own remaining callers
    (`_same_instagram_link`, `x_read`, `linkedin_read`, `instagram_read`),
    none of which move in this PR.
    """
    text = link.strip()
    return text if "://" in text else "https://" + text


# ---- the four live strings, kept as literals purely for back-compat ---------
#
# `tests/test_media_fetch.py` and `tests/unit/test_social_tool_notes.py`
# import these by name and are UNCHANGED in this PR — so the names stay. They
# are no longer read by `redirect()` below (which COMPOSES the note from
# `LinkOwner`/`LinkClaim`/`LinkInput` instead): `tests/unit/test_link_owners.
# py::test_the_composed_note_matches_every_live_string` is what proves the
# composition still produces exactly these four strings.
_INSTAGRAM_PAGE_LINK = (
    "That link is an Instagram page, not a picture or video file. Open it "
    "with instagram_read using this link, then look at the media it returns."
)
_INSTAGRAM_MOBILE_PAGE_LINK = (
    "That is a mobile Instagram page link, not a picture or video file, and "
    "it can't be read directly. Ask for the www.instagram.com link to the "
    "same post, reel, story or profile."
)
_WEB_FETCH_INSTAGRAM_PAGE = (
    "That link is an Instagram page. Open it with instagram_read using this "
    "link."
)
_WEB_FETCH_INSTAGRAM_MOBILE_PAGE = (
    "That is a mobile Instagram page link, and it can't be opened directly. "
    "Ask for the www.instagram.com link to the same post, reel, story or "
    "profile."
)


def _instagram_page_kind(url: str) -> str:
    """`"page"` for an Instagram page link `parse_link` accepts, `"mobile"`
    for the same shape on `m.instagram.com`, `""` for anything else. A link
    given without a scheme is read as the parser reads a pasted one
    (`_with_scheme`)."""
    candidate = _with_scheme(url)
    if _parse_instagram_link(candidate) is not None:
        return "page"
    try:
        parts = urllib.parse.urlsplit(candidate)
    except ValueError:
        return ""
    if (parts.hostname or "") != "m.instagram.com":
        return ""
    # Judged on the www spelling of the same path, so the shape and every
    # charset are still the parser's. Nothing is read from the rebuilt url.
    www = urllib.parse.urlunsplit(("https", "www.instagram.com", parts.path, "", ""))
    return "mobile" if _parse_instagram_link(www) is not None else ""


@dataclass(frozen=True)
class LinkClaim:
    """What a pasted link IS, to the registry.

    `kind` is the parser's own kind (`"page"`), or `"mobile_page"` for the
    one shape Instagram's recogniser also claims that no `nora.domain`
    parser accepts directly (`m.instagram.com`). `readable` is whether the
    owning tool can actually read this kind — false for `mobile_page`,
    because `instagram_read` refuses that host too.

    `unreadable_template` is the network's OWN explanation of why, used only
    when `readable` is false: a `str.format` template with two fetcher-filled
    slots, `{not_clause}` and `{verb}` (`LinkInput`'s own fields — see
    `redirect()`). Only the network knows this text; a fetcher never carries
    it, which is what keeps adding a network from touching a fetcher's own
    declaration.
    """

    network: str
    kind: str
    readable: bool
    unreadable_template: str = ""


@dataclass(frozen=True)
class LinkOwner:
    """One tool's registration: which network it reads, the parameter that
    carries the link, how a redirect names it, and its recogniser.

    `claim` wraps the network's own `parse_link` (after `_with_scheme`) and
    returns a `LinkClaim` or `None` — never a host-level guess. A recogniser
    claims a PAGE, never a host: `owner_of` below tries each in order and a
    disjointness test (`tests/unit/test_link_owners.py`) keeps "first match
    wins" from ever having to decide anything.
    """

    network: str
    tool: str
    param: str
    label: str
    claim: Callable[[str], LinkClaim | None]


# Instagram's own explanation for the one shape it claims but cannot read
# (`m.instagram.com`) — the network's text, not a fetcher's. `{not_clause}`
# and `{verb}` are filled in by whichever fetcher's `LinkInput` is asking
# (`redirect()`): `media_fetch` says "not a picture or video file" and
# "read"; `web_fetch` says neither and "opened".
_INSTAGRAM_MOBILE_TEMPLATE = (
    "That is a mobile Instagram page link{not_clause}, and it can't be "
    "{verb} directly. Ask for the www.instagram.com link to the same post, "
    "reel, story or profile."
)

def _instagram_claim(text: str) -> LinkClaim | None:
    # Built per call, not as a module-level singleton
    # (`tests/architecture/test_no_module_level_mutable_singletons.py`
    # forbids instantiating a locally-defined class at module scope,
    # frozen-and-cheap or not): a fresh `LinkClaim` costs nothing a model
    # call would notice.
    kind = _instagram_page_kind(text)
    if kind == "page":
        return LinkClaim(network="instagram", kind="page", readable=True)
    if kind == "mobile":
        return LinkClaim(
            network="instagram", kind="mobile_page", readable=False,
            unreadable_template=_INSTAGRAM_MOBILE_TEMPLATE,
        )
    return None


#: Instagram only (PR 1, `design/social-per-network/30-link-owner-registry.md`
#: "Migration" step 1). X and LinkedIn are steps 2 and 3 of that same
#: "Migration" section — each is one more row here, never an edit to a
#: fetcher's body. **Do not add a row for either in this PR.**
LINK_OWNERS: tuple[LinkOwner, ...] = (
    LinkOwner(
        network="instagram", tool="instagram_read", param="link",
        label="an Instagram page", claim=_instagram_claim,
    ),
)


def owner_of(text: str) -> tuple[LinkOwner, LinkClaim] | None:
    """The first owner in `LINK_OWNERS` that claims `text`, or `None`. `text`
    is read exactly as a fetcher's argument arrives — untrimmed, with or
    without a scheme — because each recogniser applies its own
    `_with_scheme` before its parser sees it."""
    for owner in LINK_OWNERS:
        claim = owner.claim(text)
        if claim is not None:
            return owner, claim
    return None


@dataclass(frozen=True)
class LinkInput:
    """One fetcher's parameter that may carry a link another tool owns
    (`NativeTool.link_inputs`, read by `build_tool_specs`'s post-pass).

    Every field here is FETCHER-generic: no network name, no network text.
    `not_clause`/`then`/`verb` are the only three ways `redirect()` lets a
    fetcher's own voice into the note, and each is a small slot, never a
    sentence naming a network:

    - `not_clause`: appended right after the owner's `label` in the readable
      note, and substituted into the unreadable template's `{not_clause}`.
      `media_fetch` uses `", not a picture or video file"` in both; `web_fetch`
      uses `""` in both, because it is not asking about media at all.
    - `then`: appended after the owner's `param` in the readable note only —
      `media_fetch`'s "then look at the media it returns" tail, because the
      model came to `media_fetch` to see something; empty for `web_fetch`.
    - `verb`: the unreadable template's `{verb}` — `media_fetch` "read"s a
      picture, `web_fetch` "opened" a page; the two fetchers describe the
      same refusal in their own words.

    `unless` names another parameter of the same tool whose truthiness means
    "don't check this one" — `media_fetch` accepts `code` XOR `url`, and its
    old inline guard never ran the Instagram check when a `code` was also
    given (`page = _instagram_page_kind(url) if url and not code else ""`);
    `unless="code"` keeps that exact behaviour.
    """

    param: str
    unless: str = ""
    not_clause: str = ""
    then: str = ""
    verb: str = ""


def redirect(owner: LinkOwner, claim: LinkClaim, link_input: LinkInput) -> dict:
    """The ONE shape a claimed link returns: `nothing_found` — the one
    redirect status the operator accepted 2026-09-25 (this node's "Open
    decisions" §1) — and a note COMPOSED from `owner`/`claim` (what the link
    is, who reads it, why an unreadable one can't be) and `link_input` (which
    fetcher is asking). Never a fetcher-held literal: a new network changes
    only `owner`/`claim`, a new fetcher only `link_input`, and neither can
    write the other's half.

    The readable template — `"That link is {label}{not_clause}. Open it with
    {tool} using this {param}{then}."` — and the unreadable one are both
    exact matches for today's four live strings (`tests/unit/
    test_link_owners.py::test_the_composed_note_matches_every_live_string`);
    the link itself is never echoed either way (unchanged: `tests/
    test_web_fetch_tool.py::test_an_instagram_page_link_is_routed_to_
    instagram_read_and_never_fetched` asserts the url is not in the note).
    """
    if claim.readable:
        note = (
            f"That link is {owner.label}{link_input.not_clause}. "
            f"Open it with {owner.tool} using this {owner.param}{link_input.then}."
        )
    else:
        note = claim.unreadable_template.format(
            not_clause=link_input.not_clause, verb=link_input.verb,
        )
    return {"status": "nothing_found", "note": note}
