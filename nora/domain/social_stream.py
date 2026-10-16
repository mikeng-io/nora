"""The social stream's vocabulary: one source interface per network, and the
decision a probe makes (design/cognition-loop/22-social-stream.md §2, §3).

Pure: no I/O. `SocialSource` (`nora.domain.ports`) is the port; the types a
source answers with, the typed failure a read can end in, and the two functions
that decide what a probe makes due live here.

**A read does not raise.** It answers its value or a `Failed`, and the
failure is typed by what the CALLER must do next, not by what went wrong
upstream: halt the login, back it off, halt one read, or skip this call.

**The marker is the probe's own value.** `decide` compares `latest_reel_media`
with the value that led to the last completed story read, never with a frame's
`taken_at`, so the detector does not depend on the two being the same clock.
An absent count is never read as "unchanged".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, get_args

#: A story lives 24 hours; a frame that arrives without an expiry of its own is
#: given the platform's bound.
STORY_LIFETIME_SECONDS = 86_400


class Cap(StrEnum):
    """What a network can do. A capability a network lacks is absent from its
    `capabilities`, not an error: Threads has no stories, so it never exposes
    `STORY`."""

    PROBE = "probe"
    STORY = "story"
    LIST_ITEMS = "list_items"
    READ_ITEM = "read_item"


class FailureKind(StrEnum):
    """What the caller does about a failed read."""

    #: The login needs a human (checkpoint, logout): every poll for it stops,
    #: and nothing retries on the tick.
    HALT = "halt"
    #: The platform pushed back (rejected, rate limited): pause the login and
    #: double the pause until a clean probe.
    BACK_OFF = "back_off"
    #: The query document rotated: that read halts for everyone, the login is
    #: fine.
    STALE_QUERY = "stale_query"
    #: The bridge has no mechanism for this read; retrying will not help.
    UNSUPPORTED = "unsupported"
    #: Platform answered that the account or item does not exist.
    NOT_FOUND = "not_found"
    #: The bridge is healing, or the call or its answer was unusable: skip this
    #: call, count nothing against the login.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Failed:
    """A read that did not complete. `detail` is a short, non-identifying
    reason for logs and the poll row (an error code, never a message)."""

    kind: FailureKind
    detail: str = ""


@dataclass(frozen=True)
class Account:
    """One member's account on one network. `username` is optional context a
    source may use to skip a lookup; the id is what is addressed."""

    id: str
    username: str | None = None


@dataclass(frozen=True)
class Probe:
    """The cheap look at an account that decides whether a costly read runs.

    `latest_reel_media` is the newest story's time as the platform reports it
    (Unix seconds, 0 when no story is up). The counts are `None` when the
    probe's second half failed (`counts_available` is then False): absent, not
    zero. `rate_limited` is set when that second half failed with a 429."""

    latest_reel_media: int | None
    media_count: int | None
    total_clips_count: int | None
    counts_available: bool
    rate_limited: bool = False


@dataclass(frozen=True)
class StoryFrame:
    """One frame of a story, with its own expiry."""

    item_id: str
    taken_at: datetime
    expires_at: datetime
    kind: Literal["image", "video"]
    url: str
    thumbnail_url: str | None = None
    caption: str | None = None
    alt_text: str | None = None


@dataclass(frozen=True)
class StorySnapshot:
    """A story read. `found` False is a complete read: no story is up, or none
    Nora may see."""

    found: bool
    frames: tuple[StoryFrame, ...] = ()


@dataclass(frozen=True)
class ItemRef:
    """One post or reel as a listing shows it: enough to dedupe, order, and
    read the item in full."""

    account: str
    item_id: str
    shortcode: str
    kind: Literal["post", "reel"]
    taken_at: datetime
    caption: str
    collab: bool
    carousel_count: int = 0


@dataclass(frozen=True)
class Page:
    """A page of a listing, newest first. `next_cursor` is None at the end."""

    items: tuple[ItemRef, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class MediaItem:
    """One image or video of an item, as the platform serves it."""

    kind: Literal["image", "video"]
    url: str


ItemKind = Literal["story_frame", "post", "reel"]

#: A `social_poll` row's stage: the probe, or one of the three reads.
PollStage = Literal["probe", "story", "items", "item"]
POLL_STAGES: tuple[str, ...] = get_args(PollStage)

#: A `social_poll` row's outcome. A probe's is `Due.outcome`; a read is
#: `read_ok`; a failure is `halted` (the login, or one read kind), `backed_off`
#: or `error`. The table's CHECK constraint holds the same set.
PollOutcome = Literal[
    "unchanged", "story_due", "items_due", "partial", "count_fell",
    "read_ok", "halted", "backed_off", "error",
]
POLL_OUTCOMES: tuple[str, ...] = get_args(PollOutcome)

#: Why the poller did not make a call it had due on a tick: the login is
#: halted or backed off, the hourly budget is spent, or the roster could not
#: be read.
SkipReason = Literal["halted", "backed_off", "budget", "roster_unavailable"]
SKIP_REASONS: tuple[str, ...] = get_args(SkipReason)

#: What became of one media item's bytes: `stored` in custody, custody
#: `not_configured`, `store_failed`, or the fetch's own reason
#: (`nora.domain.media.MediaBytes.reason`, the media sources' closed set).
CustodyOutcome = Literal[
    "stored", "not_configured", "store_failed", "not_supported", "no_id",
    "bad_token", "bad_payload", "unreachable", "not_found", "too_large",
]
CUSTODY_OUTCOMES: tuple[str, ...] = get_args(CustodyOutcome)


@dataclass(frozen=True)
class Member:
    """One member's account on the network a poller reads: the person the
    rows are recorded under, and the account that is probed."""

    person: str
    account: Account


@dataclass(frozen=True)
class SocialItem:
    """One recorded unit of a member's social output. No person: which member
    an account belongs to is the caller's. `expires_at` is set for story
    frames only; `collab` marks a post another account owns and this one
    co-authored."""

    network: str
    item_id: str
    account: str
    kind: ItemKind
    taken_at: datetime
    expires_at: datetime | None
    caption: str | None
    alt_text: str | None
    media: tuple[MediaItem, ...]
    collab: bool = False


def frame_item(*, network: str, account: str, frame: StoryFrame) -> SocialItem:
    """A story frame as the item it is recorded as."""
    return SocialItem(
        network=network, item_id=frame.item_id, account=account, kind="story_frame",
        taken_at=frame.taken_at, expires_at=frame.expires_at, caption=frame.caption,
        alt_text=frame.alt_text, media=(MediaItem(kind=frame.kind, url=frame.url),),
    )


@dataclass(frozen=True)
class Counts:
    """The last recorded item counts; either is None when never recorded."""

    media_count: int | None
    total_clips_count: int | None


@dataclass(frozen=True)
class Due:
    """What a probe made due.

    `partial`: the counts were unavailable, so the count comparison was
    skipped; the story comparison stands. `count_fell`: a count went below the
    last recorded one; recorded, and nothing else follows from it."""

    story_due: bool
    items_due: bool
    partial: bool
    count_fell: bool

    @property
    def outcome(self) -> str:
        """The `social_poll` outcome for the probe. A read being due outranks
        the observations beside it, which the row's own counts still carry."""
        if self.story_due:
            return "story_due"
        if self.items_due:
            return "items_due"
        if self.count_fell:
            return "count_fell"
        if self.partial:
            return "partial"
        return "unchanged"


def decide(
    probe: Probe, *, marker: int | None, last_counts: Counts | None,
) -> Due:
    """What `probe` makes due, given the person's read marker (the probe value
    that led to their last completed story read) and the last recorded counts.

    A story read is due when the probe's value differs from the marker and a
    story is up (0 is "no story up", and there is nothing to read). A read that
    found nothing advances the marker like any other, so the same value never
    triggers a second read. A list read is due when a count rose, or when
    counts are present and none were ever recorded. Absent counts compare
    nothing: they are `partial`, never "unchanged"."""
    value = probe.latest_reel_media
    story_due = value is not None and value != 0 and value != marker
    if not probe.counts_available or probe.media_count is None \
            or probe.total_clips_count is None:
        return Due(story_due=story_due, items_due=False, partial=True, count_fell=False)
    rose = fell = False
    if last_counts is None:
        rose = True
    else:
        for now, before in (
            (probe.media_count, last_counts.media_count),
            (probe.total_clips_count, last_counts.total_clips_count),
        ):
            if before is None or now > before:
                rose = True
            elif now < before:
                fell = True
    return Due(story_due=story_due, items_due=rose, partial=False, count_fell=fell)
