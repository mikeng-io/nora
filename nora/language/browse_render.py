"""The browse occasion's assembled prompt ([computer.scoped], B2).

`computer_browse` hands a whole objective to `BrowseAgentRunner`; the
text the browse agent is sent is assembled here through `ContextBlock`/
`assemble` like every other occasion's — one `browse_task` block, the
objective and the optional start page as its declared parts — never an
f-string built beside the model call.
"""

from __future__ import annotations

from nora.domain.blocks import BlockKey, ContextBlock, assemble, restamp
from nora.domain.composed import compose_body

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("browse_task", 10, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {name: (order, key) for name, order, key in _BLOCKS}


def render_browse_brief(objective: str, start_url: str | None) -> str:
    """The browse agent's prompt: the task as a typed block whose parts
    say what they are."""
    from nora.domain.blocks import render_elements

    parts: list[tuple[str, str]] = [("objective", objective.strip())]
    if start_url:
        parts.append(("start", start_url.strip()))
    return assemble([
        restamp(ContextBlock(
            name="browse_task",
            body=compose_body(render_elements(
                parts, tag="part", attrs=lambda part, _i: (("is", part[0]),),
                body=lambda part: part[1],
            )),
        ), _ROW),
    ])
