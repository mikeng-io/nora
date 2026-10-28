"""DiscordAdapter — a `PlatformAdapter` implementation.

Mirrors `nora.platform.signal.adapter.SignalAdapter`'s composition: a thin
class wiring together a listener/parser/sender, adapted to Discord's
transport — a Gateway WebSocket (`discord.py`) instead of an SSE stream, and
REST calls instead of signal-cli's JSON-RPC.
"""

from __future__ import annotations

import asyncio
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
from .channels import ClientChannelDirectory
from .listener import DiscordListener
from .music_commands import register as register_music_commands
from .music_commands import resume_persisted_sessions as resume_persisted_music_sessions
from .music_manager import MusicPlayerManager
from .music_notify import ResolveMusicChannel, no_music_channel
from .parser import (
    parse_discord_delete,
    parse_discord_edit,
    parse_discord_message,
    parse_discord_reaction,
)
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
        resolve_music_channel: ResolveMusicChannel = no_music_channel,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._sender = DiscordSender(token, base_url=base_url)
        self._reactor = DiscordReactor(token, base_url=base_url)
        self._identity: PlatformIdentity | None = None
        self._own_id = ""
        # Her Discord handle, cached from the SAME `/users/@me` response
        # `resolve_identity()` fetches once — `own_handle()` reuses it rather
        # than reading it live per turn. `None`
        # until the first `resolve_identity()`, and stays `None` if Discord
        # reported no username.
        self._own_handle: str | None = None
        # The music control surface — `None` when `[music] enabled = false`
        # (nora/app/composition.py), which keeps this adapter unchanged for
        # every deployment that hasn't turned the feature on.
        self._music_manager = music_manager
        # WHERE this guild's music ANNOUNCEMENTS go — `policy.toml`'s
        # `music_channel`, threaded as a callable so the platform layer knows
        # nothing about `nora.policy`. The default answers `None` for every
        # guild: announce where the session started.
        self._resolve_music_channel = resolve_music_channel
        # The voice path: the MiniMax speaker and the audio-controller hub.
        # BOTH None on every deployment that hasn't wired the voice path — the
        # text-only posture stays byte-identical. A SPOKEN turn routes through
        # speak → the controller's `/stream` endpoint instead of REST text (see
        # `send`). `voice_guild_ids` is not part of that decision: it is the
        # join allow-list, naming the guilds whose voice channels Nora may be
        # pulled into at all.
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
        # permanently down. A per-attempt client is what makes the
        # supervisor's restart real.
        self._listener: DiscordListener | None = None
        # What the gateway cache knows about guild channels, shared with the
        # policy's server-entry expansion (composition hands the same kind to
        # `load_policy`). Reads `current_client()` per call, so between
        # attempts it answers unknown rather than stale.
        self._channels = ClientChannelDirectory(self.current_client)
        # The ONE thing an agent
        # tool needs that `NormalizedMessage`/`TagContext` never carry: a
        # live client to resolve a guild member's current voice channel.
        # Same per-attempt lifecycle as `_listener` above; `current_client()`
        # is the accessor, never read directly by anything outside this class.
        self._current_client: discord.Client | None = None

    async def resolve_identity(self) -> PlatformIdentity:
        """Memoized after the first call — same rationale as
        `SignalAdapter.resolve_identity`: a stable answer to "who am I" that
        `/health` can poll without a network round trip every time. The same
        `/users/@me` response also fills `_own_handle` for `own_handle()`."""
        if self._identity is None:
            primary, identifiers, handle = await derive_identity(
                self._token, base_url=self._base_url,
            )
            self._own_id = primary
            self._own_handle = handle
            self._identity = PlatformIdentity(
                platform=self.platform, primary=primary, identifiers=identifiers,
            )
        return self._identity

    async def own_handle(self) -> str | None:
        """Her handle on Discord — `@username#discriminator`, or `@username`
        alone once the account is on unique usernames — read from the SAME
        `/users/@me` response `resolve_identity()` fetches once at startup,
        never a second call per turn. Ensures
        that call has happened (a no-op once memoized) rather than answering
        `None` merely because nothing has asked yet. Display text for
        `<presence>` only — it never enters `PlatformIdentity`, so it can
        never become a mention id."""
        if self._identity is None:
            await self.resolve_identity()
        return self._own_handle

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None:
        """No `parse()` on the public surface — parsing happens internally via
        `parse_discord_message`, mirroring `SignalAdapter.listen`'s Trap B."""
        if self._identity is None:
            await self.resolve_identity()

        # One client, one listener, PER ATTEMPT — see `__init__`.
        intents = discord.Intents.default()
        intents.message_content = True  # privileged
        intents.guilds = True
        intents.members = True          # privileged; the roster read needs it
        intents.presences = True        # privileged; presence intent
        client = discord.Client(intents=intents)
        self._current_client = client

        async def _register_music_commands() -> None:
            # The CommandTree is built fresh alongside `client`, every
            # attempt, same lifecycle. Not independently testable (a real
            # `discord.Client`/`CommandTree`) -- `music_commands.py`'s own
            # docstring is explicit about that boundary.
            if self._music_manager is not None:
                await register_music_commands(
                    client, self._music_manager,
                    hub=self._voice_hub,
                    voice_guild_ids=frozenset(self._voice_guild_ids),
                    resolve_music_channel=self._resolve_music_channel,
                    client_getter=self.current_client,
                )
                # Safe to run on every on_ready, not just a genuine boot
                # (resume_persisted_sessions's own guard against clobbering
                # an already-live player).
                await resume_persisted_music_sessions(
                    client, self._music_manager, hub=self._voice_hub,
                    resolve_music_channel=self._resolve_music_channel,
                    client_getter=self.current_client,
                )
                # Idempotent, so every on_ready is safe: the watch asks the
                # controller whether sound is actually moving, because
                # otherwise nora would only learn that music stopped from
                # an event a wedged controller does not send.
                self._music_manager.start_liveness_watch()
            # A guild with no persisted MUSIC row
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

        # ONE EVENT AT A TIME PER CHANNEL. discord.py dispatches every gateway
        # event as its own task, so a message and its edit could be ingested
        # CONCURRENTLY — and a new message's insert waits on attachment custody
        # and transcription, so an edit made in that window found nothing and
        # was dropped as an orphan. Signal, WhatsApp and
        # Instagram already ingest one event at a time off a single stream;
        # this gives Discord the same order within a channel, which is the only
        # order an edit depends on, without serialising unrelated channels.
        channel_locks: dict[str, asyncio.Lock] = {}

        def _lock_for(raw_message: Any) -> asyncio.Lock:
            key = str(getattr(getattr(raw_message, "channel", None), "id", ""))
            return channel_locks.setdefault(key, asyncio.Lock())

        async def _on_discord_message(raw_message: Any) -> None:
            message = parse_discord_message(raw_message, own_id=self._own_id)
            if message is not None:
                async with _lock_for(raw_message):
                    await on_message(message)

        # An edit reaches the SAME `on_message` as a new message: it is a
        # `NormalizedMessage` with `edit_of` set, and ingest routes it
        # as it does for every other network.
        async def _on_discord_edit(raw_message: Any) -> None:
            message = parse_discord_edit(raw_message, own_id=self._own_id)
            if message is not None:
                async with _lock_for(raw_message):
                    await on_message(message)

        # A reaction or a delete reaches the SAME `on_message` too, as a
        # `NormalizedMessage` with `touch` set,
        # under the channel's lock so it cannot race the message it touches.
        async def _on_discord_touch(kind: str, payload: Any) -> None:
            # A raw payload names only the channel it fired in — a thread's
            # own id. Resolve its parent through the same cache the room gate
            # expands on, so the touch lands in the room the message was
            # recorded under, tagged with the thread like a message would be.
            channel_id = getattr(payload, "channel_id", None)
            parent = (
                self._channels.parent_channel(channel_id=str(channel_id))
                if channel_id is not None else None
            )
            message = (
                parse_discord_delete(payload, room_channel_id=parent)
                if kind == "delete"
                else parse_discord_reaction(
                    payload, own_id=self._own_id, removed=kind == "reaction_remove",
                    room_channel_id=parent,
                )
            )
            if message is not None:
                key = str(getattr(payload, "channel_id", ""))
                async with channel_locks.setdefault(key, asyncio.Lock()):
                    await on_message(message)

        try:
            await self._listener.listen(
                _on_discord_message, _on_discord_edit, _on_discord_touch,
            )
        finally:
            # `Client.start()` is `login()` then `connect()`. `connect()` closes
            # the client itself before re-raising, but `login()` raising — a
            # rotated token (401), a DNS failure, a Discord 5xx — leaves its
            # `aiohttp.ClientSession` open. `supervise_adapter` retries forever
            # and this method builds a fresh client each time, so a sustained
            # auth or network outage would leak one session and connector per
            # attempt, each announcing itself later as "Unclosed client
            # session" from the GC.
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
        # Routed on the TURN, not on the room. A guild's speech lands in its
        # text room, so one room takes both spoken and typed questions, and
        # the rule is: answer the way you were asked. A typed question in a
        # room Nora also listens to by voice still gets a text reply.
        if target.voice:
            spoken = await self._send_voice(target=target, text=text)
            if spoken.ok:
                return spoken
            # FALL BACK TO TEXT. Speech is a surface of an ordinary room, so
            # `target.conversation_id` is a postable text channel — and an
            # answer the room can read beats an answer nobody gets.
            #
            # Covers every reason speech failed, not just an unspeakable one:
            # no session, a TTS outage, an unwired guild. Each already logged
            # its own reason.
            log.info(
                "voice reply for %s could not be spoken — sending it as text",
                target.conversation_id,
            )
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
            # A long opaque string must not be read aloud: the caller either
            # rewords the reply or sends it text-only (`speakable`), and
            # `send` above posts it as text.
            log.info(
                "voice-channel send refused: not speakable (target=%s)",
                target.conversation_id,
            )
            return SendResult(ok=False)
        # REFUSE BEFORE PAYING. `policy.toml`'s `voice = true` and
        # `[speech.tts] sidecars` are independent on purpose, so a guild whose
        # speech is RECORDED may legitimately have no controller to play it
        # through. Without this check that configuration would bill MiniMax for a
        # synthesis on every spoken turn and throw the audio away, because the
        # guard above only asks whether a speaker and hub exist AT ALL — and
        # they do, for some other guild.
        guild_id, _, _ = target.conversation_id.partition("/")
        if self._voice_hub.session_for(guild_id) is None:
            log.info(
                "voice reply for %s not synthesized: no live voice session for "
                "guild %s", target.conversation_id, guild_id,
            )
            return SendResult(ok=False)
        result = await self._speaker.speak(text=text)
        if not result.available or not result.audio:
            log.warning("voice-channel send failed: tts unavailable (reason=%s)", result.reason)
            return SendResult(ok=False)
        # The ONLY speech path: the
        # controller's `/stream` endpoint, generation-scoped via the hub.
        # `False` covers every failure (stale generation, controller down) —
        # the hub already logged which. The no-session case was refused above,
        # before the synthesis was paid for.
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

        `NormalizedMessage.message_id` carries the real snowflake from the
        parser, and the port passes it through as `to_message_id`. Absent one,
        `DiscordReactor` answers `supported=False` rather than guessing at a
        message.
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
