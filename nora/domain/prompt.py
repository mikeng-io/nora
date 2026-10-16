"""The escaping every XML-ish block in the prompt shares (R-13).

`nora.agent.prefill` escapes group-derived text, and `nora.doctrine.loader`
renders frontmatter into section attributes and needs the identical rule. The
layer contract runs `nora.agent` -> `nora.doctrine`, so the loader cannot reach
up into `prefill` for it; `nora.domain` is the one place both of them may
import from.

Two escapers rather than one because the contexts differ by exactly one
character: `escape` is for text between tags, `escape_attr` is for a value
inside a `"..."` attribute and must also close the quote. Doctrine frontmatter
is operator-authored and not an attack surface today — it is escaped anyway,
because the cost of doing it is nothing and the cost of the habit of not doing
it is R-13.

`one_line` is the THIRD member of that family and closes the hole the other two
are blind to — see its own docstring. It lives here for the same layering
reason `age` does: `nora.agent`, `nora.engagement`, `nora.ingest`, `nora.app`
and `nora.language` all render transcript rows into line-oriented blocks and
cannot import each other. `nora.obs.determination` is a sixth and is gated on
importing no `nora.*` at all, so it holds the same two-line expression over the
same `str.splitlines` authority, pinned equal to this one by a named test.

**The placement is the fix.** This collapse spent two days as a private
`_one_line` inside `nora.ingest.relevance_gate`, where five of the six could not
reach it — and all five went on rendering raw bodies while that module's own
tests stayed green.
"""

from __future__ import annotations

from datetime import datetime
from xml.sax.saxutils import escape as _escape

#: The C0 control characters XML 1.0 forbids OUTRIGHT — everything below 0x20
#: except tab, newline and carriage return, plus the lone 0x7F. There is no
#: escape for these: `&#11;` is as illegal as the raw byte, so the only
#: conforming rendering is to remove them.
#:
#: A member's body containing U+000B would render
#: `<m n="1">ok\x0bhidden</m>`, which is not well-formed XML, and the element
#: form relies on the tag being a delimiter a body cannot break. `escape`
#: handles `&`, `<`, `>` only, and the body is deliberately NOT collapsed, so
#: nothing else catches these.
#:
#: Stripped rather than replaced with a space: a control character carries no
#: meaning a reader loses, and a substitution would change the body's length
#: for a caller that bounds it.
_XML_FORBIDDEN = {
    c: None for c in [*range(0x20), 0x7F]
    if c not in (0x09, 0x0A, 0x0D)
}


def legal(text: object) -> str:
    """`str()`, with the characters XML cannot carry at all removed. Public
    because a name that is VALIDATED against what was rendered has to be put
    through the same removal the renderer applies (`speaker_of` in
    `nora.language.cognition_render`)."""
    return str(text).translate(_XML_FORBIDDEN)


def escape(text: object) -> str:
    """XML-escape text that will sit BETWEEN tags."""
    return _escape(legal(text))


def escape_attr(text: object) -> str:
    """XML-escape text that will sit inside a double-quoted attribute value."""
    return _escape(legal(text), {'"': "&quot;"})


def age(ts_ms: int, now: datetime) -> str:
    """How long ago something was, for a block a model has to judge freshness
    from. It lives here so there is ONE implementation: `nora.language` renders
    prompt blocks too and sits below `nora.agent` in the layer contract, so it
    cannot import it from `nora.agent`.

    It exists because, without an age, every message looks equally recent,
    which makes 今晚 and 琴日 unanswerable even with a clock in the header.

    An age rather than a timestamp because it is what the question actually
    needs — «is this still current?» — and it costs a few characters per line
    instead of a full datetime.

    `ago` renders a different vocabulary («2 h ago» where this renders «2h
    ago»); merging two vocabularies changes what the model reads, so it is a
    measured change, not a tidy-up.
    """
    seconds = max(0, int(now.timestamp() - ts_ms / 1000))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def ago(delta_ms: int) -> str:
    """A span as a person says it: «just now», «42 min», «3 h 5 min», «2 d».

    It lives here because `nora.domain.computer_sessions` also calls it, and a
    domain module may not import `nora.language`. The wording is NOT `age`'s
    (which says «2h ago»); merging the two changes what the model reads, so it
    is a measured change, not a tidy-up.
    """
    minutes = max(delta_ms, 0) // 60_000
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 24 * 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} h {rest} min ago" if rest else f"{hours} h ago"
    return f"{minutes // (24 * 60)} d ago"


