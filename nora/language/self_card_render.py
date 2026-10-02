"""Renders a peer's own conclusions into the `<self_card>` block — episodic
continuity about what she said and did, not a description of who she is
(`nora/agent/prefill.py::_self_card_block`'s own argument for the framing).

**The ONE builder for a name two occasions built separately**
(`design/context-assembly/01-the-registry.md` S3). `nora.agent.prefill` and
`nora.language.proactive` (deleted 2026-10-03) each constructed `ContextBlock(name="self_card",
body=render_bullets(conclusions))` by hand from a `tuple[str, ...]` of
Honcho conclusions — `TagContext.self_card` in one, `Recollection.conclusions`
in the other — with nothing comparing the two. They were already the same
computation on the same input type; this module is that computation, once.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.blocks import ContextBlock, render_bullets


def render_self_card(conclusions: Sequence[str]) -> ContextBlock | None:
    """`None` when there is nothing to show — an unreachable peer or a peer
    Honcho holds no conclusions about is not a peer she has an empty card
    about, so the block does not render rather than rendering empty.
    """
    if not conclusions:
        return None
    return ContextBlock(name="self_card", body=render_bullets(conclusions))
