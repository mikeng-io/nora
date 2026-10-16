"""The ONE renderer: an Instagram `Result` → the dict the model is handed.

Everything the model reads from an Instagram read is written here and
nowhere else: the `source_kind`, the framing `note`, the machine-usable
`items` and `accounts`, and the `text` block. So the three rules that make an
Instagram result safe to hand a model live in one place, where one test file
can hold them:

* **untrusted text is wrapped and clamped (R-13).** Everything a stranger
  wrote (a caption, a bio, a display name, Instagram's alt text) and
  everything a vision model wrote about a stranger's media (a reading) goes
  into ONE `social_post` block per result, `wrap_outside_text`, after
  `clamp_chars`. None of it goes into `items` or `accounts`, which reach the
  model unwrapped: the dict is handed over whole.
* **no identifier (R-18).** A view has no field that could carry one
  (`views.py`), and nothing here reads anything but a view.
* **the caption sits beside the readings, and LAST.** It is the poster's own
  words, so nothing Nora states follows it, and the clamp cuts it before any
  reading. A reading is model text about stranger bytes and gets the same
  treatment as a caption: one line, square brackets turned round so it cannot
  forge a marker, labelled, clamped to its share.

The profile and search renders are pinned byte for byte by
`tests/unit/test_instagram_profile_search_snapshot.py`. Item urls live in
`items`, not in the text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ...domain.blocks import wrap_outside_text
from .readings import MAX_READ_ITEMS, MAX_READ_VIDEOS
from .results import clamp_chars
from .views import (
    NOT_READ_HERE,
    ItemView,
    PostView,
    ProfileView,
    Reading,
    Result,
    SearchView,
    StoryView,
)

#: What a story result IS, in a field nothing can mistake for a URL. A model
#: cannot attribute what it was not told, and "there is a `taken_at` in here"
#: is not being told.
SOURCE_KIND = "instagram_story"

#: What a profile result is.
PROFILE_SOURCE_KIND = "instagram_profile"

#: What an account search result IS.
SEARCH_SOURCE_KIND = "instagram_account_search"

#: What a post result is. A reel is a post to the bridge: one route.
POST_SOURCE_KIND = "instagram_post"

#: The story framing, carried BY the result (ADR 0015). Short on purpose —
#: it is re-sent on every later model call in the turn — and it names the
#: contrast rather than a rule, because the failure is a phrasing that reads
#: as knowledge rather than a phrasing that breaks a policy.
STORY_FRAMING = (
    "This is their Instagram STORY: it disappears about 24h after it was "
    "posted (`taken_at`, and `expires_at` is when the links die). If you "
    "mention any of it, say it was their story and when: «his story from "
    "this morning», never «he was at the gym». You saw something "
    "perishable; you don't know where he was."
)

#: Added to the story framing when the frames were looked at: what the two
#: kinds of text in a frame ARE, and that they can disagree. A shows line is
#: NOTES FOR HER, so she can see the frame too: read as a description to hand
#: on, it would be relayed item by item in its own order. A story is read by
#: person or handle, so nothing here claims the asker has seen it.
STORY_READ_FRAMING = (
    "Each shows line is a vision model's notes on that frame, so you can see "
    "it too, and a caption is what they wrote; the two can disagree. A frame "
    "with no shows line says why, and where it can be read."
)

#: Added to the story framing when no frame was seen because they were not
#: read HERE (a long video, the video cap, the item cap): each can be read on
#: request, so she is sent to `media_fetch`, never told she could not see it.
STORY_NOT_READ_FRAMING = (
    "Its frames were not read here (each says why): call media_fetch(url=...) "
    "on a frame's link in items to see it, and don't describe a frame from "
    "its caption alone."
)

#: Added to the story framing when every look that ran failed and some
#: frames were not read here: only those can still be opened.
STORY_MIXED_FRAMING = (
    "None of the frames was seen. The ones that were looked at could not be "
    "seen just now; only the ones marked not read here can be opened with "
    "media_fetch(url=...). Don't describe a frame from its caption alone."
)

#: Added to the story framing when every look at a frame failed.
STORY_UNSEEN_FRAMING = (
    "None of the frames was seen (each says why): say so, and don't describe "
    "them from their captions."
)

#: The post framing when NO media reading was attempted (a caller that
#: passed no `media_fetch`): the result carries the text layer and the
#: links, and seeing an item is `media_fetch` on it.
POST_FRAMING = (
    "One Instagram post: its author, caption and media, in order. This says "
    "nothing about what the media shows: call media_fetch(url=...) on each "
    "item you need to see. The caption is the author's own words."
)

#: The post framing when the items were looked at. It says what she is
#: READING, which is the whole of it: two kinds of text, who wrote each, and
#: that they can disagree. It does not tell her how to settle a
#: disagreement; she has both in front of her. Nor does it ask her to report
#: anything: a «report what an item shows» instruction makes an answer open by
#: translating the shows lines back, in order, to the person who shared the
#: post. A post is read from its link only, so whoever linked it has usually
#: seen it, and the note says so.
POST_READ_FRAMING = (
    "One Instagram post, item by item. The caption is what the poster wrote; "
    "each shows line is a vision model's notes on that item, so you can see "
    "it too. The two can disagree, so read them side by side, and take a "
    "shows line as how it looks, not as fact. Whoever shared or linked the "
    "post has usually seen it. An item with no shows line says why, and "
    "where it can be read; every item's link is in items, in the same order, "
    "for media_fetch(url=...)."
)

#: No item was seen because none was read HERE: a reel longer than one
#: window, a video of unknown length, videos past the cap, items past the
#: first 8. Each can be read on request, so the note sends her to
#: `media_fetch` and never tells her to say she could not see the post.
POST_NOT_READ_FRAMING = (
    "One Instagram post. Its items were not read here (each says why), so "
    "what they show is not in front of you yet: call media_fetch(url=...) on "
    "an item's link in items to see it. The caption is only what the poster "
    "wrote; don't describe the post from it alone."
)

#: Every look that RAN failed, and some items were not read here (past the
#: cap, the video cap, unknown length). Sending her to `media_fetch` on
#: every item would invite a retry of the ones that just failed, so the note
#: says which can still be opened.
POST_MIXED_FRAMING = (
    "One Instagram post. None of its items was seen: the ones that were "
    "looked at could not be seen just now, and only the ones marked not read "
    "here can be opened with media_fetch(url=...) on their link in items. "
    "The caption is only what the poster wrote; don't describe the post from "
    "it alone."
)

#: Every look failed. The post WAS read, so the status stays `ok`; what
#: could be wrong is a claim about what it shows, and that is what this
#: withholds.
POST_UNSEEN_FRAMING = (
    "One Instagram post. None of its items was seen (each says why), so the "
    "caption is all there is, and it is only what the poster wrote. Say you "
    "couldn't see the post; don't describe it from the caption."
)

#: The framing for an `ok` search (ADR 0015: the result explains the status).
#: It says the one thing a model would otherwise assume: the first result is
#: not always the person.
SEARCH_FRAMING = (
    "Accounts Instagram's search matched, in its own order. The first is not "
    "always the one they mean: say which looks like a match and why, or list "
    "them. Display names are written by the account owners."
)

#: A reading's own ceiling. One reel's reading keeps it; several readings
#: share what is left of the text between them (`_lay_out`). The whole
#: reading is not lost past it: `media_fetch(url=...)` on the same item is
#: then a cache hit.
_READING_MAX_CHARS = 1500

#: The shortest a prose run is ever cut to, so a run the layout squeezes
#: still says something rather than « …». A floored run is this plus the
#: two characters of « …», which is what the layout reserves for it.
_PROSE_FLOOR = 24

#: A story frame's caption's and Instagram's description's own ceiling.
_CAPTION_MAX_CHARS = 800

#: A POST caption's ceiling: Instagram's own (2,200), so it comes back
#: whole. The transcript carries only a shared item's first sentence on the
#: promise that this read returns the rest, so the post
#: caption also has FIRST claim on the text budget (`_Prose.first`); the
#: readings share what it leaves, and a reading cut short is whole again
#: through `media_fetch(url=...)` on its link.
_POST_CAPTION_MAX_CHARS = 2200

#: What an item line says when there is no reading to show, by state.
_NOT_SEEN: dict[str, str] = {
    "withheld": "not shown: what it shows has what looks like a phone number or ID in it",
    "failed": "not seen: it could not be looked at just now",
    "timed_out": "not seen: looking at it took too long",
    "not_started": "not seen: there was no time left to look at it",
    "too_long": (
        "not read here: longer than one reading window; "
        "media_fetch(url=...) on its link reads it"
    ),
    "over_video_cap": (
        f"not read here: only {MAX_READ_VIDEOS} videos are read per post or story; "
        "media_fetch(url=...) on its link reads it"
    ),
    "unknown_length": (
        "not read here: a video of unknown length; "
        "media_fetch(url=...) on its link reads it"
    ),
}

#: The line an item past the item cap says, when the others were read.
_PAST_THE_CAP = f"not read here (past the first {MAX_READ_ITEMS}); media_fetch reads it"


def render(result: Result, *, max_chars: int) -> dict[str, Any]:
    """`result` → the model-facing dict. A non-`ok` result is its status and
    Nora's sentence, nothing else; an `ok` one is rendered from its view."""
    if result.status != "ok":
        return {"status": result.status, "note": result.note}
    view = result.view
    if isinstance(view, ProfileView):
        return _profile(view, max_chars)
    if isinstance(view, SearchView):
        return _search(view, max_chars)
    if isinstance(view, PostView):
        return _post(view, max_chars)
    if isinstance(view, StoryView):
        return _story(view, max_chars)
    raise TypeError(f"no render for {type(view).__name__}")


