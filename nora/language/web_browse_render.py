"""Context assembly for deciding and reporting a web retrieval."""

from nora.domain.blocks import BlockKey, ContextBlock, assemble, render_elements, restamp
from nora.domain.composed import compose_body

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (("web_retrieval", 10, "turn"),)
_ROW = {name: (order, key) for name, order, key in _BLOCKS}


def render_retrieval(objective: str, evidence: str, actions: str) -> str:
    parts = (("objective", objective), ("page_evidence", evidence), ("actions", actions))
    return assemble([restamp(ContextBlock(
        name="web_retrieval",
        body=compose_body(render_elements(
            parts, tag="part", attrs=lambda part, _i: (("is", part[0]),),
            body=lambda part: part[1],
        )),
    ), _ROW)])
