"""Stage 4 — feed the ambient message to Honcho. Mapped senders only (D6);
always off the listener path.

`on_event` is awaited by the SSE reader, so an inline 200-800ms Honcho call
would stall the socket read for every conversation on this adapter. This
stage schedules the feed through the injected `spawn` and returns immediately
— it never awaits the feed itself. `HonchoClient` is documented as never
load-bearing and already swallows its own failures (`nora/infra/honcho/
client.py`), so there is no try/except here to add; a `MemoryStore` fake that
raises would surface via `spawn`'s own done-callback, not this stage.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.ports import MemoryStore


async def run(
    *,
    message: NormalizedMessage,
    person: PersonRef | None,
    memory: MemoryStore,
    metrics: Any,
    spawn: Callable[[Coroutine[Any, Any, None]], None],
) -> None:
    """Schedule the feed; never await it here. `person is None` means the
    sender is unmapped (D6) — the message is never fed to the semantic store."""
    if person is None:
        return
    spawn(_feed(memory=memory, peer_id=person.honcho_peer_id, text=message.body, metrics=metrics))


async def _feed(*, memory: MemoryStore, peer_id: str, text: str, metrics: Any) -> None:
    await memory.ensure_peer(peer_id=peer_id)
    ok = await memory.feed(peer_id=peer_id, text=text)
    metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")
