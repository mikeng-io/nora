"""Stage: feedback — the single post-reply exchange feed (spec C.7).

Replaces `Handler._feed_exchange` (`nora/handler.py:137-142`) byte-for-byte:
same text shape, same guard. Called at most once per tag, and only after
`nora.engagement.stages.deliver.check_outcome` has confirmed a reply was
actually delivered — `ctx.tagger is None` (an unmapped sender) is checked
again here too, defensively, so a caller that forgets that precondition still
gets the right behaviour (no Honcho call for someone we can't name) rather
than an `AttributeError` on `ctx.tagger.honcho_peer_id`.

The result is COUNTED, on the same two series the ambient path uses
(`nora.ingest.stages.memory_feed`). It used to be discarded, which made this
the last Honcho write whose failure left no trace anywhere — and the reason
that matters is not hypothetical: `HonchoClient.ensure_session` once POSTed to
a list endpoint, so `feed` returned `False` without making an HTTP call and
Nora's semantic memory received nothing for the deployment's entire life. If
that class of drift recurs, the ambient counters go red while the tagging path
stays invisible, and an operator reading a dashboard would conclude the
memory of actual conversations was healthy when none of it was landing.
"""

from __future__ import annotations

from typing import Any

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
    metrics: Any,
) -> None:
    if ctx.tagger is None:
        return
    text = deliverer.last_text or ""
    ok = await memory.feed(
        peer_id=ctx.tagger.honcho_peer_id,
        text=f"[{tagger_name}]: {ctx.message.body}\n[{display_name}]: {text}",
    )
    metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")
