"""`/music play|skip|queue|pause|resume|stop` — the slash-command control
surface.

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

import contextlib
import logging
import math

import discord
from discord import app_commands

from nora.infra.voice.http_controller import (
    GenerationConflict,
    VoiceControllerError,
    VoiceControllerUnavailable,
)
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord import music_messages as m
from nora.platform.discord.discord_commands import DiscordCommands
from nora.platform.discord.music_manager import Connect, MusicPlayerManager
from nora.platform.discord.music_notify import (
    ClientGetter,
    ResolveMusicChannel,
    make_notify,
    no_music_channel,
)
from nora.platform.discord.music_player import PlayerStatus, PlayOutcome, VoiceSession
from nora.platform.discord.voice_gate import voice_join_decision

log = logging.getLogger("nora.music")

# `/music queue` pages through the queue (`_QueuePaginator`, below): one message
# listing every track would blow past Discord's 2000-char cap (HTTPException
# 50035) on a long queue, which playlist expansion makes routine.
_QUEUE_PAGE_SIZE = 10
_QUEUE_VIEW_TIMEOUT_SECONDS = 180.0
# Longer than the queue paginator's -- a listening session runs far longer
# than someone browsing a queue list, and losing the control panel mid-song
# would be a worse surprise than a long-lived one going stale.


def _notify_factory(
    interaction: discord.Interaction,
    guild_id: str,
    *,
    client: ClientGetter,
    resolve_music_channel: ResolveMusicChannel,
):
    """The announcement destination for a session started by `/music play`:
    the `music_channel` one of this guild's `policy.toml` rooms names, else
    the channel this command was invoked from ("where it started"). Both
    halves live in `music_notify.make_notify` — the ONE place any surface
    decides where a music announcement goes.

    `client` is the ADAPTER's getter, deliberately not `interaction.client`:
    this closure is stored in `MusicPlayerManager._notify`
    and outlives the connection attempt the interaction belonged to, and a
    closed `discord.Client` keeps its channel cache — so a captured one
    resolves a channel and then fails at `send`, on every announcement for the
    rest of the session, with nothing re-registering the closure. Only the
    channel id is taken off the interaction."""
    return make_notify(
        client=client,
        guild_id=guild_id,
        fallback_channel_id=(
            str(interaction.channel_id) if interaction.channel_id else None
        ),
        resolve_music_channel=resolve_music_channel,
    )


def _connect_factory(hub: VoiceControllerHub | None, guild_id: str, channel_id: str) -> Connect:
    """Built only when the invoker IS in a voice channel — `None` reaching
    `MusicPlayerManager.play` is what triggers the join-rejection,
    so this function is never called at all in the rejection case.

    The join goes through the hub (the audio-controller owns the connection):
    first join for a guild POSTs `/voice/join`,
    later ones reuse the cached session. A `None` hub means music is enabled
    without an audio-controller configured — the join then fails CLOSED as
    `VoiceControllerUnavailable`, which the command layer already renders as
    the join-failed reply."""

    async def connect() -> VoiceSession:
        if hub is None:
            raise VoiceControllerUnavailable(
                "no audio-controller configured for this guild ([speech.tts] sidecars)"
            )
        return await hub.join(guild_id, channel_id)

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
    lifecycle as the `discord.Client` it's attached to (the command tree lives
    where the client lives)."""

    def __init__(
        self,
        manager: MusicPlayerManager,
        *,
        hub: VoiceControllerHub | None = None,
        voice_guild_ids: frozenset[str] = frozenset(),
        resolve_music_channel: ResolveMusicChannel = no_music_channel,
        client: ClientGetter | None = None,
    ) -> None:
        super().__init__(name="music", description="Play music in a voice channel.")
        self._manager = manager
        self._hub = hub
        self._voice_guild_ids = voice_guild_ids
        self._resolve_music_channel = resolve_music_channel
        # `None` only in a harness that registers commands without an adapter;
        # production always passes `DiscordAdapter.current_client`. Falling
        # back to the interaction's own client keeps such a harness working
        # while production gets the getter that survives a reconnect.
        self._client = client

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
        # The auto-join gate: a FRESH session
        # may only join the caller's current channel, and only when that
        # channel is on the configured allow-list. An EXISTING session is
        # reused regardless (no join happens), so the gate binds only when
        # starting one — `manager.status` is how "fresh" is known before
        # `play` runs.
        decision = voice_join_decision(
            is_member=isinstance(interaction.user, discord.Member),
            guild_id=guild_id,
            caller_channel_id=str(channel.id) if channel is not None else None,
            allowed_guilds=self._voice_guild_ids,
        )
        if decision != "ok" and await self._manager.status(guild_id) is None:
            line = (
                m.join_voice_channel_line()
                if decision == "not_in_voice"
                else m.not_in_allowed_guild_line()
            )
            log.info(
                "music command: /music play guild=%s -> rejected (%s)", guild_id, decision,
            )
            await interaction.response.send_message(line, ephemeral=True)
            return
        connect = (
            _connect_factory(self._hub, guild_id, str(channel.id))
            if channel is not None and decision == "ok" else None
        )

        notify_channel_id = str(interaction.channel_id) if interaction.channel_id else None
        voice_channel_id = (
            str(channel.id) if connect is not None and channel is not None else None
        )
        await interaction.response.defer()
        try:
            outcome = await self._manager.play(
                guild_id=guild_id, query_or_url=query, requested_by=str(interaction.user.id),
                added_at=discord.utils.utcnow(), connect=connect,
                notify=_notify_factory(
                    interaction, guild_id,
                    client=self._client or (lambda: interaction.client),
                    resolve_music_channel=self._resolve_music_channel,
                ),
                voice_channel_id=voice_channel_id,
                notify_channel_id=notify_channel_id,
            )
        except Exception:
            # `connect()` (inside `connect`, above) can raise -- the
            # controller unreachable, a 409 it could not adopt, a timeout.
            # Without this, the already-deferred interaction is left
            # unresolved and Discord shows "The application did not respond"
            # with nothing in the logs to explain why.
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
        # No control view: music is driven by asking Nora, or by the slash
        # commands below, and nothing else. One reply, no components.
        await interaction.followup.send(_reply_for(outcome), silent=True)

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info("music command: /music skip guild=%s user=%s", guild_id, _who(interaction))
        await interaction.response.defer()
        try:
            skipped = await self._manager.skip(guild_id)
        except VoiceControllerError:
            # A controller outage mid-skip would leave this deferred
            # interaction unresolved ("The application did not respond") —
            # the manager's own `commit_pending` errors are already logged
            # there, so this is just the reply.
            log.exception("music skip failed for guild %s", guild_id)
            await interaction.followup.send(m.controller_unavailable_line(), silent=True)
            return
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
        try:
            ok = await self._manager.pause(guild_id)
        except GenerationConflict:
            # Speech holds the connection (controller's `pause_during_speech`)
            # — a refusal, not a crash.
            await interaction.followup.send(m.cannot_pause_now_line(), silent=True)
            return
        except VoiceControllerError:
            log.exception("music pause failed for guild %s", guild_id)
            await interaction.followup.send(m.controller_unavailable_line(), silent=True)
            return
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
        try:
            ok = await self._manager.resume(guild_id)
        except GenerationConflict:
            await interaction.followup.send(m.cannot_pause_now_line(), silent=True)
            return
        except VoiceControllerError:
            log.exception("music resume failed for guild %s", guild_id)
            await interaction.followup.send(m.controller_unavailable_line(), silent=True)
            return
        await interaction.followup.send(
            m.resumed_line() if ok else m.not_paused_line(), silent=True,
        )

    @app_commands.command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        guild_id = str(interaction.guild_id)
        log.info("music command: /music stop guild=%s user=%s", guild_id, _who(interaction))
        await interaction.response.defer()
        try:
            await self._manager.stop(guild_id)
        except VoiceControllerError:
            # `MusicPlayer.stop` deletes the persisted row before
            # this can be raised (a controller outage), so the session is
            # already gone even though the reply below reports the outage.
            log.exception("music stop failed for guild %s", guild_id)
            await interaction.followup.send(m.controller_unavailable_line(), silent=True)
            return
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
        try:
            result = await self._manager.set_volume(guild_id, percent / 100.0)
        except VoiceControllerError:
            log.exception("music volume failed for guild %s", guild_id)
            await interaction.followup.send(m.controller_unavailable_line(), silent=True)
            return
        if result is None:
            await interaction.followup.send(m.nothing_playing_line(), silent=True)
            return
        if not result:
            await interaction.followup.send(m.volume_out_of_range_line(), silent=True)
            return
        await interaction.followup.send(m.volume_line(percent), silent=True)


