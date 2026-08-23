"""DiscordAdapter — the second `PlatformAdapter` implementation
(design/discord-integration/).

Mirrors `nora.platform.signal.adapter.SignalAdapter`'s composition: a thin
class wiring together a listener/parser/sender, adapted to Discord's
transport — a Gateway WebSocket (`discord.py`) instead of an SSE stream, and
REST calls instead of signal-cli's JSON-RPC.
"""

from __future__ import annotations

import contextlib
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
from .music_commands import register as register_music_commands
from .music_manager import MusicPlayerManager
from .parser import parse_discord_message
from .react import DiscordReactor
from .self_id import derive_identity
from .send import DEFAULT_API_BASE, DiscordSender


class DiscordAdapter:
    platform = "discord"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_API_BASE,
        music_manager: MusicPlayerManager | None = None,
        crossfade_seconds: float = 0.0,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._sender = DiscordSender(token, base_url=base_url)
        self._reactor = DiscordReactor(token, base_url=base_url)
        self._identity: PlatformIdentity | None = None
        self._own_id = ""
        # design/discord-music/02-control-surface.md — `None` when
        # `[music] enabled = false` (nora/app/composition.py), which is what
        # keeps this adapter unchanged for every deployment that hasn't
        # turned the feature on.
        self._music_manager = music_manager
        self._crossfade_seconds = crossfade_seconds
        # Built fresh in `listen()`, NOT here. A `discord.Client` is single-use:
        # once it closes, `is_closed()` stays True for that instance forever
        # (`_closing_task` is never cleared except by `Client.clear()`), so
        # `start()` on a closed client returns IMMEDIATELY without connecting.
        # `nora.app.runtime.supervise_adapter` only backs off in its `except`
        # branch, so a `listen()` that RETURNS rather than raising becomes a
        # hot loop — re-running `login()` against Discord with no delay, no
        # `nora_adapter_restarts_total`, and no log line, with the Gateway
        # permanently down. Holding one client for the process lifetime turned
        # the supervisor's restart into a silent no-op; a per-attempt client is
        # what makes the restart real.
        self._listener: DiscordListener | None = None

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

        # One client, one listener, PER ATTEMPT — see `__init__`.
        intents = discord.Intents.default()
        intents.message_content = True  # privileged; see design/discord-integration/01
        intents.guilds = True
        intents.members = True          # privileged; the roster read needs it
        client = discord.Client(intents=intents)

        async def _register_music_commands() -> None:
            # `design/discord-music/02-control-surface.md`: the CommandTree
            # is built fresh alongside `client`, every attempt, same
            # lifecycle. Not independently testable (a real
            # `discord.Client`/`CommandTree`) -- `music_commands.py`'s own
            # docstring is explicit about that boundary.
            if self._music_manager is not None:
                await register_music_commands(
                    client, self._music_manager, crossfade_seconds=self._crossfade_seconds,
                )

        self._listener = DiscordListener(
            client, self._token, on_ready_extra=_register_music_commands,
        )

        async def _on_discord_message(raw_message: Any) -> None:
            message = parse_discord_message(raw_message, own_id=self._own_id)
            if message is not None:
                await on_message(message)

        try:
            await self._listener.listen(_on_discord_message)
        finally:
            # `Client.start()` is `login()` then `connect()`. `connect()` closes
            # the client itself before re-raising, but `login()` raising — a
            # rotated token (401), a DNS failure, a Discord 5xx — leaves its
            # `aiohttp.ClientSession` open. `supervise_adapter` retries forever
            # and this method builds a fresh client each time, so a sustained
            # auth or network outage leaked one session and connector per
            # attempt (~1400/day at the 60s backoff cap), each announcing
            # itself later as "Unclosed client session" from the GC.
            #
            # `close()` on an already-closed client is a no-op, so this is safe
            # on the ordinary path too.
            with contextlib.suppress(Exception):
                await client.close()

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
        # `None` until the first `listen()` builds one. Reported as NOT
        # connected rather than as an error: before the listener exists there
        # genuinely is no Gateway, and that is the honest answer.
        listener = self._listener
        last = listener.last_event_at if listener is not None else None
        return AdapterHealth(
            platform=self.platform,
            connected=listener.connected if listener is not None else False,
            last_event_age_seconds=None if last is None else time.monotonic() - last,
        )