# ---- profile ------------------------------------------------------------------


def _profile(view: ProfileView, max_chars: int) -> dict[str, Any]:
    text, truncated = clamp_chars(_profile_text(view), max_chars)
    return {
        "status": "ok",
        "source_kind": PROFILE_SOURCE_KIND,
        "text": wrap_outside_text("social_post", "instagram profile", text),
        "truncated": truncated,
    }


def _profile_text(view: ProfileView) -> str:
    """The lines the model reads: identity, follow state both ways, private
    or public, the counts, whether a story is up, the bio. A value the
    client could not check is `None` and is left out, never guessed. Neither
    id is here: a `ProfileView` has none."""
    head = f"@{view.username}"
    if view.full_name is not None:
        head += f" ({view.full_name})"
    lines = [head]
    social = view.social

    follows = _yes_no(view.following)
    followed_by = _yes_no(social.followed_by) if social else None
    follow_line = []
    if follows is not None:
        follow_line.append(f"Nora follows them: {follows}")
    if followed_by is not None:
        follow_line.append(f"they follow Nora: {followed_by}")
    if follow_line:
        lines.append(" · ".join(follow_line))

    if social:
        if social.is_private is not None:
            lines.append("Private account" if social.is_private else "Public account")
        counts = [
            f"{n} {label}"
            for n, label in (
                (social.follower_count, "followers"),
                (social.following_count, "following"),
                (social.media_count, "posts"),
                (social.clips_count, "reels"),
            )
            if n is not None
        ]
        if counts:
            lines.append(" · ".join(counts))

    has_story = _yes_no(view.has_story)
    if has_story is not None:
        lines.append(f"Story up now: {has_story}")

    if social:
        if social.biography is not None:
            lines.append(f"Bio: {social.biography}")
        else:
            # Read, and empty: a fact about the account, said as one.
            lines.append("Bio: (none)")
    else:
        # The second call failed. Said outright, so an unread bio is never
        # reported as an empty one and «public or private» is never guessed.
        lines.append(
            "Private/public, follower counts, whether they follow Nora, and "
            "the bio could not be read just now."
        )
    return "\n".join(lines)


