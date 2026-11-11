from __future__ import annotations

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.ports import ToolSpec


def _spec(name: str, call) -> ToolSpec:
    """One `ToolSpec`, with every declared field taken from `NATIVE_TOOLS[name]`."""
    declared = NATIVE_TOOLS[name]
    return ToolSpec(
        name=name,
        description="",
        schema={},
        call=call,
        budget=declared.budget,
        terminal=declared.terminal,
        time_shiftable=declared.time_shiftable,
        # The contract triple (ADR 0008), copied for the same reason as the
        # fields above: a hand-written constructor call is how a field gets the
        # dataclass default instead of the declaration, and for these three the
        # default is `none`/`non_idempotent`/`never` — which would quietly turn
        # every write verb on this table into a tool that wrote nothing.
        side_effect_state=declared.side_effect_state,
        idempotency=declared.idempotency,
        retry=declared.retry,
        # And the output contract (ADR 0009, DEC-153) for the strongest version
        # of that reason: this one is READ at runtime. A dropped copy here does
        # not merely lose a declaration — it silently switches the validation
        # `adapt()` performs off for that tool, and nothing downstream would
        # look any different.
        output_contract=declared.output_contract,
    )
