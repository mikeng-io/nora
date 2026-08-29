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
import contextlib
import logging
import math

import discord
from discord import app_commands

from nora.platform.discord import music_messages as m
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_player import PlayerStatus, PlayOutcome
from nora.platform.discord.voice_session import DiscordVoiceSession

log = logging.getLogger("nora.music")

# `/music queue` used to reply with every track in one message, which blew
# past Discord's 2000-char cap outright (HTTPException 50035) once playlist
# expansion (01-playback-and-source.md) made a 50-track queue routine
# instead of a "someone pasted an enormous list" edge case. Real navigation
# (`_QueuePaginator`, below) replaced the truncate-with-a-trailer fix.
_QUEUE_PAGE_SIZE = 10
_QUEUE_VIEW_TIMEOUT_SECONDS = 180.0
# Longer than the queue paginator's -- a listening session runs far longer
# than someone browsing a queue list, and losing the control panel mid-song
# would be a worse surprise than a long-lived one going stale.
_CONTROLS_VIEW_TIMEOUT_SECONDS = 900.0


def _notify_factory(interaction: discord.Interaction):
    """Captures the channel `/music play` was invoked FROM -- the manager
    only actually uses this the moment it starts a fresh session (`03-
    state-and-persistence.md`'s "where it started"), so a no-op fallback
    here for the rare non-messageable channel is fine: nothing calls it in
    the ordinary case anyway."""
    channel = interaction.channel

    async def notify(text: str) -> None:
        if isinstance(channel, discord.abc.Messageable):
            # Silent: a "Now playing" line on every auto-advance is useful
            # to have in the channel, not worth a push notification/ping
            # every few minutes.
            await channel.send(text, silent=True)

    return notify


def _connect_factory(
    manager: MusicPlayerManager, guild_id: str, channel: discord.VoiceChannel,
    *, crossfade_seconds: float,
):
    """Built only when the invoker IS in a voice channel — `None` reaching
    `MusicPlayerManager.play` is what triggers the design's join-rejection,
    so this function is never called at all in the rejection case."""

    async def connect() -> DiscordVoiceSession:
        voice_client: discord.VoiceClient = await channel.connect()

        async def _on_finished() -> None:
            await manager.on_track_finished(guild_id)

        async def _on_voice_death() -> None:
            # The voice connection died while a track was supposedly
            # audible (a silent voice-websocket drop Discord never surfaces
            # as a state change — R-66, found live 2026-08-26: "now its NOT
            # even playing" after a `discord gateway resumed`). The player
            # still believes it is playing; end the session like a stop so
            # a fresh `/music play` joins a clean connection instead of
            # streaming into a dead socket.
            await manager.on_voice_death(guild_id)

        return DiscordVoiceSession(
            voice_client, loop=asyncio.get_running_loop(), on_finished=_on_finished,
            on_voice_death=_on_voice_death, crossfade_seconds=crossfade_seconds,
        )

    return connect


def _requester_voice_channel(interaction: discord.Interaction) -> discord.VoiceChannel | None:
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None:
        return None
    channel = member.voice.channel
    return channel if isinstance(channel, discord.VoiceChannel) else None


def _who(interaction: discord.Interaction) -> str:
    return f"{interaction.user}({interaction.user.id})"


def _reply_for(outcome: PlayOutcome) -> str:
    """Thin dispatch over the canonical message table — the table owns the
    wording; this only picks the line the outcome calls for."""
    if outcome.status == "nothing_found" or outcome.track is None:
        return m.nothing_found_line()
    if outcome.status == "playing":
        return m.now_playing_line(outcome.track)
    return m.queued_line(outcome.track, outcome.queued_count)


