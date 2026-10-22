"""The public Instagram summons: the typed model
of one summons, the ledger port that makes it answered at most once, and the
ONE rendering of what she reads.

Comprehend once. A summons card from the bridge's SSE stream becomes a
`SummonsCard` (the platform adapter parses it, once); the post and its
comments become a frozen `SummonsContext`; `render_summons` is the only
thing that turns that context into text, and nothing downstream re-reads a
bridge body. The context carries what every judgement she is asked to make
turns on: WHEN (an age on the summons, the post and every comment, and which
end of the thread is newest), WHO (usernames only, never an igid or a
`person_id`), and WHOSE words (everything a stranger wrote is wrapped as a
`social_post` block, never an instruction).

**What never leaves this module into a log line or a span**: a comment id, an
igid, a username, a shortcode, a caption or a comment's text. The ledger's
own uuid is the one handle telemetry may carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, get_args
from uuid import UUID

from nora.domain.blocks import BlockKey, ContextBlock, assemble
from nora.domain.framing import SummonsHead
from nora.domain.prompt import escape

#: Every state a ledger row can be in. Three are OPEN and the rest are final
#: (`finish` refuses to move a final row):
#:
#: * `claimed` — the run owns the row and has not ended;
#: * `staged` — the reply passed every guard and is stored; final in
#:   `dry_run`, and in `live` it is waiting for its flush (or for the
#:   bridge's `too_soon` / a pre-claim 503 to clear);
#: * `flushing` — ONE flush has taken the row (`take_for_flush`) and its POST
#:   is in flight. Nothing else can take it, and a row left here by a dead
#:   process is swept to `ambiguous`, never posted again.
#:
#: A `claimed` row older than the claim timeout is swept to `failed`, never
#: retried (a double public comment is worse than a missing one).
#:
#: `unresolved` is final and costs no read and no model call: the bridge could
#: not find which post the card points at (`resolved = false`), so there is
#: nothing she could answer under.
SummonsState = Literal[
    "claimed", "unresolved", "ignored_summoner", "stale", "unreadable", "silent", "staged",
    "flushing", "published", "refused", "capped", "duplicate", "ambiguous", "failed",
]
#: The states `finish` may move a row OUT of.
OPEN_STATES: tuple[str, ...] = ("claimed", "staged", "flushing")
SUMMONS_STATES: tuple[str, ...] = get_args(SummonsState)

SummonsMode = Literal["dry_run", "live"]
SUMMONS_MODES: tuple[str, ...] = get_args(SummonsMode)

#: `nora_summons_total`'s `outcome` label: every state a run can END in, plus
#: `already_claimed` (another tick or process holds the row, so this run wrote
#: nothing). `claimed` itself is never an outcome: a run that ended there did
#: not end. DERIVED, so the help text and the increments cannot disagree.
ALREADY_CLAIMED = "already_claimed"
SUMMONS_OUTCOMES: tuple[str, ...] = (
    *(s for s in SUMMONS_STATES if s not in ("claimed", "flushing")), ALREADY_CLAIMED,
)

#: `nora_summons_follow_ups_total`'s `outcome` label: what one follow-up
#: (`nora.app.summons.UNSENT_REPLY_FOLLOW_UP`) ended in. `replied` is a
#: `send_reply` that passed the guards and was held (the flush may still
#: refuse it, and a dry run posts nothing); `silent` is nothing held;
#: `timed_out` / `failed` is nothing held because the invocation hit its
#: timeout or raised after the follow-up began.
FollowUpOutcome = Literal["replied", "silent", "timed_out", "failed"]
FOLLOW_UP_OUTCOMES: tuple[str, ...] = get_args(FollowUpOutcome)

#: `nora_summons_intake_total`'s `outcome` label: what became of one `summons`
#: event from the bridge's SSE stream. `queued` reached the consumer's inbox;
#: `disabled` arrived while `[social.instagram.summons]` is off and was
#: dropped; `malformed` failed to parse (fail closed: no row, no answer);
#: `overflow` is an OLDER queued card pushed out of the full inbox by a newer
#: one. DERIVED, so the help text and the increments cannot disagree.
IntakeOutcome = Literal["queued", "disabled", "malformed", "overflow"]
INTAKE_OUTCOMES: tuple[str, ...] = get_args(IntakeOutcome)

#: The longest reply the bridge accepts (its `MaxCommentRunes`, Instagram's
#: own comment limit). A Python `str`'s length is its code-point count, which
#: is what the bridge's `utf8.RuneCountInString` counts.
MAX_COMMENT_CHARS = 2200

#: The longest reply she POSTS, far under Instagram's limit: a comment is a
#: sentence or two (public/voice.md). It is a hard cap, refused whole rather
#: than cut, since a cut fact-check can say the opposite of the whole one. The
#: summons head states this number to her (`SummonsHead`); no doctrine names it.
PUBLIC_REPLY_MAX_CHARS = 320

#: How many of a post's items she is shown, and every one of them is READ
#: before her turn (`nora.app.summons`: `media_fetch` itself, through the
#: `instagram_read` mechanism). A carousel can hold 20; the rest are counted,
#: never silently dropped, and never read: four looks is one wave of
#: `READ_CONCURRENCY`, so a carousel costs no more wall time than one reel.
MAX_ITEMS_SHOWN = 4

#: What looking at one item came to (`ItemReading.state`):
#:
#: * `seen` — read; `text` says what it shows, and a video's `coverage`
#:   sentence says how much of it was watched when that was not all of it;
#: * `withheld` — read, and what it shows carries what looks like a phone
#:   number or ID, so the words are not passed on (`media_fetch`'s screen);
#: * `unreadable` — the look failed, ran out of time, or never got to start:
#:   she is told it could not be read, never left to guess;
#: * `too_long`, `unknown_length`, `over_video_cap` — a video the reading
#:   does not open by design (longer than one reading window, a post video
#:   with no stated length, or past the per-post video cap), said so.
ItemReadState = Literal[
    "seen", "withheld", "unreadable", "too_long", "unknown_length", "over_video_cap",
]
ITEM_READ_STATES: tuple[str, ...] = get_args(ItemReadState)

#: A reading's own ceiling in the render (`render._READING_MAX_CHARS`, the
#: same number `instagram_read` keeps): one reel's reading fits, and the
#: coverage sentence is never the part that is cut.
READING_MAX_CHARS = 1500

#: How many comments beside the summons are kept, nearest first.
NEAREST_COMMENTS = 3


@dataclass(frozen=True)
class SummonsCard:
    """One `summons` event from the bridge's SSE stream: somebody's comment
    (or caption) tagged her, and Instagram told her so as a DM card from the
    commenter. Parsed ONCE, by `nora.platform.instagram.summons`.

    The card does NOT carry the comment's id: the
    occasion finds it by reading the post's comments and matching
    `sender_igid` and her word-bounded `@handle` (`summons_comment`).
    `resolved` is False when the bridge could not name the post (its thread
    refetch failed, or carried no `target_url`); then `shortcode` and
    `media_id` are empty, and the occasion records it and never answers.
    `sender_igid` is "" when the wire's was not a decimal id, which matches
    nobody (the summoner gate fails closed). `text` is the comment text as the
    card showed it, never shown to her: she reads the comment itself.
    """

    message_id: str
    room_id: str
    sender_igid: str
    sender_username: str
    text: str
    shortcode: str
    media_id: str
    received_at: datetime
    resolved: bool


class SummonsSink(Protocol):
    """Where the Instagram adapter hands a parsed summons card: the one seam
    between the platform layer, which reads the SSE stream, and the summons
    occasion, which is composed above it. The production answer is
    `nora.app.summons_inbox.SummonsInbox`. Neither method may block or raise:
    the SSE loop calls them inline."""

    def offer(self, card: SummonsCard) -> None: ...

    def malformed(self) -> None: ...


@dataclass(frozen=True)
class ItemReading:
    """What one item of the post SHOWS, or why that is not known
    (`ItemReadState`). `text` is a vision model's words about bytes a
    stranger posted: untrusted, so it is rendered only inside the post's
    `social_post` block. It is present exactly when `state` is `seen`."""

    state: ItemReadState
    text: str | None = None
    coverage: str | None = None

    def __post_init__(self) -> None:
        if self.state not in ITEM_READ_STATES:
            raise ValueError(f"unknown item reading state {self.state!r}")
        if (self.state == "seen") != bool(self.text and self.text.strip()):
            raise ValueError("a reading carries text exactly when it was seen")
        if self.coverage is not None and self.state != "seen":
            raise ValueError("only a reading that was seen carries a coverage sentence")


@dataclass(frozen=True)
class PostItem:
    """One image or video of the post. `reading` is `None` until the item has
    been looked at (`with_readings`); a context built with no reading at all
    renders each item as not read, never as if it had been."""

    kind: str
    url: str
    duration_seconds: float | None = None
    reading: ItemReading | None = None


@dataclass(frozen=True)
class SummonsPost:
    """`GET /posts/{shortcode}`, read field by field. `more` says the bridge
    sent more items than a carousel holds."""

    author: str | None
    caption: str | None
    taken_at: int | None
    items: tuple[PostItem, ...]
    more: bool = False


@dataclass(frozen=True)
class Comment:
    """One comment. `author_igid` is kept for ONE use, finding the summoner's
    own comment (`summons_comment`); it never reaches a `ThreadRow`, so a
    model never sees one (R-18). "" when the wire's was not a decimal id."""

    id: str
    parent_id: str | None
    author: str | None
    text: str
    at: datetime | None
    author_igid: str = ""


