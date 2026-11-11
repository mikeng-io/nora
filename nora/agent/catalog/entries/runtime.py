from __future__ import annotations

from nora.agent.catalog.model import NativeTool

TOOLS = {
    "now": NativeTool(
        4,
        time_shiftable=False,
        source_tier="confirmed",
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        level=1,
    ),
    "calculate": NativeTool(
        8,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=2,
    ),
    "load_tools": NativeTool(
        3,
        terminal=False,
        time_shiftable=None,
        read_only=True,
        source_tier=None,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=2,
    ),
    "skills": NativeTool(
        2,
        terminal=False,
        time_shiftable=None,
        plugin=True,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
    ),
}
