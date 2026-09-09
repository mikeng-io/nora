"""The escaping every XML-ish block in the prompt shares (R-13).

`nora.agent.prefill` has escaped group-derived text since the first prefill, and
`nora.doctrine.loader` now renders frontmatter into section attributes and needs
the identical rule. The layer contract runs `nora.agent` -> `nora.doctrine`, so
the loader cannot reach up into `prefill` for it; `nora.domain` is the one place
both of them may import from.

Two functions rather than one because the contexts differ by exactly one
character: `escape` is for text between tags, `escape_attr` is for a value
inside a `"..."` attribute and must also close the quote. Doctrine frontmatter
is operator-authored and not an attack surface today — it is escaped anyway,
because the cost of doing it is nothing and the cost of the habit of not doing
it is R-13.
"""

from __future__ import annotations

from datetime import datetime
from xml.sax.saxutils import escape as _escape


def escape(text: object) -> str:
    """XML-escape text that will sit BETWEEN tags."""
    return _escape(str(text))


def escape_attr(text: object) -> str:
    """XML-escape text that will sit inside a double-quoted attribute value."""
    return _escape(str(text), {'"': "&quot;"})


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
