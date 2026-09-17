"""DiscordAdapter — the second `PlatformAdapter` implementation
(design/discord-integration/).

Mirrors `nora.platform.signal.adapter.SignalAdapter`'s composition: a thin
class wiring together a listener/parser/sender, adapted to Discord's
transport — a Gateway WebSocket (`discord.py`) instead of an SSE stream, and
REST calls instead of signal-cli's JSON-RPC.
"""

from __future__ import annotations

import contextlib
import logging
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
    OutboundQuote,
    PlatformIdentity,
    ReactionResult,
    SendResult,
    TextStyle,
    speakable,
)
from ...infra.voice.hub import VoiceControllerHub
from .listener import DiscordListener
from .music_commands import register as register_music_commands
from .music_commands import resume_persisted_sessions as resume_persisted_music_sessions
from .music_manager import MusicPlayerManager
from .parser import parse_discord_message
from .react import DiscordReactor
from .self_id import derive_identity
from .send import DEFAULT_API_BASE, DiscordSender

log = logging.getLogger("nora.platform.discord")


class DiscordAdapter:
    platform = "discord"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_API_BASE,
        music_manager: MusicPlayerManager | None = None,
        speaker: Any = None,
        voice_hub: VoiceControllerHub | None = None,
        voice_guild_ids: frozenset[str] = frozenset(),
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
        # The voice path (design/voice-channel/07): the MiniMax speaker and
        # the audio-controller hub. BOTH None on every deployment that hasn't
        # wired the voice path — the text-only posture stays byte-identical.
        # A SPOKEN turn routes through speak → the controller's `/stream`
        # endpoint instead of REST text (see `send`). `voice_guild_ids` is
        # not part of that decision: it is the join allow-list, naming the
        # guilds whose voice channels Nora may be pulled into at all
        # (design/voice-channel/07 §12.2, reversed 2026-09-17).
        self._speaker = speaker
        self._voice_hub = voice_hub
        self._voice_guild_ids = set(voice_guild_ids)
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
        # design/discord-music/07-agent-tool.md — the ONE thing an agent
        # tool needs that `NormalizedMessage`/`TagContext` never carry: a
        # live client to resolve a guild member's current voice channel.
        # Same per-attempt lifecycle as `_listener` above; `current_client()`
        # is the accessor, never read directly by anything outside this class.
        self._current_client: discord.Client | None = None

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
        intents.presences = True        # privileged; presence intent (design/online-status/02)
        client = discord.Client(intents=intents)
        self._current_client = client

        async def _register_music_commands() -> None:
            # `design/discord-music/02-control-surface.md`: the CommandTree
            # is built fresh alongside `client`, every attempt, same
            # lifecycle. Not independently testable (a real
            # `discord.Client`/`CommandTree`) -- `music_commands.py`'s own
            # docstring is explicit about that boundary.
            if self._music_manager is not None:
                await register_music_commands(
                    client, self._music_manager,
                    hub=self._voice_hub,
                    voice_guild_ids=frozenset(self._voice_guild_ids),
                )
                # design/discord-music/06-session-persistence.md stage 2 --
                # safe to run on every on_ready, not just a genuine boot
                # (resume_persisted_sessions's own guard against clobbering
                # an already-live player).
                await resume_persisted_music_sessions(
                    client, self._music_manager, hub=self._voice_hub,
                )
            # B-2 (2026-09-16 audit): a guild with no persisted MUSIC row
            # never calls `hub.join` above, so a wake-reply-only boot would
            # leave the controller's boot-joined connection un-adopted and
            # every speech reply silently dropped until a human ran
            # `/discord join`. Independent of `_music_manager` and safe to
            # run every `on_ready` — `hub.join`/`adopt_boot_voice_sessions`
            # are both idempotent against an already-cached session.
            if self._voice_hub is not None:
                await self._voice_hub.adopt_boot_voice_sessions(self._voice_guild_ids)

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
            self._current_client = None

    def current_client(self) -> discord.Client | None:
        """`None` whenever there is no live connection attempt right now
        (between attempts, or before the first) — an agent tool built on
        top of this must treat that as `unavailable`, the same as any
        other wiring gap this codebase's provider ports already do."""
        return self._current_client

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
        # Voice playout: answer a SPOKEN turn aloud, through the speaker →
        # the audio-controller's `/stream` seam. Fail-closed: without a
        # speaker or a hub the reply is SILENT (ok=False, text-only fallback
        # is the caller's business).
        #
        # Routed on the TURN, not on the room (design/voice-channel/07 §12.2,
        # reversed 2026-09-17). While speech had a room of its own, "this
        # conversation_id is a voice channel" and "this question was spoken"
        # were the same fact. Now a guild's speech lands in its text room, so
        # one room takes both — and the honest rule is the one that was always
        # meant: answer the way you were asked. A typed question in a room
        # Nora also listens to by voice still gets a text reply.
        if target.voice:
            return await self._send_voice(target=target, text=text)
        return await self._sender.send(
            target, text, mentions=mentions, styles=styles, preview=preview,
            attachments=attachments, quote=quote,
        )

    async def _send_voice(self, *, target: ConversationTarget, text: str) -> SendResult:
        if self._speaker is None or self._voice_hub is None:
            log.warning(
                "voice-channel send to %s but speaker/voice hub unset — "
                "voice replies stay silent (config [speech.tts])",
                target.conversation_id,
            )
            return SendResult(ok=False)
        if not speakable(text):
            # design/voice-channel/01 §7 — a long opaque string must not be
            # read aloud. A voice channel has no text surface; the honest
            # result is a refused send, matching what Discord would do with
            # the string anyway.
            log.info(
                "voice-channel send refused: not speakable (target=%s)",
                target.conversation_id,
            )
            return SendResult(ok=False)
        result = await self._speaker.speak(text=text)
        if not result.available or not result.audio:
            log.warning("voice-channel send failed: tts unavailable (reason=%s)", result.reason)
            return SendResult(ok=False)
        # The ONLY speech path (design/voice-channel/07 §3): the
        # controller's `/stream` endpoint, generation-scoped via the hub.
        # `False` covers every failure (no session, stale generation,
        # controller down) — the hub already logged which.
        guild_id, _, _ = target.conversation_id.partition("/")
        ok = await self._voice_hub.stream_speech(guild_id, result.audio, result.media_type)
        return SendResult(ok=ok)

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
