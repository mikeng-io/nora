"""The social stream's vocabulary: one source interface per network, the
decision a probe makes (design/cognition-loop/22-social-stream.md §2, §3), and
when a story frame's bytes may go (§5).

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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Literal, get_args

#: A story lives 24 hours; a frame that arrives without an expiry of its own is
#: given the platform's bound.
STORY_LIFETIME_SECONDS = 86_400

#: The longest tagged place kept, in characters. A place's name is a few
#: words; anything longer is not one, and is cut rather than stored whole.
LOCATION_MAX_CHARS = 120


#: The states that hide a recorded item (`social_item_state`) from every
#: reader and the memory index.
ItemState = Literal["gone", "hidden"]
HIDING_STATES: tuple[str, ...] = get_args(ItemState)
#: The state a change of an item's tagged place is recorded as: it carries the
#: new place, NULL when the tag was removed, and hides nothing.
PLACE_STATE = "place"


def published_location(value: object) -> str | None:
    """A tagged place's name as the author published it, made safe to keep:
    one line (every run of whitespace or control characters one space),
    trimmed, cut to `LOCATION_MAX_CHARS`; None when there is none. It is the
    author's words and stays data: it is stored and indexed as text, never
    rendered into an instruction."""
    if not isinstance(value, str):
        return None
    flat = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return flat[:LOCATION_MAX_CHARS].rstrip() or None


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
    #: The place a location sticker tags it with (`published_location`).
    location: str | None = None
    #: False when the source could not read its place: `location` is then
    #: None without meaning "no tag".
    location_known: bool = True


@dataclass(frozen=True)
class StorySnapshot:
    """A story read. `found` False is a complete read: no story is up, or none
    Nora may see."""

    found: bool
    frames: tuple[StoryFrame, ...] = ()


@dataclass(frozen=True)
class ItemRef:
    """One post or reel named by its shortcode: what `read_item` reads a single
    post by."""

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
    """A page of a listing, newest first, each item complete enough to record
    (its media included). `next_cursor` is None at the end."""

    items: tuple[SocialItem, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class MediaItem:
    """One image or video of an item, as the platform serves it.
    `duration_seconds` is a video's length when the platform sent one."""

    kind: Literal["image", "video"]
    url: str
    duration_seconds: float | None = None


ItemKind = Literal["story_frame", "post", "reel"]

#: A `social_poll` row's stage: the probe, or one of the reads. `backfill` is
#: a page of a member's history (§7): their first list read, and each page
#: paged back from it.
PollStage = Literal["probe", "story", "items", "item", "backfill", "relist"]
POLL_STAGES: tuple[str, ...] = get_args(PollStage)

#: A `social_poll` row's outcome. A probe's is `Due.outcome`; a read is
#: `read_ok`; a failure is `halted` (the login, or one read kind), `backed_off`
#: or `error`; `abandoned` is a back-fill page whose cursor was judged bad,
#: which ends that member's back-fill; `unavailable` is a back-fill page that
#: failed unavailable on a tick no other call was answered on (the bridge
#: healing), which does not count against its cursor. The table's CHECK
#: constraint holds the same set.
PollOutcome = Literal[
    "unchanged", "story_due", "items_due", "partial", "count_fell",
    "read_ok", "halted", "backed_off", "error", "abandoned", "unavailable",
]
POLL_OUTCOMES: tuple[str, ...] = get_args(PollOutcome)

#: Why a member's back-fill ended (§7): the grid ended (`next_cursor` null),
#: the item limit was reached, a page held nothing inside the day limit, or
#: its cursor was judged bad.
BackfillEnd = Literal["grid_end", "max_items", "max_days", "bad_cursor"]
BACKFILL_ENDS: tuple[str, ...] = get_args(BackfillEnd)

#: A back-fill cursor is judged bad once the pages sent at it were refused
#: upstream this many times in a row: each refusal backs the login off, and
#: the next try at the same cursor would cost another.
BAD_CURSOR_REJECTIONS = 2
#: ... or failed this many times in a row, whatever the failure (a refusal,
#: an answer the adapter cannot read, not found), so no cursor is sent on
#: every tick for good. A halt, a stale query, or an unavailable page while
#: nothing else is answered (the bridge down) is not the cursor's and does
#: not count.
BAD_CURSOR_FAILURES = 3


def cursor_is_bad(*, rejected: int, failures: int) -> bool:
    """Whether a back-fill cursor whose pages have failed `failures` times in
    a row, `rejected` of them refused upstream, is to be given up."""
    return rejected >= BAD_CURSOR_REJECTIONS or failures >= BAD_CURSOR_FAILURES


