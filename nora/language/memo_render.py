"""Renders a unit's kept memos into the `<memos>` block: the INDEX, not the memos
(DEC-306, design/cognition-loop/15-map.md: titles pushed, body pulled).

One `memo` element per kept memo: its id (what `memo_read`, `memo_revise` and
`memo_withdraw` take), the day it last changed, and its title as the content.
Never a `## Kept` line: what a memo keeps is read on demand, so a value kept
verbatim (a password, D5) is never pushed into every turn of every room. Unit-
wide like `<notes>`: a memo kept in one room renders in every room of its unit
(operator, 2026-10-09). No block at all when nothing is kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, tzinfo

from ..domain.blocks import ContextBlock, render_elements
from ..domain.memo import Memo


def _day(ms: int, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=tz).date().isoformat()


def render_memo_rows(memos: Sequence[Memo], *, tz: tzinfo) -> str:
    """The rows: `<memo id=… updated=…>title</memo>`, kept memos only, in the
    order given."""
    return render_elements(
        [m for m in memos if m.is_kept], tag="memo",
        attrs=lambda m, _i: (("id", m.id), ("updated", _day(m.head.written_at, tz))),
        body=lambda m: m.title,
    )


def render_memos(memos: Sequence[Memo], *, tz: tzinfo) -> ContextBlock | None:
    """The tag and fire turns' `<memos>` block, or `None` when nothing is kept."""
    rows = render_memo_rows(memos, tz=tz)
    return ContextBlock(name="memos", body=rows) if rows else None


__all__ = ["render_memo_rows", "render_memos"]