@dataclass(frozen=True)
class ThreadRow:
    author: str | None
    text: str
    at: datetime | None
    is_summons: bool


@dataclass(frozen=True)
class SummonsContext:
    """Everything one summons turn reads, built ONCE and frozen.

    `question` is the summons comment with her own `@handle` removed; empty
    means "the post above" (somebody tagged her with nothing else). `summoner`
    is a username, never a `person_id`: who they are in `identity.toml` is the
    gate's business, not something strangers' thread needs to hear about.
    `thread` is oldest first and keeps the summons plus its nearest
    neighbours; `elided_comments` and `elided_items` count what was left out,
    so an absence is never silent. Each of `items` carries its `reading` once
    the occasion has looked at it (`with_readings`), BEFORE the turn: what a
    post shows is part of what she reads, not something she must go and get.
    """

    summons_id: str
    now: datetime
    question: str
    summoner: str | None
    summoned_at: datetime
    post_author: str | None
    caption: str | None
    post_taken_at: int | None
    items: tuple[PostItem, ...]
    elided_items: int
    thread: tuple[ThreadRow, ...]
    elided_comments: int


@dataclass(frozen=True)
class SummonsRow:
    """One ledger row, as the store reads it back. `comment_id` is `None`
    until the summoner's comment is found on the post (`bind_comment`); an
    `unresolved`, `stale` or `ignored_summoner` row never gets one."""

    id: UUID
    message_id: str
    comment_id: str | None
    media_id: str
    shortcode: str
    author_igid: str
    person_id: str | None
    mention_at: datetime | None
    seen_at: datetime
    state: str
    mode: str
    reply_text: str | None = None
    reply_comment_id: str | None = None
    bridge_code: str | None = None
    trace_id: str | None = None
    updated_at: datetime | None = None


