from __future__ import annotations

from nora.agent.catalog.model import NativeTool
from nora.agent.link_owners import LinkInput

TOOLS = {
    "media_fetch": NativeTool(
        8,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=2,
        link_inputs=(
            LinkInput(
                "url",
                unless="code",
                not_clause=", not a picture or video file",
                then=", then look at the media it returns",
                verb="read",
                accepts=("file_fetch",),
            ),
        ),
    ),
    "generate_image": NativeTool(
        2,
        read_only=False,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=3,
    ),
    "video_create": NativeTool(
        2,
        read_only=False,
        side_effect_state="confirmed",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=3,
    ),
    "media_search": NativeTool(
        2,
        read_only=True,
        side_effect_state="none",
        idempotency="idempotent",
        retry="safe",
        output_contract="three_state",
        level=1,
    ),
    "media_send": NativeTool(
        2,
        read_only=False,
        side_effect_state="unknown",
        idempotency="non_idempotent",
        retry="never",
        output_contract="three_state",
        level=1,
    ),
}