async def register(
    client: discord.Client,
    manager: MusicPlayerManager,
    *,
    hub: VoiceControllerHub | None = None,
    voice_guild_ids: frozenset[str] = frozenset(),
    resolve_music_channel: ResolveMusicChannel = no_music_channel,
    client_getter: ClientGetter | None = None,
) -> None:
    """Built and synced fresh inside `DiscordAdapter.listen()`, same lifecycle
    as `client` itself — a fresh `discord.Client` per connection attempt
    (`adapter.py`'s own reasoning), so a fresh `CommandTree` every attempt too.
    Re-syncing identical commands on every reconnect is slightly wasteful but
    always correct; tracking "already synced" to skip it is worth doing only if
    reconnects turn out frequent enough to matter.

    Registers `/music` AND the `/discord` voice-control group. There is NO
    `on_voice_state_update` empty-channel handler here: the
    audio-controller observes the channel itself and POSTs `channel-emptied`
    to `/app/v1/voice/event` — nora holds no voice client to watch."""
    tree = app_commands.CommandTree(client)
    tree.add_command(MusicCommands(
        manager, hub=hub, voice_guild_ids=voice_guild_ids,
        resolve_music_channel=resolve_music_channel,
        client=client_getter,
    ))
    tree.add_command(
        DiscordCommands(hub=hub, voice_guild_ids=voice_guild_ids, manager=manager)
    )
    await tree.sync()


