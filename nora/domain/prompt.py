"""The escaping every XML-ish block in the prompt shares (R-13).

`nora.agent.prefill` has escaped group-derived text since the first prefill, and
`nora.doctrine.loader` now renders frontmatter into section attributes and needs
the identical rule. The layer contract runs `nora.agent` -> `nora.doctrine`, so
the loader cannot reach up into `prefill` for it; `nora.domain` is the one place
both of them may import from.

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
#: **Found by a component test that PARSED a rendered prompt instead of
#: substring-matching it.** A member's body containing U+000B renders
#: `<m n="1">ok\x0bhidden</m>`, which is not well-formed XML — and the element
#: form's whole argument is that the tag is a delimiter a body cannot break.
#: `escape` handles `&`, `<`, `>` and is blind to these exactly as it was blind
#: to the newline; the body is deliberately NOT collapsed, so nothing else was
#: going to catch them.
#:
#: Stripped rather than replaced with a space: a control character carries no
#: meaning a reader loses, and a substitution would change the body's length
#: for a caller that bounds it.
_XML_FORBIDDEN = {
    c: None for c in [*range(0x20), 0x7F]
    if c not in (0x09, 0x0A, 0x0D)
}


def _legal(text: object) -> str:
    """`str()`, with the characters XML cannot carry at all removed."""
    return str(text).translate(_XML_FORBIDDEN)


def escape(text: object) -> str:
    """XML-escape text that will sit BETWEEN tags."""
    return _escape(_legal(text))


def escape_attr(text: object) -> str:
    """XML-escape text that will sit inside a double-quoted attribute value."""
    return _escape(_legal(text), {'"': "&quot;"})


def age(ts_ms: int, now: datetime) -> str:
    """How long ago something was, for a block a model has to judge freshness
    from. Moved here from `nora.agent.prefill` on 2026-09-10 so it can be ONE
    implementation: `nora.language` renders prompt blocks too and sits below
    `nora.agent` in the layer contract, so it could not reach the original and
    the alternative was a second copy.

    The failure it exists for, in `prefill`'s own words: every message looked
    equally recent, "which is what made 今晚 and 琴日 unanswerable even with a
    clock in the header". The Relevance Gate had the same gap on its whole
    transcript and lost two thirds of its error rate closing it.

    An age rather than a timestamp because it is what the question actually
    needs — «is this still current?» — and it costs a few characters per line
    instead of a full datetime.

    NOT the only age vocabulary in the tree yet: `nora.language.proactive`
    renders «2 h» where this renders «2h ago», in a prompt whose wording is
    graded by the `proactive_decide` suite. Merging them changes that prompt,
    so it is a measured change and not a tidy-up.
    """
    seconds = max(0, int(now.timestamp() - ts_ms / 1000))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


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

    Measured on the relevance gate (2026-09-10, commit 99b34e4): a forged
    commitment line took that gate to `go` 5/5, and it STAYED `go` 5/5 against
    a hardened instruction — which is what says this belongs to the renderer
    and not to the wording. The same reading applies to every other block that
    renders a row as a line, and AGENTS.md's own rule is the general form of
    it: never rewrite a model's instruction before reading its input.

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