class SummonsLedger(Protocol):
    """One reply per summons, claimed BEFORE the model runs.

    `claim` is the idempotency line: `INSERT ... ON CONFLICT (message_id) DO
    NOTHING`, keyed on the summons CARD's message id, so of two concurrent
    claims on one card exactly one gets an id and the other gets `None` and
    stops. `bind_comment` then records which comment the card resolved to,
    once, and only if no other row holds that comment (`comment_id` stays
    UNIQUE): two cards that resolve to one comment get one answer.

    `finish` moves a row only out of an `OPEN_STATES` state and answers
    whether it did: a final row is final, so a second writer (an old
    container overlapping a new one, a sweep racing a run) cannot overwrite
    what the first recorded. `take_for_flush` is the live write's lock:
    `staged` -> `flushing` atomically, so of two flushes of one row exactly
    one POSTs. `sweep_stale_claims` fails a `claimed` row and makes a
    `flushing` row `ambiguous`, each only once it is older than
    `older_than` — a row another process is still working is left alone.
    """

    async def claim(
        self, *, message_id: str, media_id: str, shortcode: str,
        author_igid: str, mention_at: datetime | None, mode: str, now: datetime,
    ) -> UUID | None: ...

    async def bind_comment(
        self, summons_id: UUID, comment_id: str, *, now: datetime,
    ) -> bool: ...

    async def finish(
        self, summons_id: UUID, state: str, *, now: datetime,
        person_id: str | None = None, reply_text: str | None = None,
        reply_comment_id: str | None = None, bridge_code: str | None = None,
        trace_id: str | None = None, clear_reply: bool = False,
    ) -> bool:
        """Move the row to `state`. A field passed as None is left as it is;
        `clear_reply=True` sets `reply_text` to NULL instead, for a row whose
        staged text must not be kept (a refused disclosure)."""
        ...

    async def take_for_flush(self, summons_id: UUID, *, now: datetime) -> bool: ...

    async def awaiting_flush(self) -> SummonsRow | None: ...

    async def get(self, summons_id: UUID) -> SummonsRow | None: ...

    async def sweep_stale_claims(
        self, *, now: datetime, older_than: timedelta,
    ) -> dict[str, int]: ...


