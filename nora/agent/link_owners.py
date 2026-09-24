"""One registry of link owners, consulted by every generic fetcher through a
declared wrapper (`design/social-per-network/30-link-owner-registry.md`).

**PR 1** ("Migration" step 1 in that node): the registry, with the Instagram
row only, and behaviour identical to what `web_fetch` and `media_fetch`
already did inline. `_instagram_page_kind` and the four note constants below
move here, unchanged, from `nora.agent.tools`. **X and LinkedIn are PRs 2 and
3 — do not add rows for them here.**

Placement (the node's "Design" §"Placement"): the network parsers stay in
`nora.domain`, framework-free and shared with the Instagram adapter; this
module is `nora.agent` because it names TOOLS (`instagram_read`, its `link`
parameter) — agent vocabulary the domain must never know. `nora.agent.tools`
is the only importer: its `build_tool_specs` post-pass (§"Where the lookup
runs", option C — a declared wrapper, applied once where tools are built)
reads `owner_of`/`redirect` from here and nothing else does, so adding a
network is one `LinkOwner` row and never an edit to a fetcher's body.

**Deliberate PR1 scoping** (see the report on the PR): the node's "Types"
section sketches a `redirect(owner, claim, *, status, then)` that BUILDS a
note from `owner.label`/`owner.tool`/`owner.param` plus a hint and a `then`
tail. That generic template does not reproduce today's exact wording — for
example `_INSTAGRAM_PAGE_LINK` also says "not a picture or video file",
which no other fetcher's redirect needs — so it changes the words a
live user reads the day a second network needs the same shape (PR2/3, with
its own eval case). PR1's own requirement is **behaviour identical**, so
`LinkInput` below carries the exact, already-live note text per fetcher
instead of a template, and `redirect()` only picks between them.
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


# ---- the four note constants (moved here unchanged from nora.agent.tools) --

# What `media_fetch(url=...)` says when the url is an Instagram PAGE (a
# post, reel, story or profile link) rather than a picture or video file.
# `nothing_found` under R-27: the link is not a describable picture, which
# is that status's own documented meaning, and nothing was tried that could
# have failed. The note names the next call and what to do with its result,
# because the result (`instagram_read`'s post and story rows) is where the
# files are. No dash, ADR 0015 §3 (`tests/unit/test_social_tool_notes.py`).
_INSTAGRAM_PAGE_LINK = (
    "That link is an Instagram page, not a picture or video file. Open it "
    "with instagram_read using this link, then look at the media it returns."
)

# The same answer for a MOBILE page link (`m.instagram.com/reel/...`). The
# host is not one `parse_link` accepts, and it stays that way
# (`INSTAGRAM_HOSTS` is exact on purpose), so `instagram_read` would refuse
# it too: the note asks for the www link rather than sending her there.
_INSTAGRAM_MOBILE_PAGE_LINK = (
    "That is a mobile Instagram page link, not a picture or video file, and "
    "it can't be read directly. Ask for the www.instagram.com link to the "
    "same post, reel, story or profile."
)

# What `web_fetch` says for the same two kinds of link (design 13,
# 2026-09-24): 3 of 12 replays of a shared post sent its page link to
# `web_fetch`, which reads an Instagram page as a login wall at best. The
# page is `instagram_read`'s, so the answer is a route, `nothing_found` as in
# `media_fetch`, and nothing is fetched. No dash, ADR 0015 §3.
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
    """

    network: str
    kind: str
    readable: bool


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


def _instagram_claim(text: str) -> LinkClaim | None:
    kind = _instagram_page_kind(text)
    if kind == "page":
        return LinkClaim(network="instagram", kind="page", readable=True)
    if kind == "mobile":
        return LinkClaim(network="instagram", kind="mobile_page", readable=False)
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
    """The first (and, today, only) owner in `LINK_OWNERS` that claims
    `text`, or `None`. `text` is read exactly as a fetcher's argument
    arrives — untrimmed, with or without a scheme — because each recogniser
    applies its own `_with_scheme` before its parser sees it."""
    for owner in LINK_OWNERS:
        claim = owner.claim(text)
        if claim is not None:
            return owner, claim
    return None


@dataclass(frozen=True)
class LinkInput:
    """One fetcher's parameter that may carry a link another tool owns
    (`NativeTool.link_inputs`, read by `build_tool_specs`'s post-pass).

    `page_note`/`mobile_note` are this fetcher's own exact wording for a
    readable claim and an unreadable one (see the module docstring's "PR1
    scoping" note on why these are literal text and not a generated
    template). `unless` names another parameter of the same tool whose
    truthiness means "don't check this one" — `media_fetch` accepts `code`
    XOR `url`, and its inline guard never ran the Instagram check when a
    `code` was also given (`page = _instagram_page_kind(url) if url and not
    code else ""`); `unless="code"` keeps that exact behaviour.
    """

    param: str
    page_note: str
    mobile_note: str
    unless: str = ""


def redirect(link_input: LinkInput, claim: LinkClaim) -> dict:
    """The ONE shape a claimed link returns: `nothing_found` — the one
    redirect status the operator accepted 2026-09-25 (this node's "Open
    decisions" §1) — and `link_input`'s own note for `claim`'s readability.
    The link itself is never echoed (unchanged: `tests/test_web_fetch_tool.
    py::test_an_instagram_page_link_is_routed_to_instagram_read_and_never_
    fetched` asserts the url is not in the note)."""
    note = link_input.page_note if claim.readable else link_input.mobile_note
    return {"status": "nothing_found", "note": note}
