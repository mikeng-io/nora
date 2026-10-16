"""Markdown emphasis in, real Signal styling out (DEC-82). One grammar, one place.

`**bold**` is forbidden by `voice.md` but the model emits it anyway on long
answers, and Signal renders it as literal asterisks. signal-cli's `--text-style`
carries `start:length:STYLE` ranges, so the emphasis survives and only the
syntax is lost. Headings strip: Signal has no heading style, so a `#` has
nothing to convert to. No markdown syntax reaches the group.

TWO SOURCES OF RANGES OVER ONE STRING
-------------------------------------
Converting `**x**` both REMOVES characters and PRODUCES offsets into what is
left. Those offsets have to index the exact bytes that go on the wire — and so
do the @-mention offsets `nora.agent.outbound` computes, a second, independent
source of ranges over the same string. Get the two out of step and Signal
renders bold over the wrong words, or tags the wrong person, with no error
anywhere (`nora.agent.outbound`'s module docstring is the long version).

So this module never hands back a text and some offsets as two things.
`render_markdown` returns a `Styled` — the final text and its ranges as ONE
frozen value — and everything downstream computes on that value's `.text`:

* `Deliverer.deliver` renders ONCE, measures its guards on `styled.text`, and
  passes the `Styled` (never the draft string) to the composer;
* `OutboundComposer.compose` takes a `Styled` and no raw text, so the
  pre-conversion string is not in its scope to be passed to the mention scanner
  by mistake;
* `Outbound` STORES the `Styled` and exposes `.text`/`.styles` as properties of
  it, so a message whose styles index a different string than its text cannot
  be constructed at all.

WHY THE RENDER RUNS IN `Deliverer` AND NOT INSIDE `compose`
-----------------------------------------------------------
A text transformation normally belongs inside `compose`, ahead of the offset
arithmetic (`nora.agent.outbound`). This one runs earlier because **R-6 and R-18
must see the converted text.** A text transformation in front of a fail-closed
guard can defeat it: `"*+85298765432*a"` scans clean once the asterisks are
gone. So `deliver` checks the drafted form AND the converted form, which means
it holds the converted form before it composes. The transformation still
happens before any offset exists, and `compose` still owns the final text — it
receives it already final.

NARROW ON PURPOSE
-----------------
The delimiter rule is about the ASTERISK RUN and the whitespace INSIDE it,
never about word characters outside it. A `(?<!\\w)`/`(?!\\w)` guard breaks
Cantonese, the language she writes in: `\\w` is Unicode-aware, so 投 and 資 ARE
word characters; in `**投資**方面` the closing `**` is followed by 方, the guard
fails on the real delimiter, and the engine backtracks onto the INNER asterisk
of each pair — emitting `*投資*方面`. Half-stripped output is worse than none:
the asterisks still reach the group AND they move onto the wrong spans
(`重點係**價錢**同**位置**` becomes `重點係*價錢同位置*`). Chinese prose has no
spaces, so emphasis adjacent to CJK is the COMMON shape here. Under conversion
the ranges would also be computed over the mangled string.

Left alone: `3 * 4`, `5*6`, `a * b * c`, a bare `*`, `**`, `****`, `**/*.py`,
and every underscore (`person_id`, `send_reply`, `_x_` — far more often a real
token than emphasis). Emphasis never spans a blank line.

FAIL-SAFE
---------
`render_markdown` never raises. Anything unexpected yields the draft text with
NO styles — visible asterisks in the room, which is R-5's shape applied to
decoration: an unconverted asterisk is visible to everyone and harmless, a
wrong range is invisible to everyone and not. It is never an empty string and
never a refusal, so this cannot turn a reply that would have been sent into an
R-4 `empty_reply`.
"""

from __future__ import annotations

import logging
import re

from ..domain.ports import Style, Styled, TextStyle
from ..domain.utf16 import utf16_len

log = logging.getLogger("nora.agent.markdown")

# Markdown emphasis/heading syntax, as it actually reaches a chat client. See
# the module docstring for what each guard is holding off.
#
#   (?<!\*)(\*{1,3})(?!\*)  a COMPLETE run of 1-3 asterisks — this is what stops
#                           the engine matching half of a `**` pair, and what
#                           leaves `****` and `**/*.py` alone.
#   (?!\s)  …  (?<!\s)      no whitespace just inside either delimiter, which is
#                           what keeps `3 * 4` and `a * b * c` intact.
#   [^\n]+?                 single line only. Emphasis never spans a blank line,
#                           else two unrelated stray asterisks in different
#                           paragraphs would pair up and eat the text between.
_MD_EMPHASIS = re.compile(r"(?<!\*)(\*{1,3})(?!\*)(?!\s)([^\n]+?)(?<!\s)\1(?!\*)")
# `(?=\S)` so a bare `###` is left as literal text instead of being emptied —
# the strip must never turn a reply that would have been SENT into an R-4
# empty_reply refusal.
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(?=\S)", re.MULTILINE)

# `*x*` is one asterisk, `**x**` two, `***x***` three. Three maps to BOLD and
# not to bold-and-italic: Signal expresses that as two overlapping ranges, and
# a second range reproducing a nuance the model did not mean is more surface
# than the emphasis is worth.
_STYLE_BY_RUN = {1: Style.ITALIC, 2: Style.BOLD, 3: Style.BOLD}

# The substitution repeats until stable, capped at 3, because one pass over
# `**a *b* c**` takes the outer pair and leaves the inner one. The cap stops a
# pathological input from looping; the nesting produces an inner ITALIC inside
# an outer BOLD.
_MAX_PASSES = 3

