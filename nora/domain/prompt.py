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

from xml.sax.saxutils import escape as _escape


def escape(text: object) -> str:
    """XML-escape text that will sit BETWEEN tags."""
    return _escape(str(text))


def escape_attr(text: object) -> str:
    """XML-escape text that will sit inside a double-quoted attribute value."""
    return _escape(str(text), {'"': "&quot;"})
