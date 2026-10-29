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

from nora.domain.external_files import render_file_links
from nora.domain.models import EditedMessage, NormalizedMessage, PersonRef
from nora.domain.ports import MemoryStore


def edit_text(*, body: str, first_body: str | None) -> str:
    """What Honcho is fed for an EDIT.

    Honcho stores a message's content once — its API updates metadata only —
    and its deriver has usually read the original before the edit arrives. So
    the edit is a NEW message, and this framing is what lets the deriver read
    it as a correction of something already said rather than as a second,
    independent statement. The first text is quoted because the deriver has no
    join from this message to that one: without it, «8pm» is a fact, not a
    change from «7pm»."""
    return f"[edited an earlier message; it first said: «{first_body or ''}»] {body}"


async def run(
    *,
    message: NormalizedMessage,
    person: PersonRef | None,
    memory: MemoryStore,
    metrics: Any,
    spawn: Callable[[Coroutine[Any, Any, None]], None],
    edited: EditedMessage | None = None,
    absorbed: bool = True,
) -> None:
    """Schedule the feed; never await it here. `person is None` means the
    sender is unmapped (D6) — the message is never fed to the semantic store.

    `edited` is the message `message` edits, when it is an edit: the text is
    then framed as a correction and the metadata points at the original's
    `ts`."""
    if person is None:
        return
    if not absorbed:
        # A mention-only room (a server-entry channel `ambient` does not
        # name): recorded and answered when tagged, never absorbed into
        # memory. `Conversation.absorbed` is the flag's one definition.
        return
    # Honcho's deriver is a model and recall hands its text back to her, so a
    # shared file link is fed as its handle, never the URL (R-18).
    text = render_file_links(message.body)
    metadata: dict[str, object] = {"ts": message.timestamp}
    if edited is not None:
        text = edit_text(body=text, first_body=render_file_links(edited.first_body or ""))
        metadata["edit_of_ts"] = edited.ts
    spawn(_feed(
        memory=memory, peer_id=person.honcho_peer_id, text=text,
        conversation_id=message.conversation_id, metadata=metadata, metrics=metrics,
    ))


async def _feed(
    *, memory: MemoryStore, peer_id: str, text: str, conversation_id: str,
    metadata: dict[str, object], metrics: Any,
) -> None:
    await memory.ensure_peer(peer_id=peer_id, conversation_id=conversation_id)
    # Fed under the SPEAKER'S peer into the CONVERSATION's session (Honcho v2).
    # `ts` rides as metadata so the deriver has structure.
    ok = await memory.feed(
        peer_id=peer_id, text=text, conversation_id=conversation_id,
        metadata=metadata,
    )
    metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")