# ---- building the context -------------------------------------------------------


def _handle_pattern(handle: str) -> re.Pattern[str]:
    """Her `@handle` as a WHOLE word, case-insensitive. Not preceded by a
    username character (`me@noraexample` is not a tag), and not followed by
    one either: `@noraexample2` and `@noraexample.x` are somebody else. A
    trailing `.` that ends a sentence is punctuation, since no username ends
    in one.

    `re.ASCII` is load-bearing: case folding is ASCII-only, as the bridge's is.
    Without it, Unicode folding matches a dotless i (U+0131) or a dotted
    capital I (U+0130) against `i`, and a look-alike of her handle spelled
    with one (somebody else's) would read as hers."""
    return re.compile(
        rf"(?<![A-Za-z0-9._])@{re.escape(handle)}(?![A-Za-z0-9_]|\.[A-Za-z0-9_])",
        re.IGNORECASE | re.ASCII,
    )


def tags_handle(text: str, handle: str) -> bool:
    """Whether `text` tags `@handle` as a whole word (`_handle_pattern`)."""
    return bool(handle) and _handle_pattern(handle).search(text) is not None


def question_of(text: str, handle: str) -> str:
    """The summons comment with every `@handle` removed, whitespace collapsed.
    Case-insensitive, and only as a whole handle: `@noraexample2` is somebody
    else."""
    return " ".join(_handle_pattern(handle).sub(" ", text).split())


def summons_comment(
    comments: tuple[Comment, ...], *, igid: str, handle: str, text: str = "",
) -> Comment | None:
    """The comment a summons card stands for: written by the card's SENDER
    (`author_igid == igid`, both non-empty) AND tagging her by `@handle`.

    Of several, the one whose text EQUALS the card's `text` (the card shows
    the comment's own text; whitespace-normalised, otherwise exact) is
    preferred, so a card is answered under the comment it was sent for. Only
    when none equals it is the newest taken (a card is sent for the comment
    just written); one with no time sorts oldest. `None` when none matches:
    she does not answer a summons whose comment she cannot find, and she
    never answers one that somebody else wrote."""
    if not igid:
        return None
    matches = [
        (i, c) for i, c in enumerate(comments)
        if c.author_igid == igid and tags_handle(c.text, handle)
    ]
    wanted = " ".join(text.split())
    exact = [pair for pair in matches if wanted and " ".join(pair[1].text.split()) == wanted]
    pool = exact or matches
    if not pool:
        return None
    return max(
        pool,
        key=lambda pair: (pair[1].at is not None,
                          pair[1].at or datetime.min.replace(tzinfo=UTC), pair[0]),
    )[1]


def nearest_thread(
    comments: tuple[Comment, ...], summons_comment_id: str,
) -> tuple[tuple[ThreadRow, ...], int] | None:
    """The summons plus the `NEAREST_COMMENTS` comments nearest it, oldest
    first, and how many were left out. `None` when the summons is not on the
    page: she does not answer under a thread she cannot see."""
    ordered = sorted(
        enumerate(comments),
        key=lambda pair: (pair[1].at is None, pair[1].at or datetime.min.replace(tzinfo=UTC),
                          pair[0]),
    )
    rows = [c for _, c in ordered]
    index = next((i for i, c in enumerate(rows) if c.id == summons_comment_id), None)
    if index is None:
        return None
    others = sorted(
        (i for i in range(len(rows)) if i != index),
        key=lambda i: (abs(i - index), i),
    )[:NEAREST_COMMENTS]
    kept = sorted([index, *others])
    thread = tuple(
        ThreadRow(author=rows[i].author, text=rows[i].text, at=rows[i].at,
                  is_summons=i == index)
        for i in kept
    )
    return thread, len(rows) - len(kept)


