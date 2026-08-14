"""DiscordAdapter — the second `PlatformAdapter` implementation
(design/discord-integration/).

Mirrors `nora.platform.signal.adapter.SignalAdapter`'s composition: a thin
class wiring together a listener/parser/sender, adapted to Discord's
transport — a Gateway WebSocket (`discord.py`) instead of an SSE stream, and
REST calls instead of signal-cli's JSON-RPC.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from ...domain.models import ConversationTarget, NormalizedMessage
from ...domain.ports import (
    AdapterHealth,
    LinkPreview,
    Mention,
    OutboundAttachment,
    PlatformIdentity,
    ReactionResult,
    SendResult,
    TextStyle,
)
from .listener import DiscordListener
from .parser import parse_discord_message
from .react import DiscordReactor
from .self_id import derive_identity
from .send import DEFAULT_API_BASE, DiscordSender


class DiscordAdapter:
    platform = "discord"

    def __init__(self, token: str, *, base_url: str = DEFAULT_API_BASE) -> None:
        self._token = token
        self._base_url = base_url
        intents = discord.Intents.default()
        intents.message_content = True  # privileged; see design/discord-integration/01
        intents.guilds = True
        self._listener = DiscordListener(discord.Client(intents=intents), token)
        self._sender = DiscordSender(token, base_url=base_url)
        self._reactor = DiscordReactor(token, base_url=base_url)
        self._identity: PlatformIdentity | None = None
        self._own_id = ""

    async def resolve_identity(self) -> PlatformIdentity:
        """Memoized after the first call — same rationale as
        `SignalAdapter.resolve_identity`: a stable answer to "who am I" that
        `/health` can poll without a network round trip every time."""
        if self._identity is None:
            primary, identifiers = await derive_identity(self._token, base_url=self._base_url)
            self._own_id = primary
            self._identity = PlatformIdentity(
                platform=self.platform, primary=primary, identifiers=identifiers,
            )
        return self._identity

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None:
        """No `parse()` on the public surface — parsing happens internally via
        `parse_discord_message`, mirroring `SignalAdapter.listen`'s Trap B."""
        if self._identity is None:
            await self.resolve_identity()

        async def _on_discord_message(raw_message: Any) -> None:
            message = parse_discord_message(raw_message, own_id=self._own_id)
            if message is not None:
                await on_message(message)

        await self._listener.listen(_on_discord_message)

    async def send(
        self,
        *,
        target: ConversationTarget,
        text: str,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
    ) -> SendResult:
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
        to_message_id: str = "",
        remove: bool = False,
    ) -> ReactionResult:
        """Discord identifies a message by its own snowflake, which
        `to_author`/`to_timestamp` (Signal's scheme) cannot reconstruct —
        `snowflake.timestamp_ms` is not invertible, the low 22 bits are gone.

        This used to be an honest `supported=False` for exactly that reason.
        It no longer has to be: `NormalizedMessage.message_id` carries the real
        snowflake from the parser, and the port passes it through as
        `to_message_id`. Absent one, `DiscordReactor` still answers
        `supported=False` rather than guessing at a message.
        """
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
