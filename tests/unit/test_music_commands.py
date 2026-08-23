"""`nora.platform.discord.music_commands`'s PURE helper functions only
(`_reply_for`, `_queue_reply`) — the module's own docstring explains why the
rest isn't unit-tested (every real DECISION lives in `MusicPlayer`/
`MusicPlayerManager`, both fully covered elsewhere; this module is pure
translation of a real `discord.Interaction`, not faithfully fakeable). These
two functions never touch an `Interaction` at all — `PlayerStatus`/
`QueuedTrack` in, a plain `str` out — so they're worth covering directly,
especially `_queue_reply`'s truncation: Discord rejects any reply over 2000
chars outright, and a 50-track playlist queue routinely exceeds that.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from nora.domain.music import QueuedTrack
from nora.platform.discord.music_commands import _queue_reply, _reply_for
from nora.platform.discord.music_player import PlayerStatus, PlayOutcome

T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _track(title: str) -> QueuedTrack:
    return QueuedTrack(
        id=uuid4(), guild_id="guild-1", position=0, source="youtube",
        url="https://www.youtube.com/watch?v=x", title=title,
        added_by_person_id="miles", added_at=T0,
    )


def test_reply_for_nothing_found() -> None:
    assert _reply_for(PlayOutcome(status="nothing_found", track=None)) == "Couldn't find that."


def test_reply_for_a_single_track_has_no_playlist_suffix() -> None:
    outcome = PlayOutcome(status="playing", track=_track("Track A"), queued_count=1)
    assert _reply_for(outcome) == "Now playing: Track A"


def test_reply_for_a_playlist_expansion_names_the_first_and_the_total() -> None:
    outcome = PlayOutcome(status="queued", track=_track("Track A"), queued_count=5)
    assert _reply_for(outcome) == "Queued: Track A (+4 more from the playlist)"


def test_queue_reply_lists_every_track_when_it_fits() -> None:
    status = PlayerStatus(
        now_playing=_track("Now Playing"), paused=False,
        queue=[_track("Track 1"), _track("Track 2")],
    )

    reply = _queue_reply(status)

    assert reply == "Now playing: Now Playing\n1. Track 1\n2. Track 2"
    assert len(reply) <= 2000


def test_queue_reply_marks_paused() -> None:
    status = PlayerStatus(now_playing=_track("Now Playing"), paused=True, queue=[])

    assert _queue_reply(status) == "Now playing: Now Playing (paused)"


def test_queue_reply_truncates_a_playlist_sized_queue_to_fit_discords_limit() -> None:
    """The exact bug this exists for: a 50-track playlist queue used to
    blow straight past Discord's 2000-char cap and the whole command
    silently failed (`HTTPException 50035`, no reply the user ever saw)."""
    queue = [_track(f"Track {i} — a reasonably long title to pad this out") for i in range(50)]
    status = PlayerStatus(now_playing=_track("Now Playing"), paused=False, queue=queue)

    reply = _queue_reply(status)

    assert len(reply) <= 2000
    assert reply.startswith("Now playing: Now Playing")
    assert "more." in reply.splitlines()[-1]
    # Every track actually included is a real, in-order prefix of the queue
    # -- truncation drops from the END, it never skips or reorders.
    shown_titles = [line.split(". ", 1)[1] for line in reply.splitlines()[1:-1]]
    assert shown_titles == [t.title for t in queue[: len(shown_titles)]]