def build_context(
    *, summons_id: str, now: datetime, comment_id: str, summoner: str | None,
    summoned_at: datetime, handle: str, post: SummonsPost,
    comments: tuple[Comment, ...],
) -> SummonsContext | None:
    """Comprehend once. `None` when the summons comment is not among the
    comments read (no reply to what she cannot read).
    `comment_id` is the comment `summons_comment` found; `summoned_at` is the
    card's own time; `summoner` the card sender's username."""
    found = nearest_thread(comments, comment_id)
    if found is None:
        return None
    thread, elided = found
    summons = next(c for c in comments if c.id == comment_id)
    shown = post.items[:MAX_ITEMS_SHOWN]
    return SummonsContext(
        summons_id=summons_id, now=now,
        question=question_of(summons.text, handle),
        summoner=summoner or summons.author,
        summoned_at=summoned_at,
        post_author=post.author, caption=post.caption, post_taken_at=post.taken_at,
        items=shown,
        elided_items=len(post.items) - len(shown) + (1 if post.more else 0),
        thread=thread, elided_comments=elided,
    )


def with_readings(
    ctx: SummonsContext, readings: tuple[ItemReading, ...], *, now: datetime,
) -> SummonsContext:
    """`ctx` with what each of its items shows, one reading per item in the
    context's own order, and `now` moved to when the reading ENDED: the read
    can take minutes, and every age she is shown is measured from `now`. A
    count that does not match is a wiring bug and raises: a reading pinned to
    the wrong item is worse than none."""
    if len(readings) != len(ctx.items):
        raise ValueError(
            f"{len(readings)} reading(s) for {len(ctx.items)} item(s): one per item",
        )
    return replace(ctx, now=now, items=tuple(
        replace(item, reading=reading)
        for item, reading in zip(ctx.items, readings, strict=True)
    ))


# ---- rendering ------------------------------------------------------------------


def age(then: datetime | None, now: datetime) -> str:
    """An age a model can read without a now-anchor (`render._post_age`'s
    shape). Unknown is said as unknown; a time in the future (clock skew) is
    said as "just now" rather than as a negative."""
    if then is None:
        return "at an unknown time"
    delta = int((now - then).total_seconds())
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < 60 * 86400:
        return f"{delta // 86400}d ago"
    return "on " + then.strftime("%Y-%m-%d")


def _epoch(value: int | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, tz=UTC)


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def _who(name: str | None) -> str:
    return f"@{name}" if name else "someone"


#: What an item line says when there is no reading to show, by state. FACTS
#: ONLY: no instruction to her and no pointer to a tool. What she does with
#: an unread item is `public/conduct.md`'s to say, not this render's (an
#: instruction such as "say you couldn't see it" on a public surface is the
#: doctrine's call, not a data line's).
_NOT_SEEN: dict[str, str] = {
    "withheld": "not shown: what it shows contains what looks like a phone number or ID",
    "unreadable": "could not be read",
    "too_long": "not read: longer than one reading window",
    "unknown_length": "not read: a video of unknown length",
    "over_video_cap": "not read: past the number of videos read per post",
}


def _clip(text: str, limit: int) -> str:
    """One line, brackets turned round (so it cannot forge a media marker,
    `render._clean`'s rule), clamped to `limit` and marked when cut."""
    line = _one_line(text).replace("[", "(").replace("]", ")")
    return line if len(line) <= limit else line[:limit].rstrip() + " …"


def _reading_line(reading: ItemReading | None) -> str:
    """What an item shows, or why that is not known. A video's coverage
    sentence is a suffix the clamp never cuts: a partial read that loses its
    caveat reads as whole."""
    if reading is None:
        return "not read"
    if reading.state == "seen" and reading.text:
        suffix = f" {_clip(reading.coverage, 240)}" if reading.coverage else ""
        return f"shows: {_clip(reading.text, READING_MAX_CHARS)}{suffix}"
    return _NOT_SEEN.get(reading.state, _NOT_SEEN["unreadable"])


#: What the summons prompt carries after its head, declared as data.
#:
#: `render_summons` hands its blocks to `assemble()`, so `order` IS the order
#: the model reads (the goldens in `tests/unit/test_summons_assembly_goldens.py`
#: pin the whole prompt byte for byte). Every row is the ONE type
#: `social_post`, so a row says WHICH of the
#: three it is by its `source`, the fourth field, and not by its name: a table
#: keyed on the name alone could hold one row, and the order of the three would
#: be the order they were listed in, a comment and not data. `key="turn"`
#: throughout: this is the turn's own message, no cached prefix.
#:
#: The head is NOT in the table. It is the occasion's own sentences (what she is
#: being asked, the clock, who tagged her, and when), authored text with no
#: opening tag, and a `ContextBlock` cannot render without one; carrying it as a
#: block would add bytes the model reads. It stays
#: the first line of the prompt (`_head`).
_BLOCKS: tuple[tuple[str, int, BlockKey, str], ...] = (
    ("social_post", 10, "turn", "the instagram post"),
    ("social_post", 20, "turn", "comments under that post"),
    ("social_post", 30, "turn", "what the comment that tagged you asks"),
)