async def resume_persisted_sessions(
    client: discord.Client,
    manager: MusicPlayerManager,
    *,
    hub: VoiceControllerHub | None,
    resolve_music_channel: ResolveMusicChannel = no_music_channel,
    client_getter: ClientGetter | None = None,
) -> None:
    """Boot-time resume: reconnects a guild's session ONLY if its voice channel still
    has a human in it right now -- symmetric with the "leave when a channel
    empties of humans" rule: a deploy must not make Nora silently rejoin and
    play into an empty room. A channel that's gone entirely, or emptied
    while Nora was down, gets its stale row discarded instead of a reconnect
    attempt.

    The connect closure goes through `hub.join`, which ADOPTS the
    controller's existing connection + generation when the controller
    outlived a nora restart.

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

        try:
            await manager.resume_persisted_session(
                session,
                connect=_connect_factory(hub, session.guild_id, session.voice_channel_id),
                # The persisted `notify_channel_id` is the FALLBACK, not the
                # destination: a resume re-reads `policy.toml`'s `music_channel`
                # like every other post does, so an operator who set one while
                # Nora was down gets it honoured on the way back up.
                notify=make_notify(
                    # The adapter's getter, not the `on_ready` client this
                    # scan is running under — see `_notify_factory` for what a
                    # captured, later-closed client costs.
                    client=client_getter or (lambda: client),
                    guild_id=session.guild_id,
                    fallback_channel_id=session.notify_channel_id,
                    resolve_music_channel=resolve_music_channel,
                ),
            )
        except Exception:
            log.exception("failed to resume music session for guild %s", session.guild_id)
