"""`/discord join|exit` — the voice-connection control surface.

The audio-controller owns the ONE Discord voice connection; these two
commands are how a human drives its lifecycle explicitly (everything else —
`/music play`'s auto-join, the wake boot join — reuses the same hub).

**Not unit-tested beyond the pure gate** (`voice_gate.py`, covered by
`tests/unit/test_voice_gate.py`) — same posture `music_commands.py`'s own
docstring explains: a real `discord.Interaction` is not faithfully fakeable,
and every actual DECISION (the admission gate, join caching/adoption,
leave-voice teardown) lives in `voice_gate` / `VoiceControllerHub` /
`MusicPlayerManager`, all fully unit-tested. This module is pure
translation.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from nora.infra.voice.http_controller import VoiceControllerError
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord import music_messages as m
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.voice_gate import voice_join_decision

log = logging.getLogger("nora.music")


def _member_voice_channel_id(interaction: discord.Interaction) -> str | None:
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None:
        return None
    channel = member.voice.channel
    return str(channel.id) if isinstance(channel, discord.VoiceChannel) else None


class DiscordCommands(app_commands.Group):
    """Registered as `/discord` beside `/music` in
    `music_commands.register` — same lifecycle (one per connection
    attempt)."""

    def __init__(
        self,
        *,
        hub: VoiceControllerHub | None,
        voice_guild_ids: frozenset[str],
        manager: MusicPlayerManager | None = None,
    ) -> None:
        super().__init__(name="discord", description="Control Nora's voice connection.")
        self._hub = hub
        self._voice_guild_ids = voice_guild_ids
        self._manager = manager

    @app_commands.command(name="join", description="Join your voice channel.")
    async def join(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                m.music_not_in_server_line(), ephemeral=True,
            )
            return
        guild_id = str(interaction.guild_id)
        channel_id = _member_voice_channel_id(interaction)
        decision = voice_join_decision(
            is_member=isinstance(interaction.user, discord.Member),
            guild_id=guild_id, caller_channel_id=channel_id,
            allowed_guilds=self._voice_guild_ids,
        )
        if decision != "ok" or channel_id is None:
            line = (
                m.join_voice_channel_line()
                if decision == "not_in_voice"
                else m.not_in_allowed_guild_line()
            )
            await interaction.response.send_message(line, ephemeral=True)
            return
        log.info("discord command: /discord join guild=%s channel=%s", guild_id, channel_id)
        await interaction.response.defer(ephemeral=True)
        if self._hub is None:
            await interaction.followup.send(m.join_failed_line(), ephemeral=True)
            return
        if self._hub.session_for(guild_id) is not None:
            await interaction.followup.send(m.already_in_voice_line(), ephemeral=True)
            return
        try:
            await self._hub.join(guild_id, channel_id)
        except VoiceControllerError:
            log.exception("voice join failed for guild %s", guild_id)
            await interaction.followup.send(m.join_failed_line(), ephemeral=True)
            return
        await interaction.followup.send(m.joined_voice_line())

    @app_commands.command(name="exit", description="Leave the voice channel.")
    async def exit(self, interaction: discord.Interaction) -> None:
        # The exit gate IS the join gate (§12.3: guild member + caller
        # physically in a voice channel + that channel on the
        # `voice_guild_ids` allow-list — "NO extra carve-out"). Without
        # it any guild member can tear down the shared connection (music +
        # TTS + wake) from anywhere.
        if interaction.guild_id is None:
            await interaction.response.send_message(
                m.music_not_in_server_line(), ephemeral=True,
            )
            return
        guild_id = str(interaction.guild_id)
        decision = voice_join_decision(
            is_member=isinstance(interaction.user, discord.Member),
            guild_id=guild_id,
            caller_channel_id=_member_voice_channel_id(interaction),
            allowed_guilds=self._voice_guild_ids,
        )
        if decision != "ok":
            line = (
                m.join_voice_channel_line()
                if decision == "not_in_voice"
                else m.not_in_allowed_guild_line()
            )
            await interaction.response.send_message(line, ephemeral=True)
            return
        log.info("discord command: /discord exit guild=%s", guild_id)
        await interaction.response.defer(ephemeral=True)
        try:
            if self._manager is not None:
                await self._manager.leave_voice(guild_id)
            left = await self._hub.exit(guild_id) if self._hub is not None else False
        except VoiceControllerError:
            # A controller outage mid-exit must still resolve the deferred
            # interaction.
            log.exception("discord exit failed for guild %s", guild_id)
            await interaction.followup.send(m.controller_unavailable_line(), ephemeral=True)
            return
        await interaction.followup.send(
            m.left_voice_line() if left else m.not_in_voice_channel_line(),
            ephemeral=True,
        )
