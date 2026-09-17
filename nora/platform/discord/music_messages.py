"""The ONE canonical message table for every user-facing music string
(design/discord-music/09-message-conventions.md).

Three surfaces publish music messages — the slash commands
(`music_commands.py`), the manager's channel auto-notify
(`music_manager._notify_now_playing`), and the agent tool bridge's
model-facing notes (`music_tool_bridge`) — and before this module each
formatted its own strings, which is how `Skipped:` lost the URL, `Now next:`
grew a third verb, and the two control paths drifted into sounding like two
different bots. Every line now renders here, as pure functions over typed
inputs (no `discord.py` objects), so a surface can only rephrase a message
by changing THIS file — visible in one diff, pinned by one test module.

Language policy (D4 of the design node): system announcements are fixed
English strings — deterministic, locale-independent, and the slash surface
carries no LLM to localise. Nora's conversational reply on the tag path is
her own doctrine-register wording and is deliberately NOT rendered here;
with the agent path suppressing the system's session-start post
(`MusicPlayerManager.play(notify_on_play=False)`), her reply is the one
announcement a tag-path play produces.
"""

from __future__ import annotations

from nora.domain.music import QueuedTrack


def now_playing_line(track: QueuedTrack, *, paused: bool = False) -> str:
    """`Now playing: {title} — {url}`, with the URL always present so a
    listener can click through. Moved verbatim from `music_commands` (the
    message-consistency pass, 2026-08-26) so every surface keeps the exact
    same line."""
    suffix = " (paused)" if paused else ""
    return f"Now playing: {track.title} — {track.url}{suffix}"


def autoplay_now_playing_line(track: QueuedTrack) -> str:
    """The autoplay variant of the now-playing line — tells apart a track
    someone actually queued from one the Mix picked
    (design/discord-music/08-autoplay.md)."""
    return f"Autoplaying: {track.title} — {track.url}"


def queued_line(track: QueuedTrack, queued_count: int) -> str:
    """`Queued: {title} — {url}`, naming the playlist total when a playlist
    link expanded into more than one track
    (01-playback-and-source.md) — queuing fifty tracks must not read as
    queuing one."""
    if queued_count <= 1:
        return f"Queued: {track.title} — {track.url}"
    rest = queued_count - 1
    return f"Queued: {track.title} — {track.url} (+{rest} more from the playlist)"


def skipped_line(track: QueuedTrack) -> str:
    """`Skipped: {title} — {url}` — the pre-table reply named the title only,
    the one off-canon drift the 2026-08-26 consistency pass missed."""
    return f"Skipped: {track.title} — {track.url}"


def next_line(track: QueuedTrack) -> str:
    """`Now next: {title} — {url}` — same URL rule as every other
    track-naming line."""
    return f"Now next: {track.title} — {track.url}"


def nothing_playing_line() -> str:
    return "Nothing is playing."


def nothing_queued_at_position_line() -> str:
    return "Nothing queued at that position."


def not_paused_line() -> str:
    return "Not paused."


def paused_line() -> str:
    return "Paused."


def resumed_line() -> str:
    return "Resumed."


def cannot_pause_now_line() -> str:
    """The controller's `pause_during_speech` refusal (design/voice-channel/07
    §5): speech holds the connection, so neither pause nor resume can act —
    the one arbiter-state refusal a music command can surface."""
    return "Can't pause or resume right now — a reply is playing."


def stopped_line() -> str:
    return "Stopped."


def controller_unavailable_line() -> str:
    """B-3 (2026-09-16 audit): a deferred command whose `MusicPlayerManager`
    call raised `VoiceControllerError` — the audio-controller is down or
    unreachable mid-command. Distinct from `join_failed_line` (a failed
    JOIN specifically); this covers stop/skip/pause/resume/volume/exit
    hitting the same outage after a session already exists."""
    return "Lost contact with the voice controller — try again in a moment."


def connection_lost_line() -> str:
    """Announced when the voice connection died and the session was torn
    down — the one transition that used to end playback with ZERO
    notification (found live 2026-09-15: pause mis-read as voice death,
    reconnect failed, session torn down, nobody in the channel knew). The
    line tells the room to re-invoke `/music play` to start again."""
    return "Music stopped — voice connection lost. Use /music play to restart."


def volume_line(percent: int) -> str:
    return f"Volume set to {percent}%."


def volume_out_of_range_line() -> str:
    return "Volume must be between 0 and 200."


def nothing_found_line() -> str:
    return "Couldn't find that."


def join_voice_channel_line() -> str:
    return "Join a voice channel first, then try again."


def music_not_in_server_line() -> str:
    return "Music only works in a server, not a DM."


def join_failed_line() -> str:
    return "Couldn't join the voice channel — try again in a moment."


def not_in_voice_note() -> str:
    """Model-facing note for the agent tools: the requester isn't in a voice
    channel, so a FIRST track can't start."""
    return "You need to be in a voice channel first."


def not_in_allowed_guild_line() -> str:
    """The join gate's refusal (design/voice-channel/07 §12.2, reversed
    2026-09-17): the caller IS in a voice channel, but this SERVER is not on
    the configured allow-list. The user-facing wording stays about the
    channel they are standing in, because that is the thing they can see."""
    return "I can't join that voice channel — this isn't a server I'm allowed in."


def not_in_allowed_guild_note() -> str:
    """Model-facing note for the agent tools: same refusal as the line
    above."""
    return "This server isn't one I'm allowed to join voice in."


def joined_voice_line() -> str:
    return "Joined the voice channel."


def already_in_voice_line() -> str:
    return "I'm already in the voice channel."


def left_voice_line() -> str:
    return "Left the voice channel."


def not_in_voice_channel_line() -> str:
    """`/discord exit` when there is no connection to leave — distinct from
    `join_voice_channel_line`, which tells the CALLER to join one."""
    return "I'm not in a voice channel."


def disconnected_note() -> str:
    """Model-facing note: no live Discord connection right now."""
    return "Discord isn't connected right now."


def join_failed_note() -> str:
    """Model-facing note: the voice join itself failed."""
    return "Couldn't join the voice channel."
