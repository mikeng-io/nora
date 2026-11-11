from __future__ import annotations

import logging
from collections.abc import Mapping

from nora.agent.toolkit.results import _record
from nora.agent.toolkit.spec import _spec
from nora.domain.ports import (
    MusicToolOutcome,
)

log = logging.getLogger("nora.tools")


_MUSIC_NOT_DISCORD: Mapping[str, object] = {
    "status": "unavailable",
    "note": "Music only works in a Discord voice channel. Say you can't play music "
    "here, and don't pretend to.",
}

_MUSIC_OFF: Mapping[str, object] = {
    "status": "unavailable",
    "note": "I can't reach the music player right now. Say so, and don't pretend it worked.",
}

_MUSIC_NO_TAGGER: Mapping[str, object] = {
    "status": "unavailable",
    "note": "I couldn't tell who's asking, so I can't check your voice channel. "
    "Say so, and don't guess a channel.",
}

_MUSIC_REPORT: Mapping[str, str] = {
    "nothing_found": "Say so plainly, and don't guess.",
    "unavailable": "Say you couldn't, and don't guess a channel or pretend it worked.",
}


def _music_result(outcome: MusicToolOutcome) -> dict[str, object]:
    """Render a `MusicToolOutcome` into the model-facing tool result — only"""
    result: dict[str, object] = {"status": outcome.status}
    if outcome.title is not None:
        result["title"] = outcome.title
    if outcome.url is not None:
        result["url"] = outcome.url
    if outcome.queued:
        result["queued"] = True
    report = _MUSIC_REPORT.get(outcome.status, "")
    note = " ".join(part for part in (outcome.note, report) if part)
    if note:
        result["note"] = note
    return result


def _make__music_sender_ids(deps):
    persons = deps.persons
    room = deps.room

    def _music_sender_ids() -> frozenset[str]:
        """The tagger's raw Discord ids — `MusicToolBridge.play`'s"""
        tagger = room.tagger
        if tagger is None:
            return frozenset()
        return persons.platform_ids(room.platform, tagger.person_id)

    return _music_sender_ids


def _make_play_music(deps):
    conversation_id = deps.conversation_id
    metrics = deps.metrics
    music = deps.music
    room = deps.room
    _music_sender_ids = _make__music_sender_ids(deps)

    async def play_music(query: str) -> dict:
        """Play a search term or a YouTube link in the asker's Discord voice
        channel. Call this when someone asks you to play music.

        The asker must already be in a voice channel for a first track.
        `query` is a search term (the top YouTube match plays) or a URL. Only a
        real playlist URL (youtube.com/playlist?list=...) queues several tracks;
        a long video titled as a playlist is still one track. For a playlist or
        a genre, find a real playlist page with web_search first, and fall back
        to a single video only if none turns up.

        Returns `status`: ok, nothing_found or unavailable, with a `note` on how
        to report it.

        Args:
            query: What to play: a search term, a video URL or a playlist URL.
        """
        if room.platform != "discord":
            _record(metrics, "play_music", "unavailable")
            return dict(_MUSIC_NOT_DISCORD)
        if music is None:
            _record(metrics, "play_music", "unavailable")
            return dict(_MUSIC_OFF)
        sender_ids = _music_sender_ids()
        if not sender_ids:
            _record(metrics, "play_music", "unavailable")
            return dict(_MUSIC_NO_TAGGER)
        try:
            outcome = await music.play(
                conversation_id=conversation_id,
                sender_ids=sender_ids,
                query_or_url=query,
            )
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("play_music failed")
            outcome = None
        if outcome is None:
            _record(metrics, "play_music", "unavailable")
            return dict(_MUSIC_OFF)
        _record(metrics, "play_music", outcome.status)
        result = _music_result(outcome)
        if outcome.status == "ok":
            # How to report a start (ADR 0015 §3), per `queued`: the system posts
            # no «Now playing» line for a session started by request, so her reply
            # is the only announcement, and «queued» must never be said as «now
            result["note"] = (
                "Queued behind what is playing: say it is queued, not playing now."
                if outcome.queued
                else "Started playing now. Your reply is the only announcement the "
                "channel gets, so name the track."
            )
        return result

    return play_music


def _make_skip_music(deps):
    conversation_id = deps.conversation_id
    metrics = deps.metrics
    music = deps.music
    room = deps.room

    async def skip_music() -> dict:
        """Skip the track playing in this Discord voice channel. Call this when
        somebody wants the current song moved on.

        Returns `status`: ok (`title` is what was skipped), nothing_found or
        unavailable, with a `note` when it is not ok.
        """
        if room.platform != "discord":
            _record(metrics, "skip_music", "unavailable")
            return dict(_MUSIC_NOT_DISCORD)
        if music is None:
            _record(metrics, "skip_music", "unavailable")
            return dict(_MUSIC_OFF)
        try:
            outcome = await music.skip(conversation_id=conversation_id)
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("skip_music failed")
            outcome = None
        if outcome is None:
            _record(metrics, "skip_music", "unavailable")
            return dict(_MUSIC_OFF)
        _record(metrics, "skip_music", outcome.status)
        return _music_result(outcome)

    return skip_music


