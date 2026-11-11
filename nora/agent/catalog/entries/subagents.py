from __future__ import annotations

from nora.agent.catalog.model import NativeTool

TOOLS = {
    "subagent_create": NativeTool(
        2,
        read_only=False,
        side_effect_state="confirmed",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=3,
    ),
    "subagent_list": NativeTool(
        1,
        time_shiftable=False,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=1,
    ),
    "subagent_read": NativeTool(
        2,
        time_shiftable=False,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=1,
    ),
    "subagent_cancel": NativeTool(
        1,
        read_only=False,
        side_effect_state="confirmed",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=1,
    ),
}
