"""Send one real Giphy GIF through the in-container Signal daemon to a probe group.

Run only after deploy, from the Nora container. `GIPHY_SMOKE_GROUP_ID` must name
a disposable Signal group; this script intentionally cannot target a direct chat.
"""

from __future__ import annotations

import asyncio
import sys

from nora.domain.models import ConversationTarget
from nora.infra.giphy import GiphyClient
from nora.platform.signal.send import SignalSender
from nora.secret import credentials


async def main() -> int:
    api_key = credentials().get("GIPHY_API_KEY", "")
    group_id = credentials().get("GIPHY_SMOKE_GROUP_ID", "")
    if not api_key or not group_id:
        print("GIPHY_API_KEY and GIPHY_SMOKE_GROUP_ID are required", file=sys.stderr)
        return 2
    client = GiphyClient(api_key=api_key)
    candidates = await client.search(query="celebratory fist pump")
    if not candidates:
        print("Giphy returned no described candidates", file=sys.stderr)
        return 2
    attachment = await client.fetch(candidate=candidates[0])
    if attachment is None:
        print("Giphy candidate could not be downloaded", file=sys.stderr)
        return 2
    result = await SignalSender("http://signal-cli:18080").send(
        ConversationTarget("signal", group_id, "group"),
        "Giphy delivery smoke test",
        attachments=(attachment,),
        request_id="giphy-smoke",
    )
    if not result.ok:
        print("signal-cli rejected the GIF", file=sys.stderr)
        return 2
    print(f"sent candidate {candidates[0].id} ({len(attachment.content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