def _make_top_music(deps):
    conversation_id = deps.conversation_id
    metrics = deps.metrics
    music = deps.music
    room = deps.room

    async def top_music(position: int) -> dict:
        """Move a track queued in this Discord voice channel to play next. Call
        this when somebody wants a queued song sooner.

        `position` is the number the queue shows beside the track (1 already
        plays next). Use only a number someone named or that was shown in this
        conversation; never guess one.

        Returns `status`: ok (`title` is the track now next), nothing_found or
        unavailable, with a `note` when it is not ok.

        Args:
            position: The 1-based queue position to move to the front.
        """
        if room.platform != "discord":
            _record(metrics, "top_music", "unavailable")
            return dict(_MUSIC_NOT_DISCORD)
        if music is None:
            _record(metrics, "top_music", "unavailable")
            return dict(_MUSIC_OFF)
        try:
            outcome = await music.top(conversation_id=conversation_id, position=position)
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("top_music failed")
            outcome = None
        if outcome is None:
            _record(metrics, "top_music", "unavailable")
            return dict(_MUSIC_OFF)
        _record(metrics, "top_music", outcome.status)
        return _music_result(outcome)

    return top_music


def _make_stop_music(deps):
    conversation_id = deps.conversation_id
    metrics = deps.metrics
    music = deps.music
    room = deps.room

    async def stop_music() -> dict:
        """Stop the music and leave this Discord voice channel. Call this when
        somebody wants the music off.

        Returns `status`: ok, nothing_found (nothing was playing) or unavailable,
        with a `note` when it is not ok.
        """
        if room.platform != "discord":
            _record(metrics, "stop_music", "unavailable")
            return dict(_MUSIC_NOT_DISCORD)
        if music is None:
            _record(metrics, "stop_music", "unavailable")
            return dict(_MUSIC_OFF)
        try:
            outcome = await music.stop(conversation_id=conversation_id)
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("stop_music failed")
            outcome = None
        if outcome is None:
            _record(metrics, "stop_music", "unavailable")
            return dict(_MUSIC_OFF)
        _record(metrics, "stop_music", outcome.status)
        return _music_result(outcome)

    return stop_music


def _make_music_status(deps):
    conversation_id = deps.conversation_id
    metrics = deps.metrics
    music = deps.music
    room = deps.room

    async def music_status() -> dict:
        """Read what is playing in this Discord voice channel. Call this when
        somebody asks what song is on.

        Returns `status`: ok (`title` is the track playing now), nothing_found
        or unavailable, with a `note` when it is not ok.
        """
        if room.platform != "discord":
            _record(metrics, "music_status", "unavailable")
            return dict(_MUSIC_NOT_DISCORD)
        if music is None:
            _record(metrics, "music_status", "unavailable")
            return dict(_MUSIC_OFF)
        try:
            outcome = await music.status(conversation_id=conversation_id)
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("music_status failed")
            outcome = None
        if outcome is None:
            _record(metrics, "music_status", "unavailable")
            return dict(_MUSIC_OFF)
        _record(metrics, "music_status", outcome.status)
        return _music_result(outcome)

    return music_status


def _make_set_music_volume(deps):
    conversation_id = deps.conversation_id
    metrics = deps.metrics
    music = deps.music
    room = deps.room

    async def set_music_volume(percent: int) -> dict:
        """Set the music volume in this Discord voice channel, up or down. Call
        this when someone asks for it louder or quieter.

        Returns `status`: ok, nothing_found (nothing is playing) or unavailable,
        with a `note`.

        Args:
            percent: The volume, a whole number from 0 to 200 (100 is normal).
        """
        # `percent` is bounded HERE (0-200, same as the slash command's
        # app_commands.Range) because the model is the only gate otherwise: a
        # hallucinated `percent=500` would fall through to the manager's out-of-range
        if not 0 <= percent <= 200:
            _record(metrics, "set_music_volume", "unavailable")
            return {
                "status": "unavailable",
                "note": "Volume must be between 0 and 200. Pick a number in that "
                "range, or ask what they want.",
            }
        if room.platform != "discord":
            _record(metrics, "set_music_volume", "unavailable")
            return dict(_MUSIC_NOT_DISCORD)
        if music is None:
            _record(metrics, "set_music_volume", "unavailable")
            return dict(_MUSIC_OFF)
        try:
            outcome = await music.set_volume(
                conversation_id=conversation_id,
                percent=percent,
            )
        except Exception:  # a tool never raises into the loop (R-5)
            log.exception("set_music_volume failed")
            outcome = None
        if outcome is None:
            _record(metrics, "set_music_volume", "unavailable")
            return dict(_MUSIC_OFF)
        _record(metrics, "set_music_volume", outcome.status)
        return _music_result(outcome)

    return set_music_volume


def build(deps):
    play_music = _make_play_music(deps)
    skip_music = _make_skip_music(deps)
    top_music = _make_top_music(deps)
    stop_music = _make_stop_music(deps)
    music_status = _make_music_status(deps)
    set_music_volume = _make_set_music_volume(deps)
    return (
        _spec("play_music", play_music),
        _spec("skip_music", skip_music),
        _spec("top_music", top_music),
        _spec("stop_music", stop_music),
        _spec("music_status", music_status),
        _spec("set_music_volume", set_music_volume),
    )
