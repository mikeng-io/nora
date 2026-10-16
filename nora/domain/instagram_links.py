"""One Instagram link parser, for everything that reads an Instagram URL

Two readers need it and they must not disagree:

* the Instagram adapter's parser (`nora/platform/instagram/parser.py`), which
  renders a SHARED item's link into the transcript and must never render a
  raw one;
* `instagram_read` (`nora/agent/tools.py`), which turns a link a person pasted
  into a read, and must never send the bridge anything the link did not
  validly name.

A second copy in the tool would be two readings of one URL, which is how two
copies drift: the day one of them loosens its host check, the other is still
green. So both read here:

* **The host is an exact match** on the parsed hostname against
  `instagram.com` and `www.instagram.com`. Never a suffix or a substring:
  `www.instagram.com.evil.x` contains the allowed text and lands elsewhere,
  and `m.instagram.com`, `instagr.am` and the trailing-dot spelling of an
  allowed host are refused as well. A port,
  credentials in the netloc, or a scheme other than http(s) refuse the link.
* **Each path segment is checked against its own charset**, whole
  (`fullmatch`): a shortcode is Instagram's base64url alphabet, a username is
  letters, digits, `.` and `_` (at most 30) with at least one non-digit, a
  story pk is digits. The charsets are ASCII by construction, so a
  full-width or Cyrillic lookalike is refused.
* **The query and the fragment are dropped.** A link is REBUILT from its
  validated segments onto a fixed origin (`InstagramLink.url`). A story
  link's query carries `reel_owner_id`, the owner's igid (R-18), and a
  fragment can carry the same, so nothing after the path survives.

`link_shape` answers only which kind a path is SHAPED like. The parser needs
that on its own, because a shared item keeps its kind label when its link
fails validation («somebody shared a reel», with no link). `parse_link` is
the strict answer, and it is the only one a caller may act on.

Framework-free and standard library only, as everything in `nora.domain` is,
so the adapter layer and the agent layer can both import it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, urlsplit

#: What a link can name. A `reel` is a `post` to the bridge (one route,
#: `GET /posts/{shortcode}`); the kinds are kept apart here because the
#: transcript says which one was shared.
LinkKind = Literal["profile", "post", "reel", "story"]

#: The hosts a link may name, exactly (module docstring).
INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})

# One charset per path segment, each the whole of what that segment can hold.
# `fullmatch`, never `match`: a segment that is a valid shortcode followed by
# anything else is not one.
SHORTCODE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
USERNAME_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
STORY_PK_RE = re.compile(r"[0-9]{1,25}")
# A username must carry at least one non-digit. The charset above admits an
# all-digit segment, and an all-digit "username" is exactly the shape an igid
# takes, which `scan_identifiers` deliberately lets through, because bare
# digit runs are ordinary chat content.
_HAS_NON_DIGIT = re.compile(r"[^0-9]")

#: First path segments Instagram uses for its own pages. A one-segment path is
#: a profile only when its segment is not one of these: `instagram.com/explore/`
#: names no account. Not exhaustive, and it need not be: a word missed here is
#: sent as a username, and the bridge's exact lookup answers «no such account»
#: or names the account that really holds it.
_RESERVED_FIRST_SEGMENTS = frozenset({
    "about", "accounts", "api", "challenge", "developer", "direct",
    "directory", "emails", "explore", "graphql", "legal", "locations", "p",
    "privacy", "reel", "reels", "session", "stories", "terms", "tv", "web",
})

#: The second segment of a `/stories/...` path that names no account.
#: `/stories/highlights/<id>/` is a saved highlight, addressed by the
#: highlight's own id, and `highlights` passes the username charset, so
#: without this it would read as a story by `@highlights`. No route reads a
#: highlight, so the link is unsupported.
_NOT_A_STORY_OWNER = frozenset({"highlights"})


def is_handle(value: str) -> bool:
    """A string that can stand as an Instagram handle: the username charset,
    whole, with at least one non-digit. One predicate for every place a
    handle is read (a story link's segment, a profile link, a shared item's
    author, a typed handle), so an igid cannot ride in through whichever of
    them was checked less."""
    return bool(USERNAME_RE.fullmatch(value)) and bool(_HAS_NON_DIGIT.search(value))


@dataclass(frozen=True)
class InstagramLink:
    """A validated Instagram link: its kind and the segments that name it.

    `key` is the username for a `profile` or a `story`, the shortcode for a
    `post` or a `reel`. `story_pk` is set for a story link that names one
    frame (`/stories/<user>/<pk>/`) and empty for a story-ring link
    (`/stories/<user>/`, the whole current story). Every field has passed
    its charset, so each is safe to put in a URL path or a bridge query as
    it is.
    """

    kind: LinkKind
    key: str
    story_pk: str = ""

    @property
    def url(self) -> str:
        """The link rebuilt on the fixed origin: no query, no fragment. A
        `/reels/` link is rebuilt on the singular `/reel/`."""
        if self.kind == "profile":
            path = f"/{self.key}/"
        elif self.kind == "reel":
            path = f"/reel/{self.key}/"
        elif self.kind == "post":
            path = f"/p/{self.key}/"
        elif self.story_pk:
            path = f"/stories/{self.key}/{self.story_pk}/"
        else:
            path = f"/stories/{self.key}/"
        return f"https://www.instagram.com{path}"


def _split(url: object) -> tuple[SplitResult, list[str]] | None:
    """`url` → its parts and its path segments, one trailing slash ignored;
    `None` for anything that is not a non-blank string urlsplit can read."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    path = parts.path
    if path.endswith("/"):
        path = path[:-1]
    segments = path[1:].split("/") if path.startswith("/") else []
    return parts, segments


