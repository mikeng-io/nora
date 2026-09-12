"""One context block, and the parameters that shape one — issue #220.

**The shape this replaces.** Nine renderers turn records into prompt lines and
none of them share a renderer; what they share is a bag of helpers each must
remember to call — `escape`, `age`, the identity-name projection, the media
join, `one_line`. Nine renderers times five obligations is ~40 decisions made
by hand across six packages, with nothing asking whether a row of that table is
complete. It never was: the media join shipped to one of eight builders, the
name projection to «two of THREE», `one_line` to one of nine.

The fix is not a fifth gate. It is that those obligations stop being things a
renderer can forget: `render_rows` applies them by construction, and what a
renderer gets to vary is a `RowStyle` — five fields, none of which is a safety
property.

**What is a parameter and what is not.** `escape` and `one_line` are NOT in
`RowStyle`, and that omission is the whole design:

* `escape` closes the tag boundary (R-13).
* `one_line` closes the line boundary (R-13c) — the one `escape` is blind to,
  and the one that spent weeks rendering one member's words under another
  member's name.

A renderer that could switch either off is a renderer that can reintroduce
those, which is the state this module exists to end. They are unconditional
here. What varies — a handle, a clock, brackets, a suffix, a reply mark — is
presentation, and getting it wrong costs legibility rather than attribution.

**Vocabulary is GLOSSARY.md's, deliberately.** `block`, `block source`, `row
style`, `placement`, and never `injection`/`injector` — that word already means
the attack (R-14), the test-seam policy, and `TranscriptInjection`, which is a
whole runtime's assembler rather than one block. The commit that added those
entries carries the argument.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Literal, Protocol, TypeVar

from nora.domain.prompt import age, escape, escape_attr, one_line

#: The record being rendered. A TypeVar rather than a Protocol bound because
#: the nine renderers read three unrelated dataclasses — `TranscriptRow`,
#: `UnattributedRow`, and the mirror's `WindowRow` — and one of them cannot be
#: named here at all: `nora.obs.determination` is gated on importing no
#: `nora.*`, so it can never hand this module its own type. Every field is
#: reached through an accessor the caller supplies, so no shape is assumed.
Row = TypeVar("Row")

#: Where a block goes in the prompt. NOT a `tier` — a tier in this codebase is
#: a cognition layer with its own store and background pass (the note tier, the
#: Dreamer); this is a position in one prompt.
#:
#: The distinction is load-bearing rather than tidy: the static head measures
#: 95-99% prefix-cache hit on the first call of every tag, and one volatile byte
#: placed in `header` ends that hit for everything after it. The clock used to
#: live there, which meant no two tags ever shared a prefix past it.
Placement = Literal["header", "turn"]

#: How the handle at the head of a line is written, or that there is none.
#:
#: `index` is the transcript's `#n` and is a per-render position, not a row id:
#: a closed set of 1..N, so an out-of-range `reply_to` is refusable rather than
#: a lookup that might hit some other row, and no raw identifier reaches the
#: model. `row_id` is the note tier's `[7]`, which IS the row id because the
#: curator's grounding check cites it back. `bullet` is the operator surfaces'
#: `- `, which addresses nothing.
HandleStyle = Literal["none", "index", "row_id", "bullet"]

#: How the time on a line is written, or that there is none.
#:
#: `age` («2h ago») is what a model judging freshness actually needs, and is the
#: one that cut the Relevance Gate's error rate by two thirds when it was added
#: to a block that had none. `clock` («[11:00]») and `datetime` are the fold's
#: and the note-candidate index's, which are read against a calendar rather than
#: against now. `none` exists because three renderers genuinely have no time —
#: and per AGENTS.md that is a thing to justify, not a default to reach for.
TimeStyle = Literal["none", "age", "clock", "datetime"]


@dataclass(frozen=True)
class RowStyle:
    """The five things a renderer may vary about one line.

    Every field here is PRESENTATION. Nothing in this dataclass can turn off
    escaping or the one-line collapse, because `render_rows` applies both
    unconditionally — see the module docstring for why that asymmetry is the
    point rather than an oversight.

    The five cover all nine renderers measured 2026-09-12:

        #1 (60m ago) [Grant]: …        index  age       bracketed
           (60m ago) [Grant]: …        none   age       bracketed
        [11:00] Grant: …               none   clock     bare + delivery mark
        [7] (60m ago) Grant: …         row_id age       bare
        [7] (60m ago) you: …             row_id age       bare, self="you"
        7 [2026-09-12 Sat 11:00] G (r): … row_id datetime  bare + room suffix
        - Grant: …                     bullet none      bare

    **Plain data, no callables.** Anything that reads a row — the speaker, the
    body, the clock, a room label — is an accessor passed to `render_rows`
    instead, so a `RowStyle` is comparable, printable and safe to declare at
    module level beside the renderer it configures.
    """

    handle: HandleStyle = "none"
    time: TimeStyle = "none"
    #: `[Grant]` versus `Grant`. Brackets separate the name from a body that
    #: begins with punctuation; the bare form reads better where a clock already
    #: delimits the head of the line.
    bracket_speaker: bool = False
    #: What HER OWN rows are called. The tag prompt uses her display name so the
    #: transcript and `tag_message from=` agree; the curator's own-sends block
    #: uses "you" because the surrounding sentence is addressed to her.
    self_name: str = ""


@dataclass(frozen=True)
class ContextBlock:
    """One rendered block, and everything the assembler needs to place it.

    `header` is the line that introduces this block in the context header's
    «What you can see:» list, and it lives HERE rather than in a hand-written
    list beside it. That is the fix for a catalogue that has gone stale three
    times in this repo: the tool list in `conduct.md` (stale by four of seven),
    the second one in `_context_header` (deleted by DEC-29/DEC-150 and replaced
    with a derived section), and the block list itself, which was missing
    `<loop_decisions>` while that block was being served every turn.

    One declaration, two consumers — the assembled prompt and its own catalogue
    entry — which is exactly `doctrine.render_section`'s trick applied to the
    data side.
    """

    name: str
    placement: Placement
    #: Ascending. The tag prompt's order is an argument its own docstring makes
    #: (her past, then theirs, then the workspace's open questions, then the
    #: room), and it is data here rather than the order of `+` operators in one
    #: expression.
    order: int
    #: The `note=` attribute: what this block IS, in the second person. A block
    #: handed to a model without one is a block it decides the meaning of.
    note: str
    #: The «- name: …» line for the context header. Empty only for a block that
    #: is not part of that catalogue (`<tag_message>`, `<replying_to>`).
    header: str
    body: str
    #: Everything built from what people typed carries it. Default True because
    #: the one block that ever lacked it was the one nobody had decided about.
    untrusted: bool = True

    def render(self) -> str:
        """The block's own bytes — opening tag with its framing, body, close."""
        marker = ' untrusted="true"' if self.untrusted else ""
        return (
            f"<{self.name}{marker} note=\"{escape_attr(self.note)}\">\n"
            f"{self.body}\n"
            f"</{self.name}>"
        )


