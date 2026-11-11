from __future__ import annotations

from nora.agent.catalog.model import NativeTool

TOOLS = {
    "document_publish": NativeTool(
        6,
        read_only=False,
        side_effect_state="confirmed",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=3,
    ),
    "document_read": NativeTool(
        2,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=1,
    ),
    "document_set_status": NativeTool(
        2,
        read_only=False,
        side_effect_state="confirmed",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=2,
    ),
}
