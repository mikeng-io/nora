"""The Discord music bot's upcoming-queue (design/discord-music/
03-state-and-persistence.md). `MusicQueueStore` is the persisted half only
— now-playing/playback position lives in-memory on `MusicPlayer` and is
never expressed here; a `QueuedTrack` is, by construction, a track that has
not started playing yet."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


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
        empty queue) IN THE SAME STATEMENT — never a separate read-then-
        write, which would race two `/music play` calls landing for the
        same guild at once onto the same position."""
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


def ensure_utc(value: datetime, *, field: str) -> datetime:
    """Same rule `nora.domain.reminders.ensure_utc` enforces, restated here
    rather than imported: a naive `datetime` names no instant, and a
    `timestamptz` column round-trips whatever offset it is handed, so an
    HKT `added_at` and a UTC one must produce the SAME stored value. Both
    `MusicQueueStore` implementations call this — never each computing the
    offset conversion independently — for the same reason
    `SqlReminderStore`/`FakeReminderStore` share `plan_successor`."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware; got naive {value!r}")
    return value.astimezone(UTC)
