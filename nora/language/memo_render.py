"""Renders a unit's kept memos into the `<memos>` block: the INDEX, not the memos
(DEC-306: titles pushed, body pulled).

One `memo` element per kept memo: its id (what `memo_read`, `memo_revise` and
`memo_set_status` take), the day it last changed, and its title as the content.
Never a `## Kept` line: what a memo keeps is read on demand, so a value kept
verbatim (a password) is never pushed into every turn of every room. Unit-
wide like `<notes>`: a memo kept in one room renders in every room of its unit.
No block at all when nothing is kept.

**Capped, newest first**: at most `lines` rows and `chars`
characters as rendered, the most recently changed first, and the block says
how many it left out (`not_listed`) rather than cutting a row. One function for
the tag, fire and Cognition prompts, so the three cannot cap differently.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, tzinfo

from ..domain.blocks import ContextBlock, render_elements
from ..domain.memo import Memo

#: The tag and fire prompts' cap: the same numbers the Cognition prompt's
#: `RenderBudget` starts from (`memo_lines`, `memo_chars`). A starting guess, like
#: every cap here.
MEMO_LINES = 20
MEMO_CHARS = 1200


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


def memo_rows(
    memos: Sequence[Memo], *, tz: tzinfo, lines: int = MEMO_LINES, chars: int = MEMO_CHARS,
) -> tuple[list[str], int]:
    """`(rows, left out)`: the kept memos newest first, within `lines` rows
    and `chars` characters as rendered, and how many did not fit."""
    kept = sorted(
        (m for m in memos if m.is_kept), key=lambda m: m.head.written_at, reverse=True,
    )
    rows: list[str] = []
    spent = 0
    for memo in kept[:lines]:
        row = render_memo_rows([memo], tz=tz)
        if spent + len(row) + 1 > chars:
            break
        rows.append(row)
        spent += len(row) + 1
    return rows, len(kept) - len(rows)


def render_memos(memos: Sequence[Memo], *, tz: tzinfo) -> ContextBlock | None:
    """The tag and fire turns' `<memos>` block at the default cap, or `None`
    when nothing is kept."""
    rows, left_out = memo_rows(memos, tz=tz)
    if not rows and not left_out:
        return None
    return ContextBlock(
        name="memos", body="\n".join(rows),
        attrs=(("not_listed", str(left_out)),) if left_out else (),
        when_empty="self_close",
    )


__all__ = ["MEMO_CHARS", "MEMO_LINES", "memo_rows", "render_memo_rows", "render_memos"]
