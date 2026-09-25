"""The public Instagram summons (design/instagram-adoption/19): the typed model
of one summons, the ledger port that makes it answered at most once, and the
ONE rendering of what she reads.

Comprehend once. A mention from the bridge becomes a `Mention`; the post and
its comments become a frozen `SummonsContext`; `render_summons` is the only
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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, get_args
from uuid import UUID

from nora.domain.blocks import wrap_outside_text

#: Every state a ledger row can be in. `claimed` is the only non-terminal one
#: besides a LIVE `staged` row waiting out the bridge's `too_soon`; the boot
#: sweep turns a `claimed` row left by a dead process into `failed`, never
#: into a retry (a double public comment is worse than a missing one).
SummonsState = Literal[
    "claimed", "ignored_summoner", "stale", "unreadable", "silent", "staged",
    "published", "refused", "capped", "duplicate", "ambiguous", "failed",
]
SUMMONS_STATES: tuple[str, ...] = get_args(SummonsState)

SummonsMode = Literal["dry_run", "live"]
SUMMONS_MODES: tuple[str, ...] = get_args(SummonsMode)

#: `nora_summons_total`'s `outcome` label: every state a run can END in, plus
#: `already_claimed` (another tick or process holds the row, so this run wrote
#: nothing). `claimed` itself is never an outcome: a run that ended there did
#: not end. DERIVED, so the help text and the increments cannot disagree.
ALREADY_CLAIMED = "already_claimed"
SUMMONS_OUTCOMES: tuple[str, ...] = (
    *(s for s in SUMMONS_STATES if s != "claimed"), ALREADY_CLAIMED,
)

#: `nora_summons_polls_total`'s `status` label: what one `GET /mentions` came
#: back as. `needs_human` (the session is checkpointed) and `unavailable` (503,
#: the bridge is reconnecting) are the two the poller BACKS OFF on; `error` is
#: every other failure, including no answer at all.
PollStatus = Literal["ok", "needs_human", "unavailable", "error"]
POLL_STATUSES: tuple[str, ...] = get_args(PollStatus)

#: The longest reply the bridge accepts (its `MaxCommentRunes`, Instagram's
#: own comment limit). A Python `str`'s length is its code-point count, which
#: is what the bridge's `utf8.RuneCountInString` counts.
MAX_COMMENT_CHARS = 2200

#: How many of a post's items are listed by url. A carousel can hold 20; the
#: rest are counted, never silently dropped.
MAX_ITEMS_SHOWN = 5

#: How many comments beside the summons are kept, nearest first.
NEAREST_COMMENTS = 3


@dataclass(frozen=True)
class Mention:
    """One comment-mention from `GET /mentions`. `at` is `None` when the
    bridge omitted it, which is "unknown" and never an epoch."""

    comment_id: str
    media_id: str
    shortcode: str
    author_igid: str
    author_username: str
    at: datetime | None


@dataclass(frozen=True)
class PostItem:
    kind: str
    url: str
    duration_seconds: float | None = None


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
    """One comment. The author's igid is not kept: nothing she reads needs it
    and a model must never see one (R-18)."""

    id: str
    parent_id: str | None
    author: str | None
    text: str
    at: datetime | None


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
    so an absence is never silent.
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
    """One ledger row, as the store reads it back."""

    id: UUID
    comment_id: str
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

    `claim` is the idempotency line: `INSERT ... ON CONFLICT (comment_id) DO
    NOTHING`, so of two concurrent claims on one comment exactly one gets an
    id and the other gets `None` and stops. `finish` records a terminal (or
    live-`staged`) state; `sweep_stale_claims` is the boot recovery.
    """

    async def claim(
        self, *, comment_id: str, media_id: str, shortcode: str,
        author_igid: str, mention_at: datetime | None, mode: str, now: datetime,
    ) -> UUID | None: ...

    async def finish(
        self, summons_id: UUID, state: str, *, now: datetime,
        person_id: str | None = None, reply_text: str | None = None,
        reply_comment_id: str | None = None, bridge_code: str | None = None,
        trace_id: str | None = None,
    ) -> None: ...

    async def seen(self, comment_ids: tuple[str, ...]) -> frozenset[str]: ...

    async def awaiting_flush(self) -> SummonsRow | None: ...

    async def get(self, summons_id: UUID) -> SummonsRow | None: ...

    async def sweep_stale_claims(self, *, now: datetime) -> int: ...


