"""SignalAdapter — the one PlatformAdapter implementation."""

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
from .listener import SignalListener
from .parser import parse_event
from .react import SignalReactor
from .self_id import MetricsSink, derive_identifiers
from .send import SignalSender


class SignalAdapter:
    platform = "signal"

    def __init__(
        self, base_url: str, account: str, *, metrics: MetricsSink,
        dm_recipients: frozenset[str] = frozenset(),
    ) -> None:
        """`metrics` is required, not optional with a null default: identity
        enrichment degrading uncounted is R-17's entire subject, and an optional
        collaborator is how it stayed uncounted before.

        `dm_recipients` is every direct address in `identity.toml`; the sender
        refuses a direct message to anything else."""
        self._base_url = base_url
        self._account = account
        self._metrics = metrics
        self._listener = SignalListener(base_url)
        self._sender = SignalSender(base_url, dm_recipients=dm_recipients)
        self._reactor = SignalReactor(base_url)
        self._identity: PlatformIdentity | None = None

    async def resolve_identity(self) -> PlatformIdentity:
        """Memoized after the first successful call: `resolve_identity` is meant to
        be a stable, idempotent answer to "who am I" (see the port's own module
        docstring), not a fresh daemon round-trip every time — `nora.obs.health`
        calls this on every `/health` poll to fill in `sse.account` (Trap A), and a
        network hit on every poll would defeat the point of asking a question whose
        answer does not change while the process is alive.

        Memoization is on SUCCESS-OR-DEGRADED alike, deliberately: a degraded
        derivation is still a stable answer, and retrying it per call would put
        a daemon round trip back on the `/health` path. The degradation is
        counted once, at startup, which is when an operator can act on it.
        """
        if self._identity is None:
            identifiers = await derive_identifiers(
                self._base_url, self._account, metrics=self._metrics,
                platform=self.platform,
            )
            self._identity = PlatformIdentity(
                platform=self.platform, primary=self._account,
                identifiers=identifiers,
            )
        return self._identity

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None:
        """Satisfies the port's `listen(*, on_message)` — no `parse()` on the public
        surface (see `nora.domain.ports`'s module docstring: forcing every adapter to
        pretend its transport is dict-shaped is wrong for Discord/Telegram). Parsing
        happens here, internally, via `nora.platform.signal.parser.parse_event`; a
        `None` result — a DM, a sync message, a self-send, an empty body (R-12's
        first pass) — is dropped before `on_message` is ever called. This
        parse-and-drop step lives here, not in `nora.ingest.pipeline`.
        """

        async def _on_event(raw_event: dict) -> None:
            message = parse_event(raw_event, self._account)
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
        return await self._sender.send(
            target, text, mentions=mentions, styles=styles, preview=preview,
            attachments=attachments, quote=quote,
        )

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_author: str,
        to_timestamp: int,
        # Accepted and IGNORED: Signal addresses a message by (author,
        # timestamp), which is what the two parameters above are. Discord needs
        # its own snowflake, so the port carries both — see
        # `nora.domain.ports.PlatformAdapter.react`.
        to_message_id: str = "",
        remove: bool = False,
    ) -> ReactionResult:
        return await self._reactor.react(
            target, emoji, to_author=to_author, to_timestamp=to_timestamp, remove=remove,
        )

    async def health(self) -> AdapterHealth:
        last = self._listener.last_event_at
        return AdapterHealth(
            platform=self.platform,
            connected=self._listener.connected,
            last_event_age_seconds=None if last is None else time.monotonic() - last,
        )
