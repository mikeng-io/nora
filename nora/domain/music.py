"""The Discord music bot's persisted state. Two stores, two different shapes:

- `MusicQueueStore` — an append/pop LOG, one row per upcoming track.
- `MusicSessionStore` — a snapshot, one row per GUILD, holding what
  `MusicPlayer` currently has in memory (now-playing, paused, volume,
  which channels) so a process restart can rejoin and pick back up
  instead of losing the session outright."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from nora.domain.reminders import ensure_utc

__all__ = [
    "AUTOPLAY_PERSON_ID", "MusicQueueStore", "MusicSession", "MusicSessionStore",
    "QueuedTrack", "ensure_utc",
]

# `QueuedTrack.added_by_person_id` sentinel for a track `MusicPlayer._advance`
# enqueued itself rather than a real
# person asking for it -- reuses the existing free-text field instead of a
# schema change, and lets `MusicPlayerManager._notify_now_playing` tell the
# two cases apart without threading a separate flag through `QueuedTrack`.
AUTOPLAY_PERSON_ID = "nora-autoplay"


@dataclass(frozen=True, slots=True)
class QueuedTrack:
    id: UUID
    guild_id: str
    position: int
    source: str
    url: str
    title: str
    added_by_person_id: str
    added_at: datetime


@runtime_checkable
class MusicQueueStore(Protocol):
    async def enqueue(
        self,
        *,
        guild_id: str,
        source: str,
        url: str,
        title: str,
        added_by_person_id: str,
        added_at: datetime,
    ) -> QueuedTrack:
        """Appends to the end of `guild_id`'s queue. `position` is assigned
        by the store as one past the current max for that guild (0 for an
        empty queue) — but NOT racily: two `/music play` calls landing for
        the same guild at once must never both take the same position, and
        a same-statement subquery is not sufficient to guarantee that (it
        collides under READ COMMITTED once two transactions genuinely
        overlap). Whatever mechanism an implementation uses
        (`SqlMusicQueueStore`: a UNIQUE constraint plus a bounded retry) is
        this method's problem to solve, not its caller's."""
        ...

    async def pop_next(self, guild_id: str) -> QueuedTrack | None:
        """Removes and returns the lowest-`position` row for `guild_id`, or
        `None` if the queue is empty. Atomic: the row a caller receives is
        guaranteed already gone from the store, so two concurrent callers
        (a `/music skip` racing a track finishing naturally) can never both
        receive the same track."""
        ...

    async def queue_for(self, guild_id: str) -> Sequence[QueuedTrack]:
        """`guild_id`'s queue, `position` ascending. Never includes whatever
        `MusicPlayer` currently holds as now-playing — that state is not in
        this store at all."""
        ...

    async def clear(self, guild_id: str) -> int:
        """Deletes every row for `guild_id` (`/music stop`). Returns how
        many rows were removed."""
        ...

    async def move_to_front(self, guild_id: str, *, position: int) -> QueuedTrack | None:
        """`/music top` / the `top_music` agent tool: moves the `position`-th
        queued track (1-based, matching what `/music queue` displays — 1 is
        the track that plays next) to the front of `guild_id`'s queue, ahead
        of everything else. `None` if `position` is out of range (including
        an empty queue); the already-at-the-front track for `position == 1`
        is a no-op that still returns it.

        An implementation must reassign every OTHER track's position too
        (not just the target's), atomically, so `uq_music_queue_guild_position`
        is never transiently violated by a caller landing between two
        separate updates — `queue_for` immediately after this call must
        reflect the target as the new lowest-`position` row and every other
        track's relative order otherwise unchanged."""
        ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MusicSession:
    """One row per guild -- a snapshot of what `MusicPlayer` holds right
    now, not an append log (contrast `QueuedTrack`). `voice_channel_id`/
    `notify_channel_id` are captured once, at session start, and never
    change for this row's lifetime — `notify_channel_id` is "the channel
    where it started" and is the FALLBACK destination for announcements,
    not necessarily where they go: a guild one of whose `policy.toml` rooms
    names a `music_channel` routes there instead, resolved per-post rather
    than persisted. Everything else is overwritten on every transition. `now_playing_*` are
    all `None` together exactly when nothing is currently playing (queue
    empty, waiting on the idle timer) -- the row still exists (the voice
    connection is still live), it's just idle."""

    guild_id: str
    voice_channel_id: str
    notify_channel_id: str
    now_playing_source: str | None
    now_playing_locator: str | None
    now_playing_title: str | None
    paused: bool
    volume: float
    updated_at: datetime
    # The most recent track that ACTUALLY started -- the autoplay re-seed
    # seed for boot-time resume (R-67). All three None together means "never
    # played a track in this session" -- no seed to re-seed from, and none
    # must be fabricated.
    last_played_source: str | None = None
    last_played_locator: str | None = None
    last_played_title: str | None = None


@runtime_checkable
class MusicSessionStore(Protocol):
    async def upsert(self, session: MusicSession) -> None:
        """Replaces `session.guild_id`'s row wholesale, or creates it if
        none exists yet."""
        ...

    async def get(self, guild_id: str) -> MusicSession | None: ...

    async def all(self) -> Sequence[MusicSession]:
        """Every persisted session -- the boot-time resume scan's input."""
        ...

    async def delete(self, guild_id: str) -> None:
        """A no-op, not an error, if `guild_id` has no row (`MusicPlayer.
        stop()` calls this unconditionally on the way out)."""
        ...

    async def aclose(self) -> None: ...


# Re-exported, not re-implemented (as `nora.domain.gatherings` does): a naive
# `added_at` names no instant for the same reason it names none for a
# reminder, and two copies of that check would have to be kept in sync.