class _NowPlayingControls(discord.ui.View):
    """Skip/Pause-Resume/Stop, attached to `/music play`'s own reply --
    edits itself in place on every click rather than sending a fresh reply
    per action, so the same message stays the one place to see what's
    playing and control it. Calls the exact same `MusicPlayerManager`
    methods the slash commands do -- never a second control path."""

    def __init__(self, manager: MusicPlayerManager, guild_id: str) -> None:
        super().__init__(timeout=_CONTROLS_VIEW_TIMEOUT_SECONDS)
        self._manager = manager
        self._guild_id = guild_id
        self.message: discord.Message | None = None

    async def render(self) -> str:
        status = await self._manager.status(self._guild_id)
        if status is None or status.now_playing is None:
            self._disable_all()
            self.stop()
            return m.nothing_playing_line()
        self.pause_resume.label = "▶ Resume" if status.paused else "⏸ Pause"
        return m.now_playing_line(status.now_playing, paused=status.paused)

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        log.info("music control: skip clicked guild=%s user=%s", self._guild_id, _who(interaction))
        await self._manager.skip(self._guild_id)
        await interaction.response.edit_message(content=await self.render(), view=self)

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.secondary)
    async def pause_resume(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        status = await self._manager.status(self._guild_id)
        if status is not None and status.paused:
            log.info(
                "music control: resume clicked guild=%s user=%s", self._guild_id, _who(interaction),
            )
            await self._manager.resume(self._guild_id)
        else:
            log.info(
                "music control: pause clicked guild=%s user=%s", self._guild_id, _who(interaction),
            )
            await self._manager.pause(self._guild_id)
        await interaction.response.edit_message(content=await self.render(), view=self)

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.danger)
    async def stop_playback(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        log.info("music control: stop clicked guild=%s user=%s", self._guild_id, _who(interaction))
        await self._manager.stop(self._guild_id)
        self._disable_all()
        await interaction.response.edit_message(content=m.stopped_line(), view=self)
        self.stop()

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]

    async def on_timeout(self) -> None:
        self._disable_all()
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)


