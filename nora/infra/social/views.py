"""The typed views an Instagram read is built into, and nothing else.

A view is the ONLY thing that crosses from what the bridge said to what the
model reads. `InstagramSocialClient` builds each one field by field from the
bridge's JSON, with a value check per field (a handle is shape checked, a url
must be on an Instagram CDN host, a count is a non-negative `int` and
never a `bool`, free text is a `str` or nothing), and `render.render` is the
only code that turns one into the dict the model is handed. Nothing from the
bridge is ever passed through raw, so a body that grows a field grows nothing
here until somebody writes the field AND its check.

**No field can hold an identifier (R-18).** There is no `id`, `igid`, `fbid`,
`pk` or shortcode on any view, and `tests/unit/test_instagram_views.py` reads
every view's fields to keep it that way: a frame id of the form `<pk>_<owner igid>`
has nowhere to go.

Frozen, because a view is a fact about one read: a reading is added by
building a new `ItemView` (`dataclasses.replace`), never by editing the one
the client built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: An item's kind, normalised by the client: anything the bridge sends that
#: is not `image` or `video` is `media`, so the vocabulary cannot grow
#: silently.
ItemKind = Literal["image", "video", "media"]

#: Per-item state distinguishes seen, withheld, failed and timed-out looks.
#: Unseen items state their budget, length or cap reason and carry no description.
#: A custody code is independent of the reading outcome.
ReadingState = Literal[
    "seen", "withheld", "failed", "timed_out", "not_started",
    "too_long", "over_video_cap", "unknown_length",
]

#: The states that mean «not read HERE, by design; `media_fetch(url=...)` on
#: its link reads it». Distinct from a look that failed: nothing about the
#: item is unknown, and she must not say she could not see it.
NOT_READ_HERE: frozenset[str] = frozenset({"too_long", "over_video_cap", "unknown_length"})

#: A result's status. `ambiguous` is the router's alone and never reaches a
#: view; `refused` is here because the envelope names every non-`ok` answer a
#: read can give, even though today only the router gives it.
ResultStatus = Literal["ok", "nothing_found", "unavailable", "refused"]


@dataclass(frozen=True)
class Reading:
    """What one item showed, or why that is not known. `text` is present
    only when `state` is `seen`: it is a vision model's words about bytes a
    stranger posted, so it is untrusted, and the renderer wraps it.

    `coverage` is a video reading's own admission that it did not see all of
    the clip («...I have not seen the rest»), split
    off `text` so the renderer can clamp the description and never the
    admission: a partial read that loses its caveat reads as whole."""

    state: ReadingState
    text: str | None = None
    coverage: str | None = None
    #: The custody handle `media_fetch(code=...)` resolves when the look held
    #: the item's media — a content hash, never an identifier, so it is not
    #: a private account identifier. A re-look by code never re-types the
    #: signed url, which is what makes it stable where the link expires.
    code: str | None = None

    def __post_init__(self) -> None:
        if (self.state == "seen") != bool(self.text):
            raise ValueError("a reading carries text exactly when it was seen")
        if self.coverage is not None and self.state != "seen":
            raise ValueError("only a reading that was seen carries a coverage sentence")


@dataclass(frozen=True)
class ItemView:
    """One item of a post, or one frame of a story.

    `url` is the item's own CDN url, host checked by the client: it is how a
    reading is taken and the handoff for a follow-up
    `media_fetch(url=..., question=...)`. `taken_at`, `caption` and `alt_text`
    are a story FRAME's own (a post's caption is the post's, on `PostView`).
    `reading` is `None` when nobody looked: past the cap, or a read with no
    media reading at all.
    """

    kind: ItemKind
    url: str
    duration_seconds: float | None = None
    taken_at: int | None = None
    caption: str | None = None
    alt_text: str | None = None
    reading: Reading | None = None


@dataclass(frozen=True)
class PostView:
    """One post or reel, from a pasted link. `more` says the bridge sent more
    items than a carousel can hold (20), so some were not kept."""

    author: str | None
    caption: str | None
    taken_at: int | None
    items: tuple[ItemView, ...]
    more: bool = False


@dataclass(frozen=True)
class PostSummary:
    """One timeline entry, with a readable link instead of network ids."""

    link: str
    kind: Literal["post", "reel"]
    taken_at: int
    caption: str | None
    collab: bool


@dataclass(frozen=True)
class PostsView:
    posts: tuple[PostSummary, ...]


@dataclass(frozen=True)
class StoryView:
    """A person's current story. `expires_at` is the reel's own expiry, which
    bounds every frame; each frame's posting time is on its `ItemView`."""

    author: str | None
    expires_at: int | None
    items: tuple[ItemView, ...]


@dataclass(frozen=True)
class ProfileSocial:
    """The half of a profile the bridge's SECOND call reads. Absent on the
    `ProfileView` when that call failed, which is a different fact from a
    blank bio."""

    followed_by: bool | None
    is_private: bool | None
    follower_count: int | None
    following_count: int | None
    media_count: int | None
    clips_count: int | None
    biography: str | None


@dataclass(frozen=True)
class ProfileView:
    """An account. `full_name` and the bio are the account's own words."""

    username: str
    full_name: str | None
    following: bool | None
    has_story: bool | None
    social: ProfileSocial | None


@dataclass(frozen=True)
class AccountView:
    """One account a search matched: its handle, whether Instagram verified
    it (`True` only when the bridge said so), and its display name."""

    username: str
    is_verified: bool
    full_name: str | None


@dataclass(frozen=True)
class SearchView:
    """The accounts shown, in Instagram's order; `truncated` says the search
    matched more than were kept."""

    accounts: tuple[AccountView, ...]
    truncated: bool


View = PostsView | PostView | StoryView | ProfileView | SearchView


@dataclass(frozen=True)
class Result:
    """One read's answer, before it is rendered.

    Total by construction: `ok` carries a view and no note of its own (the
    renderer frames a view, because what the note says depends on what the
    view holds); every other status carries Nora's sentence and no view.
    """

    status: ResultStatus
    note: str = ""
    view: View | None = None

    def __post_init__(self) -> None:
        if self.status == "ok":
            if self.view is None or self.note:
                raise ValueError("an ok result carries a view and no note of its own")
        elif self.view is not None or not self.note:
            raise ValueError("a result that is not ok carries a note and no view")
