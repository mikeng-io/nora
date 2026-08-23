"""The Discord music bot's persisted state (design/discord-music/
03-state-and-persistence.md, 06-session-persistence.md). Two stores, two
different shapes:

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

__all__ = ["MusicQueueStore", "MusicSession", "MusicSessionStore", "QueuedTrack", "ensure_utc"]


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
        a same-statement subquery is not sufficient to guarantee that
        (verified live against Postgres, subagent review 2026-08-23: it
        collides on every run under READ COMMITTED once two transactions
        genuinely overlap). Whatever mechanism an implementation uses
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

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MusicSession:
    """One row per guild -- a snapshot of what `MusicPlayer` holds right
    now, not an append log (contrast `QueuedTrack`). `voice_channel_id`/
    `notify_channel_id` are captured once, at session start, and never
    change for this row's lifetime; everything else is overwritten on
    every transition. `now_playing_*` are all `None` together exactly when
    nothing is currently playing (queue empty, waiting on the idle timer)
    -- the row still exists (the voice connection is still live), it's
    just idle."""

    guild_id: str
    voice_channel_id: str
    notify_channel_id: str
    now_playing_source: str | None
    now_playing_locator: str | None
    now_playing_title: str | None
    paused: bool
    volume: float
    updated_at: datetime


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


# Re-exported, not re-implemented: `nora.domain.events` already established
# the precedent (imports the same function rather than restating the naive-
# datetime guard a second time) — a naive `added_at` names no instant for
# exactly the reason it names no instant for a reminder, and two copies of
# that check are two copies to keep in sync when it changes.
