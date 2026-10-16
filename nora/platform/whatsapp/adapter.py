"""WhatsAppAdapter — the third `PlatformAdapter` implementation.

Mirrors `nora.platform.signal.adapter.SignalAdapter`'s composition exactly:
a thin class wiring a listener, parser, sender and reactor against the
whatsapp-bridge's HTTP/SSE surface (`deploy/whatsapp-bridge`, the Baileys
daemon). This adapter holds no
normalization logic of its own — that lives in `parser.py`/`jid.py`, tested
directly against the contract.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from ...domain.models import ConversationTarget, NormalizedMessage
from ...domain.ports import (
    AdapterHealth,
    LinkPreview,
    Mention,
    OutboundAttachment,
    OutboundQuote,
    PlatformIdentity,
    ReactionResult,
    SendResult,
    TextStyle,
)
from .listener import WhatsAppListener
from .parser import parse_event
from .react import WhatsAppReactor
from .self_id import derive_identity
from .send import WhatsAppSender


class WhatsAppAdapter:
    platform = "whatsapp"

    def __init__(self, base_url: str, *, dm_recipients: frozenset[str] = frozenset()) -> None:
        """`dm_recipients` is every direct address in `identity.toml`; the
        sender refuses a 1:1 message to any other JID."""
        self._base_url = base_url
        self._listener = WhatsAppListener(base_url)
        self._sender = WhatsAppSender(base_url, dm_recipients=dm_recipients)
        self._reactor = WhatsAppReactor(base_url)
        self._identity: PlatformIdentity | None = None

    async def resolve_identity(self) -> PlatformIdentity:
        """Memoized after the first successful call — same rationale as
        `SignalAdapter.resolve_identity`: a stable answer to "who am I" that
        `/health` can poll without a daemon round trip every time. Memoized
        on SUCCESS only: the bridge answers 503 until its socket opens, and
        an identity cached from a half-paired bridge would be wrong for the
        life of the process, so a failure here raises and is retried."""
        if self._identity is None:
            primary, identifiers = await derive_identity(self._base_url)
            self._identity = PlatformIdentity(
                platform=self.platform, primary=primary, identifiers=identifiers,
            )
        return self._identity

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None:
        """Satisfies the port's `listen(*, on_message)` — parsing happens
        here, internally, via `nora.platform.whatsapp.parser.parse_event`, and
        a `None` result (a non-message event, `from_me`, a broadcast JID, an
        empty turn) is dropped before `on_message` is ever called. The
        parse-and-drop step lives in the adapter, not the ingest pipeline,
        same as `SignalAdapter.listen`."""

        async def _on_event(raw_event: dict) -> None:
            message = parse_event(raw_event)
            if message is not None:
                await on_message(message)

        await self._listener.listen(_on_event)

    async def send(
        self,
        *,
        target: ConversationTarget,
        text: str,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: OutboundQuote | None = None,
    ) -> SendResult:
        # `quote` is ACCEPTED and dropped: the bridge exposes no reply shape
        # here yet. Accepted rather than omitted because `PlatformAdapter` is a
        # structural Protocol — a signature missing the keyword is not a
        # platform that cannot thread, it is a platform that cannot be an
        # adapter, and the failure would be a startup type error rather than
        # an unthreaded message. `OutboundQuote` states the posture this
        # relies on: a reply that lost its thread is still a reply.
        return await self._sender.send(
            target, text, mentions=mentions, styles=styles, preview=preview,
            attachments=attachments,
        )

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_author: str,
        to_timestamp: int,
        # Accepted and IGNORED: the bridge addresses a message by its stanza
        # id, which is what `to_message_id` below carries — Signal's
        # (author, timestamp) scheme cannot reconstruct one, the situation
        # `nora.domain.ports.PlatformAdapter.react`'s additive parameter is for.
        to_message_id: str = "",
        remove: bool = False,
    ) -> ReactionResult:
        return await self._reactor.react(
            target=target, emoji=emoji, to_message_id=to_message_id, remove=remove,
        )

    async def health(self) -> AdapterHealth:
        last = self._listener.last_event_at
        return AdapterHealth(
            platform=self.platform,
            connected=self._listener.connected,
            last_event_age_seconds=None if last is None else time.monotonic() - last,
        )