#: `source -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    source: (order, key) for _name, order, key, source in _BLOCKS
}

#: What `assemble` puts between the head and the blocks, and between the blocks.
_SEP = "\n"


def _social_post(source: str, text: str) -> ContextBlock:
    """One stranger-written piece, as the block `wrap_outside_text` builds (the
    same name, the same `source` attribute, the same `escape`: R-13), at its
    row in `_BLOCKS` and kept when its body is empty. A question or a line of
    control characters escapes to nothing, which renders as an empty pair that
    `assemble` would otherwise drop."""
    order, key = _ROW[source]
    return ContextBlock(
        name="social_post", attrs=(("source", source),), body=escape(text),
        order=order, key=key, when_empty="render",
    )


def _head(ctx: SummonsContext) -> str:
    """What she is reading, in our own sentences: the first line of the prompt,
    outside every wrap (nothing a stranger wrote is in it but a username). The
    text is `nora.domain.framing.SummonsHead`; this says who and when."""
    now = ctx.now
    return SummonsHead(
        now=now, summoned_by=_who(ctx.summoner), summoned=age(ctx.summoned_at, now),
        max_chars=PUBLIC_REPLY_MAX_CHARS,
    ).render()


def render_summons(ctx: SummonsContext) -> str:
    """The ONE rendering of a summons: what she is reading, then the post, the
    thread and the question, each stranger-written part wrapped as a
    `social_post` block. Our own sentences stay outside the wraps; nothing a
    stranger wrote is outside one."""
    now = ctx.now
    post_lines = [
        f"posted by {_who(ctx.post_author)} "
        f"{age(_epoch(ctx.post_taken_at), now)}",
        f"caption: {_one_line(ctx.caption) or '(none)'}",
    ]
    # No "item N" label and no URL: "item 1" could be passed to media_fetch as
    # if it were a media code, and the pre-read has already looked at each
    # one, so a link invites a second, wrong look.
    for i, item in enumerate(ctx.items, 1):
        dur = (f", {item.duration_seconds:g} seconds" if item.duration_seconds is not None else "")
        post_lines.append(f"the post's {_ordinal(i)} {item.kind}{dur}:")
        post_lines.append(f"  {_reading_line(item.reading)}")
    if ctx.elided_items:
        post_lines.append(f"({ctx.elided_items} more item(s) not listed or read)")
    thread_lines = [
        "Oldest first; the last line is the newest.",
    ]
    if ctx.elided_comments:
        thread_lines.append(
            f"({ctx.elided_comments} other comment(s) on this post are not shown)"
        )
    for row in ctx.thread:
        mark = " [the comment that tagged you]" if row.is_summons else ""
        thread_lines.append(
            f"{_who(row.author)}, {age(row.at, now)}{mark}: {_one_line(row.text)}"
        )
    question = ctx.question or "(nothing but the tag: they mean the post above)"
    blocks = [
        _social_post("the instagram post", "\n".join(post_lines)),
        _social_post("comments under that post", "\n".join(thread_lines)),
        _social_post("what the comment that tagged you asks", question),
    ]
    return f"{_head(ctx)}{_SEP}{assemble(blocks, sep=_SEP)}"


@dataclass(frozen=True)
class SummonsOutcome:
    """What one run did: the ledger state it left, the bridge code behind it
    when there was one, and whether the run called the bridge at all. The
    consumer backs off on a `needs_human` or 503 answer and clears its backoff
    only on a run that reached the bridge and was answered: a card filed
    without a bridge call (unresolved, stale, a stranger) says nothing about
    the session."""

    state: str
    bridge_code: str | None = None
    reached_bridge: bool = False


def _ordinal(n: int) -> str:
    """1 -> "first" ... a word, never a number that reads like a code."""
    words = ("first", "second", "third", "fourth", "fifth", "sixth")
    return words[n - 1] if 0 < n <= len(words) else f"number {n}"
