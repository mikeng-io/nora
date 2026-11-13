"""Renders a unit's held topic accounts into the `<topics>` block: the whole
account, bounded — an account is short, and a revision needs all of it.

One `topic` element per held (non-retired) account: its id (what
`topic_revise` and `topic_set_status` take), the subject it is about, its
state, confidence, head revision and seq, when it was last written, and the
account as the content. An account renders only where its revision's lineage
permits — the union of its cited rows' own lineage — so the grounder has
already filtered it to the agenda's rooms; what reaches this renderer can be
emitted whole.

**Capped**: at most `lines` rows; the block says how many it left out
(`not_listed`) rather than cutting a row.
"""

from __future__ import annotations

from collections.abc import Sequence

from nora.domain.blocks import ContextBlock, render_elements
from nora.domain.composed import compose_body
from nora.domain.entries import Entry
from nora.domain.prompt import ago

#: The held accounts the session prompt starts with; a starting guess like
#: every cap.
TOPIC_LINES = 20


def render_topic_rows(topics: Sequence[Entry], *, now_ms: int) -> str:
    """The rows: `<topic id=… subject=… …>account</topic>`, in the order given."""
    return render_elements(
        topics, tag="topic",
        attrs=lambda e, _i: (
            ("id", e.id),
            ("subject", e.head.content.about[0][1]),
            ("state", e.head.content.state),
            ("confidence", e.head.content.confidence),
            ("written", ago(now_ms - e.head.written_ms)),
            ("revision", e.head.id),
            ("seq", str(e.head.seq)),
        ),
        body=lambda e: e.head.content.body,
    )


def render_topics(
    topics: Sequence[Entry], *, now_ms: int, lines: int = TOPIC_LINES
) -> ContextBlock:
    """The session's `<topics>` block at the cap, or `None` when none is held."""
    rows = render_topic_rows(topics[:lines], now_ms=now_ms)
    left_out = len(topics) - min(len(topics), lines)
    return ContextBlock(
        name="topics", body=compose_body(rows),
        attrs=(("not_listed", str(left_out)),) if left_out else (),
        when_empty="self_close",
    )


__all__ = ["TOPIC_LINES", "render_topic_rows", "render_topics"]