# ---- building the context -------------------------------------------------------


def question_of(text: str, handle: str) -> str:
    """The summons comment with every `@handle` removed, whitespace collapsed.
    Case-insensitive, and only as a whole handle: `@noraexample2` is somebody
    else."""
    pattern = re.compile(rf"@{re.escape(handle)}(?![A-Za-z0-9._])", re.IGNORECASE)
    return " ".join(pattern.sub(" ", text).split())


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
    *, summons_id: str, now: datetime, mention: Mention, summoned_at: datetime,
    handle: str, post: SummonsPost, comments: tuple[Comment, ...],
) -> SummonsContext | None:
    """Comprehend once. `None` when the summons comment is not among the
    comments read (design 19 rule 6: no reply to what she cannot read).
    `summoned_at` is the mention's own time, passed separately because a
    mention with none is stale before this is ever called."""
    found = nearest_thread(comments, mention.comment_id)
    if found is None:
        return None
    thread, elided = found
    summons = next(c for c in comments if c.id == mention.comment_id)
    shown = post.items[:MAX_ITEMS_SHOWN]
    return SummonsContext(
        summons_id=summons_id, now=now,
        question=question_of(summons.text, handle),
        summoner=mention.author_username or summons.author,
        summoned_at=summoned_at,
        post_author=post.author, caption=post.caption, post_taken_at=post.taken_at,
        items=shown,
        elided_items=len(post.items) - len(shown) + (1 if post.more else 0),
        thread=thread, elided_comments=elided,
    )


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


def render_summons(ctx: SummonsContext) -> str:
    """The ONE rendering of a summons: what she is reading, then the post, the
    thread and the question, each stranger-written part wrapped as a
    `social_post` block. Our own sentences stay outside the wraps; nothing a
    stranger wrote is outside one."""
    now = ctx.now
    head = (
        "You were tagged in a comment under a public Instagram post. Whatever "
        "you send is posted once, as a public reply to that comment, where "
        "strangers read it. Saying nothing is a complete answer. It is now "
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC. {_who(ctx.summoner)} tagged "
        f"you {age(ctx.summoned_at, now)}."
    )
    post_lines = [
        f"posted by {_who(ctx.post_author)} "
        f"{age(_epoch(ctx.post_taken_at), now)}",
        f"caption: {_one_line(ctx.caption) or '(none)'}",
    ]
    for i, item in enumerate(ctx.items, 1):
        dur = (f", {item.duration_seconds:g}s" if item.duration_seconds is not None else "")
        post_lines.append(f"item {i}: {item.kind}{dur} {item.url}")
    if ctx.elided_items:
        post_lines.append(f"({ctx.elided_items} more item(s) not listed)")
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
    return "\n".join((
        head,
        wrap_outside_text("social_post", "the instagram post", "\n".join(post_lines)),
        wrap_outside_text(
            "social_post", "comments under that post", "\n".join(thread_lines),
        ),
        wrap_outside_text(
            "social_post", "what the comment that tagged you asks", question,
        ),
    ))


@dataclass(frozen=True)
class SummonsOutcome:
    """What one run did: the ledger state it left, the bridge code behind it
    when there was one, and whether the run reached the bridge beyond the
    mention (so the poller knows whether this tick's one read is spent)."""

    state: str
    bridge_code: str | None = None
    spent_tick: bool = False