# ---- search -------------------------------------------------------------------


def _search(view: SearchView, max_chars: int) -> dict[str, Any]:
    text, _clamped = clamp_chars(_accounts_text(view), max_chars)
    return {
        "status": "ok",
        "source_kind": SEARCH_SOURCE_KIND,
        "note": SEARCH_FRAMING,
        # References only, shape-checked, no prose: the handle a follow-up
        # names and the badge. Display names are NOT here.
        "accounts": [
            {"username": account.username, "is_verified": account.is_verified}
            for account in view.accounts
        ],
        # Every account with its display name, WRAPPED and CLAMPED as one
        # block (R-13), the only place a display name goes.
        "text": wrap_outside_text("social_post", "instagram account search", text),
        "truncated": view.truncated,
    }


def _accounts_text(view: SearchView) -> str:
    """One numbered line per account, in order: handle, then the badge
    stated either way, then the display name, LABELLED and LAST.

    The order is the defence, not taste. The name is stranger-written, and
    anything after it on the line reads as its continuation, so nothing Nora
    states may follow it: a name of `Nike), verified (official` after a
    parenthesised name would forge the badge. With the badge
    first and stated for every account, a name claiming the badge lands after
    the real answer, under `name:`."""
    lines = []
    for number, account in enumerate(view.accounts, start=1):
        badge = "verified" if account.is_verified else "not verified"
        line = f"{number}. @{account.username}, {badge}"
        if account.full_name is not None:
            line += f", name: {account.full_name}"
        lines.append(line)
    return "\n".join(lines)


