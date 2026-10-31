"""`DiscordMusicToolBridge` — the `MusicToolBridge` port's real
implementation. The ONE thing a
Nora agent music tool needs that `NormalizedMessage`/`TagContext` never
carry: a live Discord client, to resolve a guild member's CURRENT voice
channel and actually join it. Reuses `MusicPlayerManager` and
`_connect_factory` verbatim — the exact same state machine and voice-join
logic the slash commands already use, never a second mutation path.

NEVER raises out of a public method — matches `TrackSource`/
`WeatherProvider`'s discipline (`nora/domain/ports.py`); every failure mode
becomes `MusicToolOutcome(status="unavailable" | "nothing_found", ...)`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord

from nora.domain.ports import MusicToolOutcome
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord import music_messages as m
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.channels import is_voice_channel
from nora.platform.discord.music_commands import _connect_factory
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_notify import (
    ResolveMusicChannel,
    make_notify,
    no_music_channel,
)
from nora.platform.discord.voice_gate import voice_join_decision

log = logging.getLogger("nora.music")

# Model-facing outcome notes pull their wording from the canonical message
# table, so the `note` the LLM reads and the line a channel post renders can
# never drift apart.
_UNAVAILABLE_DISCONNECTED = MusicToolOutcome(
    status="unavailable", note=m.disconnected_note(),
)
_UNAVAILABLE_NOT_IN_VOICE = MusicToolOutcome(
    status="unavailable", note=m.not_in_voice_note(),
)
_NOTHING_PLAYING = MusicToolOutcome(
    status="nothing_found", note=m.nothing_playing_line(),
)
_NOTHING_QUEUED_AT_POSITION = MusicToolOutcome(
    status="nothing_found", note=m.nothing_queued_at_position_line(),
)


class DiscordMusicToolBridge:
    """Satisfies `nora.domain.ports.MusicToolBridge`."""

    def __init__(
        self,
        *,
        adapter: DiscordAdapter,
        manager: MusicPlayerManager,
        hub: VoiceControllerHub | None = None,
        voice_guild_ids: frozenset[str] = frozenset(),
        resolve_music_channel: ResolveMusicChannel = no_music_channel,
    ) -> None:
        self._adapter = adapter
        self._manager = manager
        self._hub = hub
        self._voice_guild_ids = voice_guild_ids
        self._resolve_music_channel = resolve_music_channel

    def _guild_id(self, conversation_id: str) -> str:
        guild_id, _, _ = conversation_id.partition("/")
        return guild_id

    def _member_voice_channel(
        self, conversation_id: str, sender_ids: frozenset[str],
    ) -> discord.VoiceChannel | None:
        """`None` covers every reason a fresh session can't start: no live
        Discord connection right now, the guild isn't resolvable from the
        cache, or none of `sender_ids` is both a real member AND in a voice
        channel — `MusicPlayerManager.play`'s own rejection (a `None`
        `connect`) already treats all of these identically, the same way
        the slash command's `_requester_voice_channel` does.

        Tries every candidate id, not just one: identity.toml can map one
        person to more than one Discord account (`MusicToolBridge`'s own
        docstring), and only the platform itself — who is ACTUALLY sitting
        in a voice channel right now — can say which one is real for this
        request: picking an arbitrary account could look up one while they sit
        in a channel on the other, and tell them to join a channel they are
        already in."""
        client = self._adapter.current_client()
        if client is None:
            return None
        try:
            guild = client.get_guild(int(self._guild_id(conversation_id)))
        except ValueError:
            return None  # conversation_id wasn't Discord-shaped at all
        if guild is None:
            return None
        for sender_id in sender_ids:
            try:
                member = guild.get_member(int(sender_id))
            except ValueError:
                continue
            if member is None or member.voice is None:
                continue
            channel = member.voice.channel
            if is_voice_channel(channel):
                return channel
        return None

    def _notify(self, conversation_id: str):
        """The announcement destination for a session started by `play_music`:
        the guild's `music_channel` if one of its rooms names one, else the
        room she was tagged in — resolved per-post by `music_notify.make_notify`, the ONE
        place any surface decides this. `current_client` is passed as the getter, not called:
        this closure outlives a gateway reconnect."""
        guild_id, _, channel_id = conversation_id.partition("/")
        return make_notify(
            client=self._adapter.current_client,
            guild_id=guild_id,
            fallback_channel_id=channel_id or None,
            resolve_music_channel=self._resolve_music_channel,
        )

    async def play(
        self, *, conversation_id: str, sender_ids: frozenset[str], query_or_url: str,
    ) -> MusicToolOutcome:
        client = self._adapter.current_client()
        if client is None:
            return _UNAVAILABLE_DISCONNECTED
        guild_id = self._guild_id(conversation_id)
        channel = self._member_voice_channel(conversation_id, sender_ids)
        connect = None
        voice_channel_id = None
        if channel is not None:
            # The same auto-join gate `/music play` applies: a FRESH session may only join the
            # requester's channel when it is on the allow-list. An existing
            # session is reused without a join, so the refusal binds only
            # when one would be needed.
            decision = voice_join_decision(
                is_member=True,  # the channel came from a guild member lookup
                guild_id=guild_id, caller_channel_id=str(channel.id),
                allowed_guilds=self._voice_guild_ids,
            )
            if decision == "ok":
                connect = _connect_factory(self._hub, guild_id, str(channel.id))
                voice_channel_id = str(channel.id)
            elif await self._manager.status(guild_id) is None:
                return MusicToolOutcome(
                    status="unavailable", note=m.not_in_allowed_guild_note(),
                )
        _, _, notify_channel_id = conversation_id.partition("/")
        # Attribution only (who gets credit for queuing the track) -- unlike
        # the voice-channel lookup above, this doesn't need to be the
        # platform-confirmed account, so an arbitrary member of the set is
        # fine here.
        requested_by = next(iter(sender_ids), "")
        try:
            outcome = await self._manager.play(
                guild_id=guild_id, query_or_url=query_or_url, requested_by=requested_by,
                added_at=datetime.now(UTC), connect=connect, notify=self._notify(conversation_id),
                voice_channel_id=voice_channel_id,
                notify_channel_id=notify_channel_id or None,
            )
        except Exception:
            log.exception("agent tool play_music failed for guild %s", guild_id)
            return MusicToolOutcome(status="unavailable", note=m.join_failed_note())
        if outcome is None:
            return _UNAVAILABLE_NOT_IN_VOICE
        if outcome.status == "nothing_found" or outcome.track is None:
            return MusicToolOutcome(status="nothing_found", note=m.nothing_found_line())
        return MusicToolOutcome(
            status="ok", title=outcome.track.title, url=outcome.track.url,
            queued=outcome.status == "queued",
        )

    async def skip(self, *, conversation_id: str) -> MusicToolOutcome:
        skipped = await self._manager.skip(self._guild_id(conversation_id))
        if skipped is None:
            return _NOTHING_PLAYING
        return MusicToolOutcome(status="ok", title=skipped.title, url=skipped.url)

    async def top(self, *, conversation_id: str, position: int) -> MusicToolOutcome:
        moved = await self._manager.top(self._guild_id(conversation_id), position)
        if moved is None:
            return _NOTHING_QUEUED_AT_POSITION
        return MusicToolOutcome(status="ok", title=moved.title, url=moved.url)

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

    async def set_volume(
        self, *, conversation_id: str, percent: int,
    ) -> MusicToolOutcome:
        """Same single-outcome shape `/music volume` uses
        (`music_manager.set_volume`: `None` = no session, `False` = out of
        range) — the agent tool must never raise past it (R-5)."""
        try:
            result = await self._manager.set_volume(
                self._guild_id(conversation_id), percent / 100.0,
            )
        except Exception:
            log.exception("agent tool set_volume failed for guild %s", conversation_id)
            return _UNAVAILABLE_DISCONNECTED
        if result is None:
            return _NOTHING_PLAYING
        if not result:
            # `False` = out of range or the N-4 teardown race — never a
            # "nothing playing" claim. The agent tool body gates 0-200
            # before reaching here, so this branch is defense-in-depth;
            # unavailable (couldn't apply) beats nothing_found (there's
            # no session), which would contradict the note.
            return MusicToolOutcome(
                status="unavailable", note=m.volume_out_of_range_line(),
            )
        return MusicToolOutcome(status="ok", note=m.volume_line(percent))