class BlockSource(Protocol):
    """What builds one block for one turn.

    Built WITH its data rather than taking a context object, because the eight
    runtimes that assemble prompts carry eight different context types and a
    `render(ctx)` signature would have to be generic over all of them for no
    gain. A source is small, per-turn, and knows what it holds.

    `None` means «nothing to say», which is not the same as an empty block:
    every block in the tag prompt omits itself rather than rendering «(none)»,
    because a block that appears whether or not it has content is a block every
    turn pays cache for.
    """

    def build(self) -> ContextBlock | None: ...


def render_rows(
    rows: Sequence[Row],
    *,
    style: RowStyle,
    speaker: Callable[[Row], str],
    body: Callable[[Row], str | None],
    timestamp: Callable[[Row], int | None] = lambda _: None,
    row_id: Callable[[Row], object] = lambda _: "",
    is_self: Callable[[Row], bool] = lambda _: False,
    reply_mark: Callable[[Row], str] = lambda _: "",
    suffix: Callable[[Row], str] | None = None,
    now: datetime | None = None,
    tz: tzinfo | None = None,
) -> str:
    """Records in, one line each, joined with `\\n`.

    **The two obligations are applied here and cannot be waived.** `one_line`
    collapses the body before `escape` escapes it, in that order, for the reason
    `_snippet`'s clip is ordered the same way: a collapse that runs after
    escaping can land inside an entity and emit `&am`.

    The accessors are callables rather than attribute names because the rows are
    three different dataclasses across the nine renderers (`TranscriptRow`,
    `UnattributedRow`, the mirror's `WindowRow`) and one of them is deliberately
    structural — `nora.obs.determination` may import no `nora.*` at all, so it
    can never hand this module its own type.
    """
    out = []
    for i, row in enumerate(rows, 1):
        head = _handle(style.handle, index=i, row_id=row_id(row))
        when = _when(style.time, timestamp(row), now=now, tz=tz)
        who = style.self_name if (style.self_name and is_self(row)) else speaker(row)
        who = escape(one_line(who)) + reply_mark(row)
        if suffix is not None:
            tail = escape(one_line(suffix(row)))
            if tail:
                who += f" {tail}"
        if style.bracket_speaker:
            who = f"[{who}]"
        line = f"{head}{when}{who}: {escape(one_line(body(row) or ''))}"
        out.append(line.lstrip() if not head else line)
    return "\n".join(out)


def _handle(style: HandleStyle, *, index: int, row_id: object) -> str:
    if style == "index":
        return f"#{index} "
    if style == "row_id":
        return f"[{row_id}] "
    if style == "bullet":
        return "- "
    return ""


def _when(
    style: TimeStyle, ts: int | None, *, now: datetime | None, tz: tzinfo | None
) -> str:
    """The time, or nothing. A missing timestamp renders NOTHING rather than a
    placeholder — `prefill._parent_of`'s rule that a symbol which can mean
    "unknown" invites a model to emit one."""
    if style == "none" or ts is None:
        return ""
    if style == "age":
        return f"({age(ts, now or datetime.now().astimezone())}) "
    moment = datetime.fromtimestamp(ts / 1000, tz=tz)
    if style == "clock":
        return f"[{moment:%H:%M}] "
    return f"[{moment:%Y-%m-%d %a %H:%M}] "
