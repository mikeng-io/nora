from __future__ import annotations

from nora.agent.catalog.model import NativeTool

TOOLS = {
    "online_now": NativeTool(
        4,
        time_shiftable=False,
        source_tier="confirmed",
        cacheable=False,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=1,
    ),
    "group_members": NativeTool(
        1,
        source_tier="confirmed",
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=2,
    ),
    "lookup_person": NativeTool(
        4,
        source_tier="remembered",
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=2,
    ),
    "about": NativeTool(
        4,
        source_tier="remembered",
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=2,
    ),
    "member_update": NativeTool(
        4,
        source_tier="confirmed",
        cacheable=False,
        read_only=False,
        # The effect is journaled and read back, so a returned result is
        # confirmed state; the operation id pins every retry to the same
        # message, which is what makes a repeat safe rather than a second
        # write.
        side_effect_state="confirmed",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=3,
    ),
}