# ---- post ---------------------------------------------------------------------


def _post(view: PostView, max_chars: int) -> dict[str, Any]:
    text, shortened = _lay_out(_post_blocks(view, int(time.time())), max_chars)
    out: dict[str, Any] = {
        "status": "ok",
        "source_kind": POST_SOURCE_KIND,
        "note": _post_note(view),
        # References only: the machine-usable handoff to `media_fetch`, in
        # the same order as the numbers in `text`.
        "items": [_item_ref(item) for item in view.items],
        "text": wrap_outside_text("social_post", "instagram post", text),
        "truncated": shortened or view.more,
    }
    if view.author is not None:
        # Shape-checked as a handle, so it cannot carry prose.
        out["author"] = view.author
    if view.taken_at is not None:
        out["taken_at"] = view.taken_at
    return out


def _post_blocks(view: PostView, now: int) -> list[_Block]:
    """Who and when, then one block per item in order (its kind, a video's
    length, and what it shows or why that is not known), then the caption,
    LABELLED and LAST: nothing Nora states follows stranger prose."""
    head = f"Post by @{view.author}" if view.author is not None else "Post"
    if view.taken_at is not None:
        head += _post_age(view.taken_at, now)
    count = len(view.items)
    head += f" · {count} item" + ("" if count == 1 else "s")
    blocks: list[_Block] = [[head]]
    looked = _looked(view.items)
    blocks += [
        _item_block(number, item, now=None, looked=looked)
        for number, item in enumerate(view.items, 1)
    ]
    if view.caption is not None:
        blocks.append([_Prose("caption: ", view.caption, _POST_CAPTION_MAX_CHARS, first=True)])
    return blocks


def _post_note(view: PostView) -> str:
    return {
        "none": POST_FRAMING, "read": POST_READ_FRAMING,
        "not_read": POST_NOT_READ_FRAMING, "mixed": POST_MIXED_FRAMING,
        "unseen": POST_UNSEEN_FRAMING,
    }[_outcome(view.items)]


def _looked(items: tuple[ItemView, ...]) -> bool:
    return any(item.reading is not None for item in items)


