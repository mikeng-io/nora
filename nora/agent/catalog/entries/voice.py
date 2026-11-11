from __future__ import annotations

from nora.agent.catalog.model import NativeTool

TOOLS = {
    "play_music": NativeTool(
        2,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=2,
        hot_on=frozenset({"voice"}),
    ),
    "skip_music": NativeTool(
        2,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=1,
    ),
    "top_music": NativeTool(
        2,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=2,
        hot_on=frozenset({"voice"}),
    ),
    "stop_music": NativeTool(
        2,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=1,
        hot_on=frozenset({"voice"}),
    ),
    "music_status": NativeTool(
        2,
        source_tier="confirmed",
        cacheable=True,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=1,
        hot_on=frozenset({"voice"}),
    ),
    "set_music_volume": NativeTool(
        2,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=1,
        hot_on=frozenset({"voice"}),
    ),
}