#: What became of one item on a back-fill page: `recorded` new, already
#: `known`, or `past_limit` (older than the day limit, or past the item
#: limit), not recorded.
BackfillItemOutcome = Literal["recorded", "known", "past_limit"]
BACKFILL_ITEM_OUTCOMES: tuple[str, ...] = get_args(BackfillItemOutcome)

#: Why the poller did not make a call it had due on a tick: the login is
#: halted or backed off, the hourly budget is spent, the roster could not be
#: read, or the bridge answered nothing on the tick (`bridge_unavailable`,
#: which stops the back-fill for that tick).
SkipReason = Literal[
    "halted", "backed_off", "budget", "roster_unavailable", "bridge_unavailable",
]
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
    #: The place the item is tagged with (`published_location`), None when it
    #: carries none.
    location: str | None = None
    #: False when the source could not read its place (an unknown shape, or a
    #: bridge too old to say): `location` is then None without meaning "no
    #: tag", and no place change is recorded from it.
    location_known: bool = True


def frame_item(*, network: str, account: str, frame: StoryFrame) -> SocialItem:
    """A story frame as the item it is recorded as."""
    return SocialItem(
        network=network, item_id=frame.item_id, account=account, kind="story_frame",
        taken_at=frame.taken_at, expires_at=frame.expires_at, caption=frame.caption,
        alt_text=frame.alt_text, media=(MediaItem(kind=frame.kind, url=frame.url),),
        location=frame.location, location_known=frame.location_known,
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


# -- perception and the stimulus (§5, §7) --------------------------------------

#: What one attempt to perceive an item's media came to: `ok` (read),
#: `nothing_found` (nothing here reads it, or it was read already),
#: `unavailable` (the read failed; tried again on a later day), `failed` (it
#: will not succeed: a permanent reason, or `MAX_PERCEPTION_ATTEMPTS` failed
#: reads; never tried again) or `capped` (the day's cap was spent; left
#: unperceived until a later day). The table's CHECK holds the same set.
PerceptionOutcome = Literal["ok", "nothing_found", "unavailable", "failed", "capped"]
PERCEPTION_OUTCOMES: tuple[str, ...] = get_args(PerceptionOutcome)

#: Where a perceived item came from: the label vocabulary of
#: `nora_perception_total{source}`.
PerceptionSource = Literal["social"]
PERCEPTION_SOURCES: tuple[str, ...] = get_args(PerceptionSource)

Origin = Literal["poll", "backfill"]

#: An item whose read failed this many times is recorded `failed`.
MAX_PERCEPTION_ATTEMPTS = 3

#: Comprehension reasons no retry can change (the bytes, the custody, the
#: configured describers): the item is recorded `failed` at once.
_TERMINAL = frozenset({
    "unsupported_format", "too_large", "unsupported_payload", "blob", "no_index", "ffmpeg",
})
#: Reasons a read ended on before any model was called.
_NO_CALL = _TERMINAL | {"store", "in_flight", "unreadable", "already_read"}


def is_terminal(reason: str) -> bool:
    """A failed read no later day can change (a describer not configured for
    the type counts: that is the deployment, not a hiccup)."""
    return reason in _TERMINAL or (reason.startswith("no_") and reason.endswith("_describer"))


def made_no_call(reason: str) -> bool:
    """Whether a read that ended on `reason` called no model."""
    return reason in _NO_CALL or is_terminal(reason)


def vision_calls(
    *, top: str, duration_seconds: float | None, chunk_seconds: int, chunk_max: int,
) -> int:
    """The model calls one medium's read makes at most: one for an image; for a
    video, one per window of `chunk_seconds` up to `chunk_max` (all of them
    when its length is unknown), plus the text pass that titles it."""
    if top != "video":
        return 1
    if duration_seconds is None or duration_seconds <= 0:
        return chunk_max + 1
    windows = -(-int(duration_seconds) // max(chunk_seconds, 1)) or 1
    return min(windows, chunk_max) + 1


#: A `social` stimulus's `ref` is this, a colon, and the read's id.
SOCIAL_READ_REF = "social_read"


def perception_allowance(
    *, cap: int, poll_used: int, backfill_used: int, origin: Origin,
) -> int:
    """How many more vision calls an item of `origin` may spend in one unit
    today, given what that unit's polled and back-filled items spent.

    A polled item may spend whatever is left of `cap`. A back-filled one may
    spend at most half of what the polled items have left of it, less what
    back-fill already spent, so history never takes more than half of a day the
    news has not used, however many passes run (§7)."""
    left = cap - poll_used - backfill_used
    if origin == "backfill":
        left = min(left, (cap - poll_used) // 2 - backfill_used)
    return max(left, 0)


_KIND_WORDS: dict[str, tuple[str, str]] = {
    "story_frame": ("story frame", "story frames"),
    "post": ("post", "posts"),
    "reel": ("reel", "reels"),
    "thread_post": ("thread post", "thread posts"),
}


def read_item(items: Sequence[tuple[str, bool]]) -> str:
    """A `social` stimulus's `item`: what the read recorded, one
    `[collab:]<kind>` per item in the read's order (`collab:post`, `reel`)."""
    return ",".join(f"collab:{kind}" if collab else kind for kind, collab in items)


def describe_read(item: str) -> str:
    """`read_item`'s words for a model: «2 story frames», «a collab post and a
    reel». Counted per kind, in the order the kinds first appear."""
    counts: dict[str, int] = {}
    for part in filter(None, item.split(",")):
        counts[part] = counts.get(part, 0) + 1
    words = []
    for part, n in counts.items():
        collab, _, kind = part.rpartition(":")
        one, many = _KIND_WORDS.get(kind, (kind.replace("_", " "),) * 2)
        noun = f"collab {one if n == 1 else many}" if collab else (one if n == 1 else many)
        words.append(f"a {noun}" if n == 1 else f"{n} {noun}")
    if len(words) <= 1:
        return "".join(words) or "an item"
    return ", ".join(words[:-1]) + " and " + words[-1]


def perception_context(
    *, network: str, kind: str, collab: bool, taken_at: datetime, tz: tzinfo,
) -> str:
    """What a describer is told it is reading (instagram-adoption/06: a story
    must be SAID to be a story). Every word is a system fact (the kind the
    platform reports, the collab flag, the time it was posted), never the
    caption, which is the author's text. The time is absolute, because the
    description it shapes is kept for good and an age would go stale in it."""
    platform = network.capitalize()
    when = taken_at.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    if kind == "story_frame":
        what = (
            f"one frame of a story on {platform}, posted {when} (a story "
            "disappears a day after it is posted)"
        )
    else:
        noun = _KIND_WORDS.get(kind, (kind.replace("_", " "), ""))[0]
        what = f"a {noun} on {platform}, posted {when}"
    if collab:
        what += ", made together with another account"
    return f"This is {what}, from a member of the group Nora is in."


# -- a story frame's bytes (§5) -------------------------------------------------

#: Why a story frame's bytes were deleted: `embedded` at its `expires_at` plus
#: `[social] story_media_keep_days`, once its perception and its embedding both
#: exist; `hard_limit` at `expires_at` plus `story_media_keep_max_days`,
#: whatever exists by then. The tombstone's CHECK holds the same set.
ByteDeletionReason = Literal["embedded", "hard_limit"]
BYTE_DELETION_REASONS: tuple[str, ...] = get_args(ByteDeletionReason)


def bytes_due(
    *, expires_at: datetime, now: datetime, perceived: bool, embedded: bool,
    keep_days: int, max_days: int,
) -> ByteDeletionReason | None:
    """Whether a story frame's bytes go now, and why; None while they stay.

    Nothing goes before `expires_at` plus `keep_days`. From then the bytes go
    once the frame is both perceived and embedded, because the description and
    the vector are what stay of it; at `expires_at` plus `max_days` they go
    regardless, and the frame stays unembedded for good."""
    if now >= expires_at + timedelta(days=max_days):
        return "embedded" if perceived and embedded else "hard_limit"
    if now >= expires_at + timedelta(days=keep_days) and perceived and embedded:
        return "embedded"
    return None


# -- what Character reads (§6) ---------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """What perception made of one medium of an item: the three comprehension
    fields `media_objects` holds for its bytes, empty when nothing read them."""

    title: str = ""
    description: str = ""
    content: str = ""


@dataclass(frozen=True)
class PersonSocialItem:
    """One of a person's readable social items as their Character session is
    handed it (`SqlSocialStreamStore.person_items`): what it is, when it was
    posted, their caption and what perception made of its media. Never its
    `location`, `alt_text`, tagged or mentioned handles."""

    network: str
    item_id: str
    kind: str
    collab: bool
    taken_at: datetime
    origin: Origin
    caption: str | None
    readings: tuple[Reading, ...] = ()
    #: When it was recorded, in microseconds since the epoch: the position a
    #: person session's social cursor advances to once it is handed.
    recorded_us: int = 0


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_us(moment: datetime) -> int:
    """`moment` in whole microseconds since the epoch, exactly (a float
    timestamp loses the last digit)."""
    return (moment - _EPOCH) // timedelta(microseconds=1)


def from_us(us: int) -> datetime:
    """The inverse of `to_us`."""
    return _EPOCH + timedelta(microseconds=us)
