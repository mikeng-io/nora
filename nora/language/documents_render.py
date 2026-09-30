"""`render_documents`: the `<documents>` block (design/artifact/00-design.md §29).

A deterministic window, never a model's fold, over `DocumentsView`: one line per
document, title then id then revision then how long ago it was last revised. **An age on
every line**: whether to revise an existing document or start a new one turns on WHEN it
was last touched (AGENTS.md, the spine).

A title is a model's words and a member's words shaped them, so it is collapsed to one
line, cut to `TITLE_CHARS`, and escaped by `render_bullets` like every record: it can
neither forge a line nor close the tag.
"""

from __future__ import annotations

from ..domain.blocks import ContextBlock, render_bullets
from ..domain.computer_sessions import age
from ..domain.documents_context import TITLE_CHARS, DocumentLine, DocumentsView


def _title(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= TITLE_CHARS else one[: TITLE_CHARS - 1].rstrip() + "…"


def _line(line: DocumentLine, view: DocumentsView) -> str:
    title = _title(line.title) or "(untitled)"
    return (
        f'"{title}" · id {line.document_id} · revision {line.revision} · '
        f"{age(line.revised_at, view.now)}"
    )


def render_documents(view: DocumentsView | None) -> ContextBlock | None:
    """The block, or None when there is no view (no document yet, `[documents]` off,
    or the read did not come back)."""
    if view is None or not view.lines:
        return None
    body = render_bullets([_line(line, view) for line in view.lines], mark="")
    if not body:
        return None
    return ContextBlock(name="documents", body=body)
