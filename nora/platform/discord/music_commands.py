"""`/music play|skip|queue|pause|resume|stop` — the slash-command control
surface (design/discord-music/02-control-surface.md).

**Not unit-tested.** Every function here takes a real `discord.Interaction`
— a `discord.Member`'s `.voice`, a `discord.VoiceChannel.connect()` — and
faking those faithfully enough to be worth anything would mean
reimplementing `discord.py`'s own object graph (CLAUDE.md's fake-realism
rule: a low-fidelity fake here would be worse than no test, not better).
Every actual DECISION (join-or-reject, play/skip/pause state, idle
disconnect) already lives in `MusicPlayerManager`/`MusicPlayer`, both fully
unit-tested — this module is deliberately kept to pure translation: pull a
few primitives off the `Interaction`, call the manager, format one reply.
Verify by running this against a real bot token and voice channel; there is
no other way to verify it.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_player import PlayOutcome
from nora.platform.discord.voice_session import DiscordVoiceSession

log = logging.getLogger("nora.music")


def _connect_factory(
    manager: MusicPlayerManager, guild_id: str, channel: discord.VoiceChannel,
):
    """Built only when the invoker IS in a voice channel — `None` reaching
    `MusicPlayerManager.play` is what triggers the design's join-rejection,
    so this function is never called at all in the rejection case."""

    async def connect() -> DiscordVoiceSession:
        voice_client: discord.VoiceClient = await channel.connect()

        async def _on_finished() -> None:
            await manager.on_track_finished(guild_id)

        return DiscordVoiceSession(
            voice_client, loop=asyncio.get_running_loop(), on_finished=_on_finished,
        )

    return connect


def _requester_voice_channel(interaction: discord.Interaction) -> discord.VoiceChannel | None:
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None:
        return None
    channel = member.voice.channel
    return channel if isinstance(channel, discord.VoiceChannel) else None


def _reply_for(outcome: PlayOutcome) -> str:
    if outcome.status == "nothing_found" or outcome.track is None:
        return "Couldn't find that."
    verb = "Now playing" if outcome.status == "playing" else "Queued"
    return f"{verb}: {outcome.track.title}"


class MusicCommands(app_commands.Group):
    """Registered as `/music` on the `CommandTree` built inside
    `DiscordAdapter.listen()` — one instance per connection attempt, same
    lifecycle as the `discord.Client` it's attached to
    (`02-control-surface.md`'s "the command tree lives where the client
    lives" note)."""

    def __init__(self, manager: MusicPlayerManager) -> None:
        super().__init__(name="music", description="Play music in a voice channel.")
        self._manager = manager

    @app_commands.command(
        name="play", description="Play a track, or queue it if one's already playing.",
    )
    @app_commands.describe(query="A YouTube URL, or search text")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Music only works in a server, not a DM.", ephemeral=True,
            )
            return
        guild_id = str(interaction.guild_id)
        channel = _requester_voice_channel(interaction)
        connect = (
            _connect_factory(self._manager, guild_id, channel) if channel is not None else None
        )

        await interaction.response.defer()
        outcome = await self._manager.play(
            guild_id=guild_id, query_or_url=query, requested_by=str(interaction.user.id),
            added_at=discord.utils.utcnow(), connect=connect,
        )
        if outcome is None:
            await interaction.followup.send(
                "Join a voice channel first, then try again.", ephemeral=True,
            )
            return
        await interaction.followup.send(_reply_for(outcome))

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        skipped = await self._manager.skip(str(interaction.guild_id))
        if skipped is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.send_message(f"Skipped: {skipped.title}")

    @app_commands.command(name="queue", description="Show what's playing and queued.")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        status = await self._manager.status(str(interaction.guild_id))
        if status is None or status.now_playing is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        suffix = " (paused)" if status.paused else ""
        lines = [f"Now playing: {status.now_playing.title}{suffix}"]
        lines += [f"{i}. {t.title}" for i, t in enumerate(status.queue, start=1)]
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="pause", description="Pause playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        ok = await self._manager.pause(str(interaction.guild_id))
        await interaction.response.send_message(
            "Paused." if ok else "Nothing is playing.", ephemeral=not ok,
        )

    @app_commands.command(name="resume", description="Resume playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        ok = await self._manager.resume(str(interaction.guild_id))
        await interaction.response.send_message(
            "Resumed." if ok else "Not paused.", ephemeral=not ok,
        )

    @app_commands.command(name="stop", description="Stop playback, clear the queue, and leave.")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await self._manager.stop(str(interaction.guild_id))
        await interaction.response.send_message("Stopped.")


async def register(client: discord.Client, manager: MusicPlayerManager) -> None:
    """Built and synced fresh inside `DiscordAdapter.listen()`, same
    lifecycle as `client` itself — a fresh `discord.Client` per connection
    attempt (`adapter.py`'s own reasoning), so a fresh `CommandTree` every
    attempt too. Re-syncing identical commands on every reconnect is
    slightly wasteful but always correct; tracking "already synced" to skip
    it is a real optimization left for if reconnects turn out frequent
    enough to matter (design/discord-music/04-deferred.md's evidence-bar
    pattern, applied here rather than pre-optimized)."""
    tree = app_commands.CommandTree(client)
    tree.add_command(MusicCommands(manager))
    await tree.sync()

    async def on_voice_state_update(
        _member: discord.Member, before: discord.VoiceState, after: discord.VoiceState,
    ) -> None:
        # A different event name than `DiscordListener` registers
        # (`on_ready`/`on_resumed`/`on_message`/`on_disconnect`), so this
        # coexists with it rather than clobbering — `Client.event()` binds
        # per NAME.
        left_channel = before.channel
        if left_channel is None or after.channel == left_channel:
            return  # not "left a channel" (e.g. a mute/deafen toggle)
        if any(not m.bot for m in left_channel.members):
            return  # still has humans
        await manager.on_voice_channel_emptied(str(left_channel.guild.id))

    client.event(on_voice_state_update)
