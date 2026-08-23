"""`DiscordMusicToolBridge` — the `MusicToolBridge` port's real
implementation (design/discord-music/07-agent-tool.md). The ONE thing a
Nora agent music tool needs that `NormalizedMessage`/`TagContext` never
carry: a live Discord client, to resolve a guild member's CURRENT voice
channel and actually join it. Reuses `MusicPlayerManager` and
`_connect_factory` verbatim — the exact same state machine and voice-join
logic the slash commands already use, never a second mutation path
(`02-control-surface.md`'s own "if added later" plan for this).

NEVER raises out of a public method — matches `TrackSource`/
`WeatherProvider`'s discipline (`nora/domain/ports.py`); every failure mode
becomes `MusicToolOutcome(status="unavailable" | "nothing_found", ...)`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord

from nora.domain.ports import MusicToolOutcome
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.music_commands import _connect_factory
from nora.platform.discord.music_manager import MusicPlayerManager

log = logging.getLogger("nora.music")

_UNAVAILABLE_DISCONNECTED = MusicToolOutcome(
    status="unavailable", note="Discord isn't connected right now.",
)
_UNAVAILABLE_NOT_IN_VOICE = MusicToolOutcome(
    status="unavailable", note="You need to be in a voice channel first.",
)
_NOTHING_PLAYING = MusicToolOutcome(status="nothing_found", note="Nothing is playing.")


class DiscordMusicToolBridge:
    """Satisfies `nora.domain.ports.MusicToolBridge`."""

    def __init__(
        self, *, adapter: DiscordAdapter, manager: MusicPlayerManager,
        crossfade_seconds: float = 0.0,
    ) -> None:
        self._adapter = adapter
        self._manager = manager
        self._crossfade_seconds = crossfade_seconds

    def _guild_id(self, conversation_id: str) -> str:
        guild_id, _, _ = conversation_id.partition("/")
        return guild_id

    def _member_voice_channel(
        self, conversation_id: str, sender_id: str,
    ) -> discord.VoiceChannel | None:
        """`None` covers every reason a fresh session can't start: no live
        Discord connection right now, the guild/member isn't resolvable
        from the cache, or the member simply isn't in a voice channel —
        `MusicPlayerManager.play`'s own rejection (a `None` `connect`)
        already treats all of these identically, the same way the slash
        command's `_requester_voice_channel` does."""
        client = self._adapter.current_client()
        if client is None:
            return None
        try:
            guild = client.get_guild(int(self._guild_id(conversation_id)))
            member = guild.get_member(int(sender_id)) if guild is not None else None
        except ValueError:
            return None  # conversation_id wasn't Discord-shaped at all
        if member is None or member.voice is None:
            return None
        channel = member.voice.channel
        return channel if isinstance(channel, discord.VoiceChannel) else None

    def _notify(self, conversation_id: str):
        _guild_id, _, channel_id = conversation_id.partition("/")

        async def notify(text: str) -> None:
            client = self._adapter.current_client()
            if client is None or not channel_id:
                return
            try:
                channel = client.get_channel(int(channel_id))
            except ValueError:
                return
            if isinstance(channel, discord.abc.Messageable):
                await channel.send(text, silent=True)

        return notify

    async def play(
        self, *, conversation_id: str, sender_id: str, query_or_url: str,
    ) -> MusicToolOutcome:
        client = self._adapter.current_client()
        if client is None:
            return _UNAVAILABLE_DISCONNECTED
        guild_id = self._guild_id(conversation_id)
        channel = self._member_voice_channel(conversation_id, sender_id)
        connect = (
            _connect_factory(
                self._manager, guild_id, channel, crossfade_seconds=self._crossfade_seconds,
            )
            if channel is not None else None
        )
        _, _, notify_channel_id = conversation_id.partition("/")
        try:
            outcome = await self._manager.play(
                guild_id=guild_id, query_or_url=query_or_url, requested_by=sender_id,
                added_at=datetime.now(UTC), connect=connect, notify=self._notify(conversation_id),
                voice_channel_id=str(channel.id) if channel is not None else None,
                notify_channel_id=notify_channel_id or None,
            )
        except Exception:
            log.exception("agent tool play_music failed for guild %s", guild_id)
            return MusicToolOutcome(status="unavailable", note="Couldn't join the voice channel.")
        if outcome is None:
            return _UNAVAILABLE_NOT_IN_VOICE
        if outcome.status == "nothing_found" or outcome.track is None:
            return MusicToolOutcome(status="nothing_found", note="Couldn't find that.")
        return MusicToolOutcome(status="ok", title=outcome.track.title, url=outcome.track.url)

    async def skip(self, *, conversation_id: str) -> MusicToolOutcome:
        skipped = await self._manager.skip(self._guild_id(conversation_id))
        if skipped is None:
            return _NOTHING_PLAYING
        return MusicToolOutcome(status="ok", title=skipped.title, url=skipped.url)

    async def stop(self, *, conversation_id: str) -> MusicToolOutcome:
        guild_id = self._guild_id(conversation_id)
        status = await self._manager.status(guild_id)
        if status is None:
            return _NOTHING_PLAYING
        await self._manager.stop(guild_id)
        return MusicToolOutcome(status="ok")

    async def status(self, *, conversation_id: str) -> MusicToolOutcome:
        status = await self._manager.status(self._guild_id(conversation_id))
        if status is None or status.now_playing is None:
            return _NOTHING_PLAYING
        return MusicToolOutcome(
            status="ok", title=status.now_playing.title, url=status.now_playing.url,
        )