# `(start, end, style)` in PYTHON CHARACTER offsets over the current string.
# Deliberately not `TextStyle`: these are intermediate, they get remapped by
# every later pass, and converting to UTF-16 once at the end means the
# expensive unit conversion never runs on an offset that is about to move.
_Span = tuple[int, int, Style]


class _Rewrite:
    """One left-to-right pass over a string: what was KEPT, and where it landed.

    A pass deletes only delimiters, so the surviving text is a sequence of runs
    copied in order. Recording those runs as `(old_start, old_end, new_start)`
    is what lets a range discovered by an EARLIER pass be moved onto the string
    a LATER pass produced — which is the whole reason this is a class and not a
    `re.sub` call. `re.sub` gives the new text and destroys the correspondence;
    the correspondence is the part that matters.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._kept: list[tuple[int, int, int]] = []
        self.length = 0

    def keep(self, text: str, start: int, end: int) -> None:
        """Copy `text[start:end]` through to the output, remembering where."""
        if end <= start:
            return
        self._parts.append(text[start:end])
        self._kept.append((start, end, self.length))
        self.length += end - start

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def remap(self, span: _Span) -> _Span | None:
        """`span`, in the coordinates of the string this pass produced.

        A span SHRINKS when characters inside it are deleted — which happens
        every time a nested emphasis is resolved inside an outer one — so the
        endpoints are re-derived from the surviving characters rather than
        shifted by a delta: the new start is where the first surviving
        character of the span landed, the new end is just past the last. A span
        whose every character was deleted returns `None` and is dropped, which
        is the only honest answer: there is nothing left to style.
        """
        start, end, style = span
        new_start: int | None = None
        new_end: int | None = None
        for old_start, old_end, landed in self._kept:
            lo, hi = max(start, old_start), min(end, old_end)
            if lo >= hi:
                continue
            if new_start is None:
                new_start = landed + (lo - old_start)
            new_end = landed + (hi - old_start)
        if new_start is None or new_end is None or new_end <= new_start:
            return None
        return (new_start, new_end, style)


def render_markdown(text: str) -> Styled:
    """`text` with its Markdown syntax replaced by real Signal styling.

    The one public entry point, and the ONLY producer of a `Styled` in `nora/`.
    Never raises: see the module docstring's fail-safe note for why the
    degraded answer is the draft text with no styles rather than a refusal or a
    guess.
    """
    try:
        return _render(text)
    except Exception:                       # styling is decoration; text is not
        log.exception("markdown render failed; sending the draft unstyled")
        return Styled(text=text)


def strip_markdown(text: str) -> str:
    """The text half of `render_markdown` — Markdown syntax removed, no styling.

    NOT a second implementation of the grammar: it is the same call with the
    ranges discarded, so the two can never disagree about what a delimiter is.
    That matters for its one caller: `LeakGuard` normalises the doctrine files
    through here to build its haystack, and a haystack that spoke a different
    Markdown dialect from the reply would miss leaks. It pays for the ranges it
    throws away, a small cost once at startup.
    """
    return render_markdown(text).text


def _render(text: str) -> Styled:
    # Headings first, and they produce no span — Signal has no heading style,
    # so there is nothing to convert to and nothing to remap afterwards. Doing
    # them before any span exists is what keeps that true.
    text = _MD_HEADING.sub("", text)
    spans: tuple[_Span, ...] = ()
    for _ in range(_MAX_PASSES):
        text, spans, changed = _one_pass(text, spans)
        if not changed:
            break
    return Styled(text=text, styles=_as_text_styles(text, spans))


def _one_pass(text: str, spans: tuple[_Span, ...]) -> tuple[str, tuple[_Span, ...], bool]:
    """One emphasis substitution, carrying the existing spans across it.

    Equivalent to `_MD_EMPHASIS.sub(r"\\2", text)` for the TEXT — same engine,
    same left-to-right non-overlapping matches, same pieces concatenated in the
    same order. The third element is False exactly when the substitution would
    have been a no-op: a match always deletes at least two delimiter
    characters, so "no matches" and "no change" are the same fact.
    """
    matches = list(_MD_EMPHASIS.finditer(text))
    if not matches:
        return text, spans, False
    rewrite = _Rewrite()
    fresh: list[_Span] = []
    pos = 0
    for match in matches:
        rewrite.keep(text, pos, match.start())          # the text before it
        inner_start = rewrite.length
        rewrite.keep(text, match.start(2), match.end(2))  # the emphasised text
        if rewrite.length > inner_start:
            fresh.append((inner_start, rewrite.length, _STYLE_BY_RUN[len(match.group(1))]))
        pos = match.end()                               # the delimiters: dropped
    rewrite.keep(text, pos, len(text))
    carried = [moved for span in spans if (moved := rewrite.remap(span)) is not None]
    return rewrite.text, (*carried, *fresh), True


def _as_text_styles(text: str, spans: tuple[_Span, ...]) -> tuple[TextStyle, ...]:
    """Character spans over the FINAL text, converted to Signal's UTF-16 ranges.

    Done once, at the end, against the string that goes on the wire — never
    against an intermediate one. Sorted by position so two renders of the same
    reply produce byte-identical parameters, which is what makes a Langfuse
    trace of one turn comparable to another.
    """
    return tuple(
        TextStyle(
            start=utf16_len(text[:start]),
            length=utf16_len(text[start:end]),
            style=style,
        )
        for start, end, style in sorted(spans, key=lambda span: (span[0], span[1]))
    )