def _outcome(items: tuple[ItemView, ...]) -> str:
    """What the readings add up to, for the note:

    * `none` — nothing was looked at (no media reading was asked for);
    * `read` — at least one item was seen (or looked at and withheld);
    * `not_read` — none was seen, no look failed, and at least one was not
      read HERE by design (`NOT_READ_HERE`, or past the item cap): it can be
      read on request, so she is never told she could not see it;
    * `mixed` — none was seen, the looks that ran all failed, and some were
      not read here: only those can still be opened;
    * `unseen` — every look that was made failed, and nothing is left."""
    if not _looked(items):
        return "none"
    readings = [item.reading for item in items]
    if any(r is not None and r.state in ("seen", "withheld") for r in readings):
        return "read"
    not_read = any(r is None or r.state in NOT_READ_HERE for r in readings)
    failed = any(r is not None and r.state not in NOT_READ_HERE for r in readings)
    if not_read and failed:
        return "mixed"
    if not_read:
        return "not_read"
    return "unseen"


# ---- story --------------------------------------------------------------------


def _story(view: StoryView, max_chars: int) -> dict[str, Any]:
    text, truncated = _lay_out(_story_blocks(view, int(time.time())), max_chars)
    out: dict[str, Any] = {
        "status": "ok",
        # WHAT this is, explicitly. A story result that says only «here are
        # some URLs» is a result the model can only describe as a fact about
        # the world, and a fact about the world does not expire.
        "source_kind": SOURCE_KIND,
        "note": _story_note(view),
        # References only; the captions and readings are NOT here, because
        # this dict reaches the model whole and prose in `items` would sit
        # outside the wrap and the clamp.
        "items": [_item_ref(item) for item in view.items],
        "text": wrap_outside_text("social_post", "instagram story", text),
        "truncated": truncated,
    }
    if view.author is not None:
        out["author"] = view.author
    if view.expires_at is not None:
        # The reel's `expiring_at` bounds the whole set, beside the note
        # that refers to it.
        out["expires_at"] = view.expires_at
    return out


def _story_blocks(view: StoryView, now: int) -> list[_Block]:
    """One block per frame in the bridge's order (newest first, the
    protocol's own): its number, kind and AGE, what it shows, then its
    caption and Instagram's description."""
    blocks: list[_Block] = [[f"Story by @{view.author}"]] if view.author is not None else []
    looked = _looked(view.items)
    blocks += [
        _item_block(number, item, now=now, looked=looked)
        for number, item in enumerate(view.items, 1)
    ]
    return blocks


def _story_note(view: StoryView) -> str:
    added = {
        "none": "", "read": STORY_READ_FRAMING,
        "not_read": STORY_NOT_READ_FRAMING, "mixed": STORY_MIXED_FRAMING,
        "unseen": STORY_UNSEEN_FRAMING,
    }[_outcome(view.items)]
    return f"{STORY_FRAMING} {added}" if added else STORY_FRAMING


# ---- items --------------------------------------------------------------------


def _item_ref(item: ItemView) -> dict[str, Any]:
    """The machine-usable reference: kind and url, a video's length, a story
    frame's time. No prose and no reading: those are wrapped, in `text`."""
    ref: dict[str, Any] = {"kind": item.kind, "url": item.url}
    if item.duration_seconds is not None:
        ref["duration_seconds"] = item.duration_seconds
    if item.taken_at is not None:
        ref["taken_at"] = item.taken_at
    return ref


def _item_block(number: int, item: ItemView, *, now: int | None, looked: bool) -> _Block:
    """One item: `N. kind` (a video's length; a story frame's age), then what
    it shows or why that is not known, then a story frame's own caption and
    Instagram's description, stranger prose LAST. When the others were
    looked at and this one was not (past the item cap), its own line says
    so and where it can be read."""
    line = f"{number}. {item.kind}"
    if item.duration_seconds is not None:
        line += f", {round(item.duration_seconds)}s"
    if now is not None:
        line += _age(item.taken_at, now)
    block: _Block = [line]
    if item.reading is not None:
        block.append(_reading_line(item.reading))
    elif looked:
        block.append(_PAST_THE_CAP)
    if item.caption is not None:
        block.append(_Prose("caption: ", item.caption, _CAPTION_MAX_CHARS))
    if item.alt_text is not None:
        block.append(_Prose("Instagram's description: ", item.alt_text, _CAPTION_MAX_CHARS))
    return block


