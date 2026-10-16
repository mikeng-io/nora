"""Stage: feedback — the tag path's semantic-store write.

**One peer's stream holds one speaker's words. Nothing else.** (DEC-36.)
Feeding a rendered two-speaker transcript under the tagger's peer would make
every reply Nora sent evidence ABOUT the person she was answering — a failure
a backfill cannot undo (the store keeps CONCLUSIONS, not text).

**What this stage feeds: NORA's own replies, under the Nora aiPeer, into the
CONVERSATION's session.** A session holds every speaker as its own peer, so
Nora's reply lands under the `nora` peer — building her global self-model
card — while a human's words stay under theirs. Neither pollutes the other;
the invariant holds at the message level.

**The human side is NOT fed here.** `nora.ingest.stages.memory_feed` already
feeds every human message (including the tagging one) under the sender's peer
into the conversation's session, on the ambient path before the engage gate.
So this stage feeds ONLY Nora's replies.

**Every sent message, not the last one.** `deliver.sent_texts` is a LIST — a
multi-message reply feeds each message. A refused/failed message was never
appended, so it is never fed.

Counted on the same two series the ambient path uses, so a silent-feed
regression shows up on the dashboard rather than hiding.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from nora.domain.ports import MemoryStore


async def run(
    *,
    memory: MemoryStore,
    ai_peer: str,
    sent_texts: Iterable[str],
    conversation_id: str,
    metrics: Any,
) -> None:
    """Feed each of Nora's sent messages under the Nora aiPeer into the
    conversation's session. Best-effort; each feed counts on the shared series."""
    await memory.ensure_peer(peer_id=ai_peer, conversation_id=conversation_id)
    for text in sent_texts:
        ok = await memory.feed(
            peer_id=ai_peer, text=text, conversation_id=conversation_id,
        )
        metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")