def _shape(segments: list[str], *, story_ring: bool = True) -> LinkKind | None:
    if len(segments) == 2 and segments[0] in ("reel", "reels"):
        return "reel"
    if len(segments) == 2 and segments[0] == "p":
        return "post"
    if len(segments) == 3 and segments[0] == "stories":
        return "story"
    if story_ring and len(segments) == 2 and segments[0] == "stories":
        return "story"
    if len(segments) == 1 and segments[0] and segments[0].lower() not in _RESERVED_FIRST_SEGMENTS:
        return "profile"
    return None


def link_shape(url: object, *, story_ring: bool = True) -> LinkKind | None:
    """The kind `url`'s PATH is shaped like, or `None`. No segment, host or
    charset is checked: this is a label, never a licence to read. Act only
    on `parse_link`.

    `story_ring=False` keeps a two-segment `/stories/<user>/` path out of
    the `story` shape. The transcript render passes it: a shared item's
    kind is only established for the three-segment form, so the two-segment
    ring form keeps its non-story rendering there."""
    split = _split(url)
    return None if split is None else _shape(split[1], story_ring=story_ring)


def _origin_ok(parts: SplitResult) -> bool:
    try:
        # `.port` raises on a netloc like `host:abc`, which is a malformed
        # origin and so no link, never a crash.
        return (
            parts.scheme in ("https", "http")
            and parts.hostname in INSTAGRAM_HOSTS
            and parts.port is None
            and parts.username is None
            and parts.password is None
        )
    except ValueError:
        return False


def parse_link(url: object) -> InstagramLink | None:
    """`url` → a validated `InstagramLink`, or `None` when anything about it
    is outside the rules in the module docstring. Unsupported shapes (an
    unknown path, the explore page, a hashtag) are `None` too."""
    split = _split(url)
    if split is None:
        return None
    parts, segments = split
    kind = _shape(segments)
    if kind is None or not _origin_ok(parts):
        return None
    if kind in ("reel", "post"):
        code = segments[1]
        return InstagramLink(kind, code) if SHORTCODE_RE.fullmatch(code) else None
    if kind == "story":
        # `/stories/<user>/<pk>/` names one frame; `/stories/<user>/` (a
        # story-ring share) names the account's whole current story. Both
        # read the same thing, the current story, under the same checks.
        user = segments[1]
        pk = segments[2] if len(segments) == 3 else ""
        if user.lower() in _NOT_A_STORY_OWNER:
            return None
        if is_handle(user) and (not pk or STORY_PK_RE.fullmatch(pk)):
            return InstagramLink("story", user, pk)
        return None
    user = segments[0]
    return InstagramLink("profile", user) if is_handle(user) else None