def _reading_line(reading: Reading) -> _Line:
    """What an item shows, as a prose run the layout shortens, with a
    video's coverage sentence as a SUFFIX it never shortens: «I have
    not seen the rest» is the part of a partial read that must survive."""
    if reading.state == "seen" and reading.text:
        suffix = f" {_clean(reading.coverage)}" if reading.coverage else ""
        return _Prose("shows: ", reading.text, _READING_MAX_CHARS, suffix)
    return _NOT_SEEN.get(reading.state, _NOT_SEEN["failed"])


# ---- the layout: everything fits, and nothing is dropped ----------------------


@dataclass(frozen=True)
class _Prose:
    """A run of stranger or model prose on one line. `prefix` and `suffix`
    are Nora's (a label, a coverage sentence) and are always kept whole;
    `text` is what the layout shortens, never past `cap`."""

    prefix: str
    text: str
    cap: int
    suffix: str = ""
    #: Served before the others share the rest (a post's caption), leaving
    #: every other run at least `_PROSE_FLOOR`.
    first: bool = False


_Line = str | _Prose
_Block = list[_Line]


def _lay_out(blocks: list[_Block], max_chars: int) -> tuple[str, bool]:
    """Blocks of lines → `(text, shortened)`, fitted to `max_chars` WITHOUT
    dropping a line.

    Everything that is Nora's is kept whole: a header, each item's number
    line, a not-seen line, the tail, every label, a video's coverage
    sentence. What is left of the budget goes to the prose runs (readings,
    captions, Instagram's descriptions) by water-filling: each gets an equal
    share, capped at its own length, and what a short one does not use goes
    to the longer ones. So a long reading gives way before a short caption
    is touched, and no run is ever cut to nothing: the caption survives a
    twenty-item carousel, and a story frame's caption and alt text are
    budgeted rather than clamped off the end. Cutting whole blocks off the
    end, as `clamp_chars` does, would lose the caption and the last frames.

    A share is exact: a run given `n` characters renders in `n` (its cut
    plus « …»), and every run is first given its floor PLUS those two
    characters, so the parts add up to the budget. When even the floors do
    not fit (a budget smaller than Nora's own lines plus a floor per run,
    which no real configuration is), the BACKSTOP shortens the `first` run,
    the post caption, rather than cutting blocks: the caption is shortened,
    never dropped. Past even that (Nora's own lines alone over budget), the
    last resort cuts the blocks BEFORE the caption and keeps its block."""
    runs = [line for block in blocks for line in block if isinstance(line, _Prose)]
    wants = [len(_cut(_clean(run.text), run.cap)) for run in runs]
    fixed = sum(
        len(line) if isinstance(line, str) else len(line.prefix) + len(line.suffix)
        for block in blocks for line in block
    ) + sum(len(block) - 1 for block in blocks) + 2 * max(0, len(blocks) - 1)
    shares = _allot(runs, wants, max_chars - fixed)
    rendered, shortened = _render_runs(blocks, shares, wants)
    overflow = len("\n\n".join(rendered)) - max_chars
    if overflow > 0:
        # The backstop: take the overflow out of the caption, down to one
        # character and « …», before anything is cut whole.
        for i, run in enumerate(runs):
            if run.first and overflow > 0:
                cut = min(overflow, shares[i] - 3)
                if cut > 0:
                    shares[i] -= cut
                    overflow -= cut
        rendered, shortened = _render_runs(blocks, shares, wants)
    text = "\n\n".join(rendered)
    if len(text) > max_chars:
        shortened = True
        keep_last = any(isinstance(line, _Prose) and line.first for line in blocks[-1])
        if keep_last and len(blocks) > 1:
            room = max(0, max_chars - len(rendered[-1]) - 4)
            head, _ = clamp_chars("\n\n".join(rendered[:-1]), room)
            text = f"{head}\n\n{rendered[-1]}"
        else:
            text, _ = clamp_chars(text, max_chars)
    return text, shortened


