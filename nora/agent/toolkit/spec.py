from __future__ import annotations

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.catalog.model import NativeTool
from nora.agent.ports import ToolSpec


def _spec(name: str, call, *, declared: NativeTool | None = None) -> ToolSpec:
    """One `ToolSpec`, with every declared field taken from the tool catalogue."""
    declared = NATIVE_TOOLS[name] if declared is None else declared
    return ToolSpec(
        name=name,
        description="",
        schema={},
        call=call,
        budget=declared.budget,
        terminal=declared.terminal,
        time_shiftable=declared.time_shiftable,
        # The contract triple (ADR 0008), copied for the same reason as the fields
        # above: a hand-written constructor call is how a field gets the dataclass
        # default instead of the declaration, and for these three the default is
        side_effect_state=declared.side_effect_state,
        idempotency=declared.idempotency,
        retry=declared.retry,
        # And the output contract (ADR 0009, DEC-153) for the strongest version of
        # that reason: this one is READ at runtime. A dropped copy here does not
        # merely lose a declaration — it silently switches the validation `adapt()`
        output_contract=declared.output_contract,
    )