class _QueuePaginator(discord.ui.View):
    """Pages through a guild's queue, `_QUEUE_PAGE_SIZE` tracks at a time.
    Re-fetches the guild's LIVE status on every button click rather than
    reusing the snapshot the view was built from -- the queue keeps
    changing (tracks finish, get skipped, more get added) while the
    message just sits in a channel, and a stale page would drift from
    reality the longer someone waits before clicking Next."""

    def __init__(self, manager: MusicPlayerManager, guild_id: str) -> None:
        super().__init__(timeout=_QUEUE_VIEW_TIMEOUT_SECONDS)
        self._manager = manager
        self._guild_id = guild_id
        self._page = 0
        self.message: discord.Message | None = None

    def render_page(self, status: PlayerStatus) -> str:
        """Caller's job to have already checked `now_playing is not None`
        -- that's what makes it worth showing a queue reply at all."""
        now_playing = status.now_playing
        if now_playing is None:
            return m.nothing_playing_line()
        pages = max(1, math.ceil(len(status.queue) / _QUEUE_PAGE_SIZE))
        self._page = max(0, min(self._page, pages - 1))
        start = self._page * _QUEUE_PAGE_SIZE
        page_tracks = status.queue[start : start + _QUEUE_PAGE_SIZE]
        lines = [m.now_playing_line(now_playing, paused=status.paused)]
        if status.queue:
            lines.append(f"Queue (page {self._page + 1}/{pages}):")
            lines += [f"{start + i + 1}. {t.title}" for i, t in enumerate(page_tracks)]
        self.previous_page.disabled = self._page <= 0
        self.next_page.disabled = self._page >= pages - 1
        return "\n".join(lines)

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        self._page -= 1
        await self._refresh_page(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_page(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        self._page += 1
        await self._refresh_page(interaction)

    async def _refresh_page(self, interaction: discord.Interaction) -> None:
        status = await self._manager.status(self._guild_id)
        if status is None or status.now_playing is None:
            for child in self.children:
                child.disabled = True  # type: ignore[attr-defined]
            await interaction.response.edit_message(content="Nothing is playing.", view=self)
            self.stop()
            return
        await interaction.response.edit_message(content=self.render_page(status), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)


class MusicCommands(app_commands.Group):
    """Registered as `/music` on the `CommandTree` built inside
    `DiscordAdapter.listen()` — one instance per connection attempt, same
    lifecycle as the `discord.Client` it's attached to
    (`02-control-surface.md`'s "the command tree lives where the client
    lives" note)."""

    def __init__(self, manager: MusicPlayerManager, *, crossfade_seconds: float = 0.0) -> None:
        super().__init__(name="music", description="Play music in a voice channel.")
        self._manager = manager
        self._crossfade_seconds = crossfade_seconds

    @app_commands.command(
        name="play", description="Play a track, or queue it if one's already playing.",
    )
    @app_commands.describe(query="A YouTube URL, or search text")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                m.music_not_in_server_line(), ephemeral=True,
            )
            return
        guild_id = str(interaction.guild_id)
        log.info(
            "music command: /music play guild=%s user=%s query=%r",
            guild_id, _who(interaction), query,
        )
        channel = _requester_voice_channel(interaction)
        connect = (
            _connect_factory(
                self._manager, guild_id, channel, crossfade_seconds=self._crossfade_seconds,
            )
            if channel is not None else None
        )

        notify_channel_id = str(interaction.channel_id) if interaction.channel_id else None
        await interaction.response.defer()
        try:
            outcome = await self._manager.play(
                guild_id=guild_id, query_or_url=query, requested_by=str(interaction.user.id),
                added_at=discord.utils.utcnow(), connect=connect,
                notify=_notify_factory(interaction),
                voice_channel_id=str(channel.id) if channel is not None else None,
                notify_channel_id=notify_channel_id,
            )
        except Exception:
            # `connect()` (inside `connect`, above) can raise -- a rate
            # limit, a permission error, a Gateway timeout. Without this,
            # the already-deferred interaction is left unresolved and
            # Discord shows "The application did not respond" with nothing
            # in the logs to explain why.
            log.exception("music play failed for guild %s", guild_id)
            await interaction.followup.send(
                m.join_failed_line(), ephemeral=True, silent=True,
            )
            return
        if outcome is None:
            log.info(
                "music command: /music play guild=%s -> rejected (not in a voice channel)",
                guild_id,
            )
            await interaction.followup.send(
                m.join_voice_channel_line(), ephemeral=True, silent=True,
            )
            return
        log.info("music command: /music play guild=%s -> %s", guild_id, outcome.status)
        if outcome.status == "nothing_found":
            await interaction.followup.send(_reply_for(outcome), silent=True)
            return
        controls = _NowPlayingControls(self._manager, guild_id)
        await interaction.followup.send(_reply_for(outcome), view=controls, silent=True)
        controls.message = await interaction.original_response()

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info("music command: /music skip guild=%s user=%s", guild_id, _who(interaction))
        await interaction.response.defer()
        skipped = await self._manager.skip(guild_id)
        if skipped is None:
            await interaction.followup.send(m.nothing_playing_line(), silent=True)
            return
        await interaction.followup.send(m.skipped_line(skipped), silent=True)

    @app_commands.command(
        name="top", description="Move a queued track to play next.",
    )
    @app_commands.describe(position="The queue position shown by /music queue (1 = plays next)")
    async def top(
        self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 500],
    ) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info(
            "music command: /music top guild=%s user=%s position=%s",
            guild_id, _who(interaction), position,
        )
        await interaction.response.defer()
        moved = await self._manager.top(guild_id, position)
        if moved is None:
            await interaction.followup.send(m.nothing_queued_at_position_line(), silent=True)
            return
        await interaction.followup.send(m.next_line(moved), silent=True)

    @app_commands.command(name="queue", description="Show what's playing and queued.")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        status = await self._manager.status(guild_id)
        if status is None or status.now_playing is None:
            await interaction.followup.send(m.nothing_playing_line(), silent=True)
            return
        view = _QueuePaginator(self._manager, guild_id)
        content = view.render_page(status)
        if len(status.queue) <= _QUEUE_PAGE_SIZE:
            # Fits on one screen -- no buttons that would only ever sit
            # disabled.
            await interaction.followup.send(content, silent=True)
            return
        await interaction.followup.send(content, view=view, silent=True)
        view.message = await interaction.original_response()

    @app_commands.command(name="pause", description="Pause playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info("music command: /music pause guild=%s user=%s", guild_id, _who(interaction))
        await interaction.response.defer()
        ok = await self._manager.pause(guild_id)
        await interaction.followup.send(
            m.paused_line() if ok else m.nothing_playing_line(), silent=True,
        )

    @app_commands.command(name="resume", description="Resume playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info("music command: /music resume guild=%s user=%s", guild_id, _who(interaction))
        await interaction.response.defer()
        ok = await self._manager.resume(guild_id)
        await interaction.followup.send(
            m.resumed_line() if ok else m.not_paused_line(), silent=True,
        )

    @app_commands.command(name="stop", description="Stop playback, clear the queue, and leave.")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info("music command: /music stop guild=%s user=%s", guild_id, _who(interaction))
        await interaction.response.defer()
        await self._manager.stop(guild_id)
        await interaction.followup.send(m.stopped_line(), silent=True)

    @app_commands.command(name="volume", description="Set playback volume (0-200%).")
    @app_commands.describe(percent="Volume as a percentage, 0-200 (100 = normal)")
    async def volume(
        self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 200],
    ) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info(
            "music command: /music volume guild=%s user=%s percent=%s",
            guild_id, _who(interaction), percent,
        )
        await interaction.response.defer()
        # Discord's own UI already enforces 0-200 via Range above; the
        # manager's own [0.0, 2.0] check is defense-in-depth, not the
        # primary guard -- kept because this class must not be the only
        # thing standing between a malformed value and MusicPlayer.
        result = await self._manager.set_volume(guild_id, percent / 100.0)
        if result is None:
            await interaction.followup.send(m.nothing_playing_line(), silent=True)
            return
        if not result:
            await interaction.followup.send(m.volume_out_of_range_line(), silent=True)
            return
        await interaction.followup.send(m.volume_line(percent), silent=True)


async def register(
    client: discord.Client, manager: MusicPlayerManager, *, crossfade_seconds: float = 0.0,
) -> None:
    """Built and synced fresh inside `DiscordAdapter.listen()`, same
    lifecycle as `client` itself — a fresh `discord.Client` per connection
    attempt (`adapter.py`'s own reasoning), so a fresh `CommandTree` every
    attempt too. Re-syncing identical commands on every reconnect is
    slightly wasteful but always correct; tracking "already synced" to skip
    it is a real optimization left for if reconnects turn out frequent
    enough to matter (design/discord-music/04-deferred.md's evidence-bar
    pattern, applied here rather than pre-optimized)."""
    tree = app_commands.CommandTree(client)
    tree.add_command(MusicCommands(manager, crossfade_seconds=crossfade_seconds))
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
        bot_voice_client = left_channel.guild.voice_client
        if bot_voice_client is None or bot_voice_client.channel != left_channel:
            # The bot isn't connected to THIS channel — an unrelated voice
            # channel in the same guild emptying must not tear down a
            # session that's actively playing somewhere else.
            return
        if any(not m.bot for m in left_channel.members):
            return  # still has humans
        guild_id = str(left_channel.guild.id)
        log.info("music control: voice channel emptied, leaving guild=%s", guild_id)
        await manager.on_voice_channel_emptied(guild_id)

    client.event(on_voice_state_update)


async def resume_persisted_sessions(
    client: discord.Client, manager: MusicPlayerManager, *, crossfade_seconds: float,
) -> None:
    """Boot-time resume (design/discord-music/06-session-persistence.md
    stage 2): reconnects a guild's session ONLY if its voice channel still
    has a human in it right now -- symmetric with the existing "leave when
    a channel empties of humans" rule (`on_voice_state_update`, above): a
    deploy must not make Nora silently rejoin and play into an empty room.
    A channel that's gone entirely, or emptied while Nora was down, gets
    its stale row discarded instead of a reconnect attempt.

    Safe to run on every `on_ready`, not just a genuine process boot --
    `MusicPlayerManager.resume_persisted_session` already refuses to
    clobber a guild that already has a live player."""
    for session in await manager.persisted_sessions():
        channel = client.get_channel(int(session.voice_channel_id))
        if channel is None:
            try:
                channel = await client.fetch_channel(int(session.voice_channel_id))
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.VoiceChannel) or not any(
            not member.bot for member in channel.members
        ):
            log.info(
                "not resuming music session for guild %s (channel gone or empty)",
                session.guild_id,
            )
            await manager.discard_persisted_session(session.guild_id)
            continue

        notify_channel = client.get_channel(int(session.notify_channel_id))

        async def notify(text: str, *, _channel=notify_channel) -> None:
            if isinstance(_channel, discord.abc.Messageable):
                await _channel.send(text, silent=True)

        try:
            await manager.resume_persisted_session(
                session,
                connect=_connect_factory(
                    manager, session.guild_id, channel, crossfade_seconds=crossfade_seconds,
                ),
                notify=notify,
            )
        except Exception:
            log.exception("failed to resume music session for guild %s", session.guild_id)
