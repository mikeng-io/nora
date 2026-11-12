"""Renders a unit's held topic accounts into the `<topics>` block: the whole
account, bounded — an account is short, and a revision needs all of it.

One `topic` element per held (non-retired) account: its id (what
`topic_revise` and `topic_set_status` take), the subject it is about, its
state, confidence, head revision and seq, the account as the content.
Unit-wide like `<memos>`: an account written from one room renders in every
room of its unit, because the subject it tracks recurs across them.

**Capped**: at most `lines` rows; the block says how many it left out
(`not_listed`) rather than cutting a row.
"""

from __future__ import annotations

from collections.abc import Sequence

from nora.domain.blocks import ContextBlock, render_elements
from nora.domain.composed import compose_body
from nora.domain.entries import Entry

#: The held accounts the session prompt starts with; a starting guess like
#: every cap.
TOPIC_LINES = 20


def render_topic_rows(topics: Sequence[Entry]) -> str:
    """The rows: `<topic id=… subject=… …>account</topic>`, in the order given."""
    return render_elements(
        topics, tag="topic",
        attrs=lambda e, _i: (
            ("id", e.id),
            ("subject", e.head.content.about[0][1]),
            ("state", e.head.content.state),
            ("confidence", e.head.content.confidence),
            ("revision", e.head.id),
            ("seq", str(e.head.seq)),
        ),
        body=lambda e: e.head.content.body,
    )


def render_topics(topics: Sequence[Entry], *, lines: int = TOPIC_LINES) -> ContextBlock:
    """The session's `<topics>` block at the cap, or `None` when none is held."""
    rows = render_topic_rows(topics[:lines])
    left_out = len(topics) - min(len(topics), lines)
    return ContextBlock(
        name="topics", body=compose_body(rows),
        attrs=(("not_listed", str(left_out)),) if left_out else (),
        when_empty="self_close",
    )


__all__ = ["TOPIC_LINES", "render_topic_rows", "render_topics"]
