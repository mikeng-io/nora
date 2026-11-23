from __future__ import annotations

from nora.agent.catalog.model import NativeTool

TOOLS = {
    "intention_commit": NativeTool(
        4,
        side_effect_state="confirmed",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=3,
    ),
}
