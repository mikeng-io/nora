"""One registry of link owners, consulted by every generic fetcher through a
declared wrapper.

`LINK_OWNERS` holds one `LinkOwner` row per network (Instagram, X, LinkedIn)
and one for shared file links. Each row wraps its network's own `parse_link`
(`nora.domain`), which stays framework-free; this module is `nora.agent`
because it names TOOLS (`instagram_read` and its `link` parameter), which the
domain must never know.

Two kinds of caller use it:

- Fetchers (`web_fetch`, `media_fetch`, `computer_import`): `build_tool_specs`
  in `nora.agent.tools` applies a post-pass, once where tools are built, that
  reads `owner_of`/`redirect` from here. Adding a network is one `LinkOwner`
  row and never an edit to a fetcher's body.
- Owners (`instagram_read`, `x_read`, `linkedin_read`): each calls `owner_of`
  and `redirect` directly, from the spot where its own `parse_link` returns
  `None`, so a link nobody claims still reaches that tool's own refusal. They
  never go through the fetcher post-pass, which would redirect an owner's OWN
  readable link away from itself (it cannot tell "claimed by me" from
  "claimed by someone else"). `OWNER_LINK_INPUT` is the one `LinkInput` they
  all pass. Every redirect from an owner is `nothing_found`.

**The note is composed from two independent slot sets, never a literal per
fetcher.** A redirect's wording has exactly two sources, and a value from one
never leaks into the other:

- **The owner's** (`LinkOwner.label`/`.tool`/`.param`, `LinkClaim.
  unreadable_template`): what the link IS and who reads it. Only the network
  knows why ITS unreadable shape can't be read (Instagram's `mobile_page` is
  a host `parse_link` refuses on purpose), so that template lives on the
  claim, not the fetcher.
- **The fetcher's** (`LinkInput.not_clause`/`.then`/`.verb`): what kind of
  tool is asking. `media_fetch` came to look at MEDIA, so its note says so and
  points at what to do with the redirect's result; `web_fetch` reads a page,
  so its note is silent on both.

`redirect()` is the one place these two combine, by template substitution
rather than a lookup table: `LinkInput` carries no network name and no network
text, so a new network is one `LinkOwner` row, plus one `unreadable_template`
on its `LinkClaim` if its link can be unreadable, and never a new field on a
fetcher's declaration. `tests/unit/test_link_owners.py` proves this with a
fake registered owner.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.external_files import recognise as _recognise_file
from ..domain.instagram_links import parse_link as _parse_instagram_link
from ..domain.linkedin_links import parse_link as _parse_linkedin_link
from ..domain.x_links import parse_link as _parse_x_link


def _with_scheme(link: str) -> str:
    """A pasted link as a network parser reads it: a link given without a
    scheme (`instagram.com/p/...`) gains `https://`. Nothing else changes.

    Duplicated from `nora.agent.tools._with_scheme` rather than imported,
    because `nora.agent.tools` imports THIS module and the reverse import
    would cycle.
    """
    text = link.strip()
    return text if "://" in text else "https://" + text


# ---- the four fixed notes, kept as literals ---------------------------------
#
# `tests/test_media_fetch.py` and `tests/unit/test_social_tool_notes.py`
# import these by name. `redirect()` does not read them; it composes the note
# from `LinkOwner`/`LinkClaim`/`LinkInput`, and `tests/unit/test_link_owners.
# py::test_the_composed_note_matches_every_live_string` proves the
# composition produces exactly these four strings.
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


#: The only slots `redirect()` fills an `unreadable_template` with. Checked at
#: `LinkClaim` construction, not at `redirect()`'s `.format()` call, so a
#: template naming an unknown slot fails in a test
#: (`tests/unit/test_link_owners.py::
#: test_an_unreadable_templates_unknown_slot_fails_at_construction`) and not
#: as a bare `KeyError` inside a tool call.
_UNREADABLE_TEMPLATE_SLOTS = {"not_clause": "", "verb": ""}


@dataclass(frozen=True)
class LinkClaim:
    """What a pasted link IS, to the registry.

    `kind` is the parser's own kind (`"page"`), or `"mobile_page"` for the
    one shape Instagram's recogniser also claims that no `nora.domain`
    parser accepts directly (`m.instagram.com`). `readable` is whether the
    owning tool can actually read this kind — false for `mobile_page`,
    because `instagram_read` refuses that host too.

    `unreadable_template` is the network's OWN explanation of why, REQUIRED
    when `readable` is false (`__post_init__` below) — ADR 0015 §3 requires a
    `note` on every non-`ok` status, and an empty string satisfies no
    reader's idea of "a note" even though it satisfies a check that only
    looks for the KEY (`tests/architecture/test_every_status_carries_a_note.
    py`). It is a `str.format` template with exactly two fetcher-filled
    slots, `{not_clause}` and `{verb}` (`LinkInput`'s own fields — see
    `redirect()`); only the network knows this text, a fetcher never carries
    it, which is what keeps adding a network from touching a fetcher's own
    declaration.
    """

    network: str
    kind: str
    readable: bool
    unreadable_template: str = ""

    def __post_init__(self) -> None:
        if self.readable:
            return
        if not self.unreadable_template:
            raise ValueError(
                f"{self.network}/{self.kind}: an unreadable LinkClaim needs "
                "its own unreadable_template (ADR 0015 §3: every non-ok "
                "status needs a real note, not an empty one)"
            )
        try:
            self.unreadable_template.format(**_UNREADABLE_TEMPLATE_SLOTS)
        except (KeyError, IndexError) as exc:
            raise ValueError(
                f"{self.network}/{self.kind}: unreadable_template names an "
                f"unknown slot — `redirect()` only ever fills "
                f"{sorted(_UNREADABLE_TEMPLATE_SLOTS)}: {exc}"
            ) from exc


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
# (`m.instagram.com`): the network's text, not a fetcher's. `{not_clause}`
# and `{verb}` are filled from the asking fetcher's `LinkInput` (see
# `redirect()`): `media_fetch` says "not a picture or video file" and "read";
# `web_fetch` says neither and "opened".
_INSTAGRAM_MOBILE_TEMPLATE = (
    "That is a mobile Instagram page link{not_clause}, and it can't be "
    "{verb} directly. Ask for the www.instagram.com link to the same post, "
    "reel, story or profile."
)

def _instagram_claim(text: str) -> LinkClaim | None:
    # Built per call, not as a module-level singleton:
    # `tests/architecture/test_no_module_level_mutable_singletons.py` forbids
    # instantiating a locally-defined class at module scope.
    kind = _instagram_page_kind(text)
    if kind == "page":
        return LinkClaim(network="instagram", kind="page", readable=True)
    if kind == "mobile":
        return LinkClaim(
            network="instagram", kind="mobile_page", readable=False,
            unreadable_template=_INSTAGRAM_MOBILE_TEMPLATE,
        )
    return None


def _x_claim(text: str) -> LinkClaim | None:
    """X's own recogniser: wraps `nora.domain.x_links.parse_link` after
    `_with_scheme`, as `_instagram_claim` wraps Instagram's.

    X's host allowlist (`x_links.X_HOSTS`) already accepts `mobile.x.com`,
    `mobile.twitter.com` and the bare `x.com`/`twitter.com` spellings as
    readable (`x_read` uses the same `parse_link`), so there is no unreadable
    mobile shape: every claim returned is `readable=True`.

    `t.co` and X's CDN (`*.twimg.com`) are not `X_HOSTS` members, so
    `parse_link` returns nothing for them; this function does not list them
    (`tests/unit/test_link_owners.py`'s negative rows pin this).
    """
    # Built per call, not as a module-level singleton, as in
    # `_instagram_claim`.
    parsed = _parse_x_link(_with_scheme(text))
    if parsed is None:
        return None
    return LinkClaim(network="x", kind=parsed.kind, readable=True)


# LinkedIn's own explanation for the one shape it claims but cannot read
# (`m.linkedin.com`), as `_INSTAGRAM_MOBILE_TEMPLATE` is for Instagram.
_LINKEDIN_MOBILE_TEMPLATE = (
    "That is a mobile LinkedIn page link{not_clause}, and it can't be "
    "{verb} directly. Ask for the www.linkedin.com link to the same "
    "profile or company."
)


def _linkedin_claim(text: str) -> LinkClaim | None:
    """LinkedIn's own recogniser: wraps `nora.domain.linkedin_links.
    parse_link` after `_with_scheme`, with two differences from a plain wrap:

    - **A `post` is never claimed.** `linkedin_read` refuses to open one from
      its link, and `web_fetch` may still read a public LinkedIn post page, so
      claiming it would route a readable page to a tool that refuses it.
      Returning `None` lets the fetcher try it as an ordinary page, as it
      does for `lnkd.in` and LinkedIn's CDN host.
    - **`m.linkedin.com` is claimed but unreadable**, like Instagram's
      `m.instagram.com`: the parser's host charset refuses it on purpose (a
      country subdomain is exactly two letters, and `m` is one). It is judged
      on the www spelling of the same path, as `_instagram_page_kind` does,
      and nothing is read from the rebuilt url beyond whether it parses. A
      path that would be a `post` even on `www.` stays unclaimed.
    """
    candidate = _with_scheme(text)
    parsed = _parse_linkedin_link(candidate)
    if parsed is not None:
        if parsed.kind == "post":
            return None
        return LinkClaim(network="linkedin", kind=parsed.kind, readable=True)
    try:
        parts = urllib.parse.urlsplit(candidate)
    except ValueError:
        return None
    if (parts.hostname or "") != "m.linkedin.com":
        return None
    www = urllib.parse.urlunsplit(("https", "www.linkedin.com", parts.path, "", ""))
    mobile = _parse_linkedin_link(www)
    if mobile is None or mobile.kind == "post":
        return None
    return LinkClaim(
        network="linkedin", kind="mobile_page", readable=False,
        unreadable_template=_LINKEDIN_MOBILE_TEMPLATE,
    )


def _external_file_claim(text: str) -> LinkClaim | None:
    """A shared file link: the domain's own pure recogniser, so a link recorded
    as a handle at ingest and a link pasted into a fetcher are claimed by the
    same rule. `network` is vendor-neutral on purpose: the redirect note names
    `file_fetch`, never the provider."""
    ref = _recognise_file(_with_scheme(text))
    if ref is None:
        return None
    return LinkClaim(network="external_files", kind="file", readable=True)


#: Instagram, X, LinkedIn and shared file links. `instagram_read`, `x_read`
#: and `linkedin_read` in `nora.agent.tools` consult this table for EACH
#: OTHER's links through `owner_of` directly, never through
#: `_apply_link_owners`'s fetcher post-pass.
LINK_OWNERS: tuple[LinkOwner, ...] = (
    LinkOwner(
        network="instagram", tool="instagram_read", param="link",
        label="an Instagram page", claim=_instagram_claim,
    ),
    LinkOwner(
        network="x", tool="x_read", param="link",
        label="an X link", claim=_x_claim,
    ),
    LinkOwner(
        network="linkedin", tool="linkedin_read", param="link",
        label="a LinkedIn link", claim=_linkedin_claim,
    ),
    # A shared file link (a document, spreadsheet, slides or a stored file)
    # is `file_fetch`'s, so `web_fetch` points at it instead of fetching a
    # sign-in page. `media_fetch` and `computer_import` accept it
    # (`LinkInput.accepts`): they take the file's bytes through the same
    # source, to look at or to copy.
    LinkOwner(
        network="external_files", tool="file_fetch", param="link",
        label="a link to an external file", claim=_external_file_claim,
    ),
)


def owner_of(
    text: str, *, unwired: frozenset[str] = frozenset(),
) -> tuple[LinkOwner, LinkClaim] | None:
    """The first owner in `LINK_OWNERS` that claims `text`, or `None`. `text`
    is read exactly as a fetcher's argument arrives, untrimmed, with or
    without a scheme, because each recogniser applies its own `_with_scheme`
    before its parser sees it. An owner whose tool is in `unwired` (it cannot
    read anything this turn: `file_fetch` with no source) claims nothing."""
    for owner in LINK_OWNERS:
        if owner.tool in unwired:
            continue
        claim = owner.claim(text)
        if claim is not None:
            return owner, claim
    return None


@dataclass(frozen=True)
class LinkInput:
    """One fetcher's parameter that may carry a link another tool owns
    (`NativeTool.link_inputs`, read by `build_tool_specs`'s post-pass).

    Every field is FETCHER-generic: no network name, no network text.
    `not_clause`/`then`/`verb` are the only three ways `redirect()` lets a
    fetcher's own voice into the note, each a small slot and never a sentence
    naming a network:

    - `not_clause`: appended right after the owner's `label` in the readable
      note, and substituted into the unreadable template's `{not_clause}`.
      `media_fetch` uses `", not a picture or video file"` in both; `web_fetch`
      uses `""` in both.
    - `then`: appended after the owner's `param` in the readable note only.
      `media_fetch` uses a "then look at the media it returns" tail; empty for
      `web_fetch`.
    - `verb`: the unreadable template's `{verb}`; `media_fetch` "read"s a
      picture, `web_fetch` "opened" a page.

    `unless` names another parameter of the same tool whose truthiness means
    "don't check this one": `media_fetch` accepts `code` XOR `url` and never
    runs the Instagram check when a `code` is also given, so it sets
    `unless="code"`.
    """

    param: str
    unless: str = ""
    not_clause: str = ""
    then: str = ""
    verb: str = ""
    #: Owner TOOLS whose links this parameter takes as its own rather than
    #: redirecting: `computer_import` copies a shared file `file_fetch` owns
    #: and `media_fetch` looks at one that is a picture or a video, so a file
    #: link reaches their bodies. A tool name, never a network.
    accepts: tuple[str, ...] = ()


#: The slot set `instagram_read`, `x_read` and `linkedin_read` pass to
#: `redirect()` when handed a link another owner claims. One shared instance,
#: not one per owner: an owning tool is never asking about media
#: (`not_clause`/`then` stay at their `""` defaults, as `web_fetch`'s do) and
#: it reads, so `verb="read"` fills the one slot an UNREADABLE claim needs
#: (e.g. `x_read` handed `m.instagram.com`: Instagram's
#: `_INSTAGRAM_MOBILE_TEMPLATE`, "...and it can't be {verb} directly").
#: `param` is not read by `redirect()`; only `link_input.param` in
#: `_link_owner_wrapped`'s FETCHER path uses it, to find the argument to
#: check. It is `"link"` here as documentation, that being every owner's
#: parameter name. Which owner is redirecting comes from the `owner`/`claim`
#: pair `owner_of` returned, never from this value.
#:
#: A 1-tuple, not a bare `NAME = LinkInput(...)`: `LinkInput` is defined in
#: THIS module, and `tests/architecture/test_no_module_level_mutable_
#: singletons.py` forbids binding a module-level name directly to a call that
#: constructs a locally-defined class (the same reason `LINK_OWNERS` is a
#: tuple of `LinkOwner(...)` calls).
_OWNER_LINK_INPUTS: tuple[LinkInput, ...] = (LinkInput(param="link", verb="read"),)
OWNER_LINK_INPUT = _OWNER_LINK_INPUTS[0]


def redirect(owner: LinkOwner, claim: LinkClaim, link_input: LinkInput) -> dict:
    """The ONE shape a claimed link returns: `nothing_found` with a note
    COMPOSED from `owner`/`claim` (what the link is, who reads it, why an
    unreadable one can't be) and `link_input` (which fetcher is asking).
    Never a fetcher-held literal: a new network changes only `owner`/`claim`,
    a new fetcher only `link_input`, and neither can write the other's half.

    The readable template is `"That link is {label}{not_clause}. Open it with
    {tool} using this {param}{then}."`; both it and the unreadable one match
    the four fixed notes above (`tests/unit/test_link_owners.py::
    test_the_composed_note_matches_every_live_string`). The link itself is
    never echoed (`tests/test_web_fetch_tool.py::
    test_an_instagram_page_link_is_routed_to_instagram_read_and_never_fetched`
    asserts the url is not in the note).
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
