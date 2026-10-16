"""The message table's own unit tests — every surface's strings are pinned
here, so a wording change anywhere in the bot is a change to THIS file (and
its diff), never a silent reformat inside a command handler or the notify
path. Pure string-in/string-out; no `discord.py` objects (same seam
`test_music_commands.py` uses)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from nora.domain.music import QueuedTrack
from nora.platform.discord.music_messages import (
    autoplay_now_playing_line,
    cannot_pause_now_line,
    join_failed_line,
    join_failed_note,
    join_voice_channel_line,
    music_not_in_server_line,
    next_line,
    not_in_voice_note,
    not_paused_line,
    nothing_found_line,
    nothing_playing_line,
    nothing_queued_at_position_line,
    now_playing_line,
    paused_line,
    queued_line,
    resumed_line,
    skipped_line,
    stopped_line,
    volume_line,
    volume_out_of_range_line,
)


def _track(title: str = "Track A", url: str = "https://www.youtube.com/watch?v=x") -> QueuedTrack:
    return QueuedTrack(
        id=uuid4(),
        guild_id="g1",
        position=0,
        source="youtube",
        url=url,
        title=title,
        added_by_person_id="person-1",
        added_at=datetime.now(UTC),
    )


def test_now_playing_line_carries_title_and_url() -> None:
    assert now_playing_line(_track()) == (
        "Now playing: Track A — https://www.youtube.com/watch?v=x"
    )


def test_now_playing_line_marks_paused() -> None:
    assert now_playing_line(_track(), paused=True) == (
        "Now playing: Track A — https://www.youtube.com/watch?v=x (paused)"
    )


def test_autoplay_line_says_autoplaying() -> None:
    assert autoplay_now_playing_line(_track()) == (
        "Autoplaying: Track A — https://www.youtube.com/watch?v=x"
    )


def test_queued_line_single_track_has_no_playlist_suffix() -> None:
    assert queued_line(_track(), 1) == "Queued: Track A — https://www.youtube.com/watch?v=x"


def test_queued_line_playlist_expansion_names_the_first_and_the_total() -> None:
    assert queued_line(_track(), 5) == (
        "Queued: Track A — https://www.youtube.com/watch?v=x (+4 more from the playlist)"
    )


def test_skipped_line_carries_the_url_the_old_reply_lost() -> None:
    # /music skip carries the track URL like every other surface.
    assert skipped_line(_track()) == "Skipped: Track A — https://www.youtube.com/watch?v=x"


def test_next_line_carries_the_url() -> None:
    assert next_line(_track()) == "Now next: Track A — https://www.youtube.com/watch?v=x"


def test_the_fixed_state_lines_are_verbatim_from_the_pre_table_surface() -> None:
    assert nothing_playing_line() == "Nothing is playing."
    assert nothing_queued_at_position_line() == "Nothing queued at that position."
    assert not_paused_line() == "Not paused."
    assert paused_line() == "Paused."
    assert resumed_line() == "Resumed."
    assert cannot_pause_now_line() == "Can't pause or resume right now — a reply is playing."
    assert stopped_line() == "Stopped."


def test_the_volume_lines() -> None:
    assert volume_line(150) == "Volume set to 150%."
    assert volume_out_of_range_line() == "Volume must be between 0 and 200."


def test_the_error_and_rejection_lines_are_verbatim_from_the_pre_table_surface() -> None:
    assert nothing_found_line() == "Couldn't find that."
    assert join_voice_channel_line() == "Join a voice channel first, then try again."
    assert music_not_in_server_line() == "Music only works in a server, not a DM."
    assert join_failed_line() == "Couldn't join the voice channel — try again in a moment."


def test_the_model_facing_notes_are_verbatim_from_the_pre_table_bridge() -> None:
    assert not_in_voice_note() == "You need to be in a voice channel first."
    assert join_failed_note() == "Couldn't join the voice channel."