def _render_runs(
    blocks: list[_Block], shares: list[int], wants: list[int],
) -> tuple[list[str], bool]:
    """Each block, rendered, with each prose run in exactly its share."""
    shortened = False
    rendered: list[str] = []
    k = 0
    for block in blocks:
        lines = []
        for line in block:
            if isinstance(line, str):
                lines.append(line)
                continue
            share, want = shares[k], wants[k]
            k += 1
            body = _clean(line.text)
            if share >= want:
                piece = _cut(body, line.cap)
                shortened |= len(body) > line.cap
            else:
                piece = _cut(body, max(1, share - 2))
                shortened = True
            lines.append(f"{line.prefix}{piece}{line.suffix}")
        rendered.append("\n".join(lines))
    return rendered, shortened


def _allot(runs: list[_Prose], wants: list[int], room: int) -> list[int]:
    """Every run first gets its floor (the floor plus « …», or all it wants
    if that is less); the `first` runs are then served up to what they want;
    the rest is water-filled among the others. Every share is exact."""
    shares = [min(want, _PROSE_FLOOR + 2) for want in wants]
    left = room - sum(shares)
    if left <= 0:
        return shares
    for i, run in enumerate(runs):
        if run.first:
            extra = min(wants[i] - shares[i], left)
            shares[i] += extra
            left -= extra
    rest = [i for i, run in enumerate(runs) if not run.first]
    extras = _water_fill([wants[i] - shares[i] for i in rest], left)
    for i, extra in zip(rest, extras, strict=True):
        shares[i] += extra
    return shares


def _water_fill(wants: list[int], room: int) -> list[int]:
    """`room` characters shared across runs that each want `wants[i]`: the
    smallest wants are met first, and each run gets at most an equal share
    of what is left."""
    shares = [0] * len(wants)
    left = max(0, room)
    order = sorted(range(len(wants)), key=wants.__getitem__)
    for done, i in enumerate(order):
        shares[i] = min(wants[i], left // (len(wants) - done))
        left -= shares[i]
    return shares


def _clean(text: str) -> str:
    """Stranger or model prose → one line: whitespace, newlines included,
    collapsed (so it cannot start a line of its own), square brackets turned
    round (so it cannot forge a media marker, the transcript's rule for a
    shared item's caption)."""
    return " ".join(text.split()).replace("[", "(").replace("]", ")")


def _cut(text: str, limit: int) -> str:
    """`text` clamped to `limit`, marked with « …» when cut."""
    clamped, _ = clamp_chars(text, limit)
    return clamped


def _yes_no(value: bool | None) -> str | None:
    if value is None:
        return None
    return "yes" if value else "no"


def _post_age(taken_at: int, now: int) -> str:
    """A post's time as an age: a model has no now-anchor to read an epoch
    against. A post does not expire, so there is no 24h clause; past two
    months it is a date."""
    delta = now - taken_at
    if delta < 0:
        return ""
    if delta < 3600:
        return f" · {max(1, delta // 60)}m ago"
    if delta < 86400:
        return f" · {delta // 3600}h ago"
    if delta < 60 * 86400:
        return f" · {delta // 86400}d ago"
    return " · posted " + time.strftime("%Y-%m-%d", time.gmtime(taken_at))


def _age(taken_at: int | None, now: int) -> str:
    """A story frame's time, rendered as an AGE rather than an epoch.

    The note asks the model to attribute what it saw («his story from this
    morning»). That is a judgement about WHEN, and `1789935632` cannot ground
    it: the model has no now-anchor, so it either declines to attribute or
    invents an interval. The epoch stays in
    `items[].taken_at` for anything that computes."""
    if taken_at is None:
        return ""
    delta = now - taken_at
    if delta < 0:  # clock skew between us and Instagram: say nothing rather than "in 3h"
        return ""
    if delta < 3600:
        return f" · {max(1, delta // 60)}m ago"
    if delta < 86400:
        return f" · {delta // 3600}h ago"
    # A story older than 24h should not exist; if one arrives, say so plainly
    # rather than rendering a day count that implies it is still live.
    return f" · {delta // 86400}d ago (past its 24h expiry)"