def stamp(ts_ms: int, now: datetime) -> str:
    """When something was, as a calendar moment AND an age:
    «YYYY-MM-DD Thu 21:14 (13h ago)», in `now`'s own zone.

    For a record a model is asked to place in time, not only to judge fresh.
    `age` answers «is this still current»; «what did we say yesterday» turns on
    the calendar day, and «13h ago» read at 10:00 is yesterday only after
    arithmetic across midnight, which a cheap model does badly. The weekday is
    there for «last Tuesday» on the same grounds.

    No zone on the line: the clock line every turn carries names it once
    (`nora.agent.clock.state_time`), and a caller passes `now` already in that
    zone. The age is `age`'s own wording, so a row read through a tool and the
    same row in the prefilled transcript agree on it.
    """
    moment = datetime.fromtimestamp(ts_ms / 1000, tz=now.tzinfo)
    return f"{moment:%Y-%m-%d %a %H:%M} ({age(ts_ms, now)})"


def one_line(body: object) -> str:
    """One transcript row is one line, whatever the sender typed.

    **The hole `escape` cannot see.** A line-oriented block renders a row as
    `[who]: body` and joins the rows with `\n`, so a member whose message
    CONTAINS a newline writes a SECOND line byte-identical to a real row —
    a name, an age, a handle, and words that are not theirs. `escape` handles
    `&`, `<` and `>`; a newline is none of the three, and the forged row sits
    inside the same block as the real ones with nothing to tell them
    apart. It needs no attacker to do damage: an ordinary member
    pressing enter mid-message writes a continuation line with no `[who]:` on
    it at all, and whose words the model then reads as somebody else's.

    A forged row is a defect of the renderer, not of the instruction wording:
    hardening the instruction does not stop the model reading it. The same
    applies to every other block that renders a row as a line.

    A COLLAPSE, not a filter. The text is still quoted in full and still
    judged; only the structural character goes, replaced by the space a reader
    would have put there. Returning the text stripped of nothing would be a
    drop, and a row the model cannot see is its own failure mode.

    **`str.splitlines` decides what a line break IS, not a character class of
    ours**, and that is load-bearing rather than stylistic. A hand-written set
    would be a list that can go stale against the very thing it has to agree
    with — the renderers join with `\n` and several consumers re-split a
    rendered block — and `nora.obs.determination`, which may import no `nora.*`
    at all, would have had to copy that list to match. Asking the standard
    library leaves NO list to copy, so the two implementations are the same
    expression over the same authority rather than two tables that drift;
    `tests/unit/test_determination.py` pins them equal over every separator
    Python knows. `\v`, `\f`, U+001C-U+001E and U+0085 are in that answer and
    would not have been in a hand-written one.
    """
    return " ".join(
        part.strip() for part in str(body).splitlines() if part.strip()
    )


def clip(text: object, limit: int) -> str:
    """One line, cut to `limit` characters: `one_line` first, so a newline can
    never survive the cut, then an ellipsis where the text was longer. At least
    one character of the text is kept (`limit` below 2 keeps one), which is what
    lets a row that must be shown at all be shown clipped rather than lost.

    Shared by the Cognition renderer's message words and the event renderer's
    venue."""
    line = one_line(text)
    if len(line) <= limit:
        return line
    return line[: max(limit - 1, 1)].rstrip() + "…"

