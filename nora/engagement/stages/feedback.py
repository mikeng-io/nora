"""Stage: feedback — the single post-reply exchange feed (spec C.7).

Replaces `Handler._feed_exchange` (`nora/handler.py:137-142`) byte-for-byte:
same text shape, same guard. Called at most once per tag, and only after
`nora.engagement.stages.deliver.check_outcome` has confirmed a reply was
actually delivered — `ctx.tagger is None` (an unmapped sender) is checked
again here too, defensively, so a caller that forgets that precondition still
gets the right behaviour (no Honcho call for someone we can't name) rather
than an `AttributeError` on `ctx.tagger.honcho_peer_id`.
"""

from __future__ import annotations

from nora.agent.guards import Deliverer
from nora.domain.models import TagContext
from nora.domain.ports import MemoryStore


async def run(
    *,
    memory: MemoryStore,
    ctx: TagContext,
    tagger_name: str,
    display_name: str,
    deliverer: Deliverer,
) -> None:
    if ctx.tagger is None:
        return
    text = deliverer.last_text or ""
    await memory.feed(
        peer_id=ctx.tagger.honcho_peer_id,
        text=f"[{tagger_name}]: {ctx.message.body}\n[{display_name}]: {text}",
    )
