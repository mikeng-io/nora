"""Shared tool catalogue block: generated tool descriptions, escaped once."""
from nora.domain.blocks import ContextBlock
from nora.domain.composed import compose_body
from nora.domain.prompt import escape


def render_tool_catalogue(catalogue: str) -> ContextBlock:
    return ContextBlock(name="tool_catalogue", body=compose_body(escape(catalogue)),
                        when_empty="self_close")
