"""`nora.platform.discord.music_commands`'s PURE helper functions and
`_QueuePaginator.render_page` — the module's own docstring explains why the
command handlers themselves aren't unit-tested (every real DECISION lives in
`MusicPlayer`/`MusicPlayerManager`, both fully covered elsewhere; this module
is pure translation of a real `discord.Interaction`, not faithfully
fakeable). `_reply_for` and `render_page` never touch an `Interaction` at
all — `PlayerStatus`/`QueuedTrack` in, a plain `str` out (plus button state
as a side effect for `render_page`) — so they're worth covering directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import discord

from nora.domain.music import QueuedTrack
from nora.platform.discord.music_commands import (
    _QUEUE_PAGE_SIZE,
    _QueuePaginator,
    _reply_for,
)
from nora.platform.discord.music_player import PlayerStatus, PlayOutcome

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _track(title: str) -> QueuedTrack:
    return QueuedTrack(
        id=uuid4(), guild_id="guild-1", position=0, source="youtube",
        url="https://www.youtube.com/watch?v=x", title=title,
        added_by_person_id="miles", added_at=T0,
    )


def _paginator() -> _QueuePaginator:
    # `render_page` never touches `_manager`/`_guild_id` -- only a button
    # click does, and that's the not-unit-tested `Interaction`-facing half
    # this module's docstring already excludes.
    return _QueuePaginator(manager=None, guild_id="guild-1")  # type: ignore[arg-type]


def test_reply_for_nothing_found() -> None:
    assert _reply_for(PlayOutcome(status="nothing_found", track=None)) == "Couldn't find that."


def test_reply_for_a_single_track_has_no_playlist_suffix() -> None:
    outcome = PlayOutcome(status="playing", track=_track("Track A"), queued_count=1)
    assert _reply_for(outcome) == (
        "Now playing: Track A — https://www.youtube.com/watch?v=x"
    )


def test_reply_for_a_playlist_expansion_names_the_first_and_the_total() -> None:
    outcome = PlayOutcome(status="queued", track=_track("Track A"), queued_count=5)
    assert _reply_for(outcome) == (
        "Queued: Track A — https://www.youtube.com/watch?v=x (+4 more from the playlist)"
    )


def test_render_page_lists_every_track_when_it_fits() -> None:
    status = PlayerStatus(
        now_playing=_track("Now Playing"), paused=False,
        queue=[_track("Track 1"), _track("Track 2")],
    )

    reply = _paginator().render_page(status)

    assert reply == (
        "Now playing: Now Playing — https://www.youtube.com/watch?v=x\n"
        "Queue (page 1/1):\n1. Track 1\n2. Track 2"
    )


def test_render_page_marks_paused() -> None:
    status = PlayerStatus(now_playing=_track("Now Playing"), paused=True, queue=[])

    assert _paginator().render_page(status) == (
        "Now playing: Now Playing — https://www.youtube.com/watch?v=x (paused)"
    )


def test_render_page_nothing_playing_reports_that_plainly() -> None:
    status = PlayerStatus(now_playing=None, paused=False, queue=[])

    assert _paginator().render_page(status) == "Nothing is playing."


def test_render_page_paginates_a_long_queue() -> None:
    queue = [_track(f"Track {i}") for i in range(25)]
    status = PlayerStatus(now_playing=_track("Now Playing"), paused=False, queue=queue)
    view = _paginator()

    page1 = view.render_page(status)
    assert "page 1/3" in page1
    assert "1. Track 0" in page1
    assert f"{_QUEUE_PAGE_SIZE}. Track {_QUEUE_PAGE_SIZE - 1}" in page1
    assert f"Track {_QUEUE_PAGE_SIZE}" not in page1  # first track of page 2 not shown yet
    assert view.previous_page.disabled is True
    assert view.next_page.disabled is False

    view._page += 1
    page2 = view.render_page(status)
    assert "page 2/3" in page2
    assert f"{_QUEUE_PAGE_SIZE + 1}. Track {_QUEUE_PAGE_SIZE}" in page2
    assert view.previous_page.disabled is False
    assert view.next_page.disabled is False

    view._page += 1
    page3 = view.render_page(status)
    assert "page 3/3" in page3
    assert "25. Track 24" in page3
    assert view.previous_page.disabled is False
    assert view.next_page.disabled is True


def test_render_page_clamps_an_out_of_range_page_back_into_bounds() -> None:
    """The queue can SHRINK between renders (tracks played through, or
    skipped) -- a page number that was valid a moment ago must not produce
    an empty or out-of-range page."""
    status = PlayerStatus(
        now_playing=_track("Now Playing"), paused=False,
        queue=[_track(f"Track {i}") for i in range(5)],
    )
    view = _paginator()
    view._page = 99

    reply = view.render_page(status)

    assert "page 1/1" in reply
    assert view.next_page.disabled is True


def test_the_only_view_in_the_music_surface_is_the_queue_paginator() -> None:
    """Music is controlled by asking Nora or by a slash command -- there is no
    third control path.

    The now-playing reply carries no Skip/Pause/Stop buttons: they would be a
    second surface for every verb the slash commands already own, each click
    an interaction with its own ~3s acknowledgement deadline over a control
    plane whose round-trips routinely outlast it.

    This pins the ABSENCE structurally rather than by name: any new
    `discord.ui.View` in this module fails it, so re-adding controls is a
    deliberate act and not a drive-by."""
    import inspect

    import nora.platform.discord.music_commands as mod

    views = {
        name
        for name, obj in vars(mod).items()
        if inspect.isclass(obj)
        and issubclass(obj, discord.ui.View)
        and obj is not discord.ui.View
    }

    unexpected = views - {"_QueuePaginator"}
    assert views == {"_QueuePaginator"}, f"unexpected control view(s): {unexpected}"
