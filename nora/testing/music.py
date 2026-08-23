"""`FakeMusicQueueStore` — the in-memory second implementation of
`nora.domain.music.MusicQueueStore`.

Bound to `nora.infra.sql.music_queue.SqlMusicQueueStore` by
`tests/contract/test_music_queue_store_contract.py`, exactly as CLAUDE.md
requires of any port with two implementations.

Where a dict cannot coast (same shape `nora.testing.reminders`' own
docstring calls out): `enqueue` must derive `position` from THIS guild's
current rows, not a global counter, and `added_at` must go through
`ensure_utc` the same way the SQL store's `timestamptz` column does it for
free.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from nora.domain.music import QueuedTrack, ensure_utc


class FakeMusicQueueStore:
    """In-memory MusicQueueStore. Passes tests/contract/test_music_queue_store_contract.py."""

    def __init__(self) -> None:
        self._rows: dict[UUID, QueuedTrack] = {}

    def _for_guild(self, guild_id: str) -> list[QueuedTrack]:
        return sorted(
            (t for t in self._rows.values() if t.guild_id == guild_id),
            key=lambda t: t.position,
        )

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
        existing = self._for_guild(guild_id)
        next_position = 0 if not existing else existing[-1].position + 1
        track = QueuedTrack(
            id=uuid4(), guild_id=guild_id, position=next_position, source=source,
            url=url, title=title, added_by_person_id=added_by_person_id,
            added_at=ensure_utc(added_at, field="added_at"),
        )
        self._rows[track.id] = track
        return track

    async def pop_next(self, guild_id: str) -> QueuedTrack | None:
        queue = self._for_guild(guild_id)
        if not queue:
            return None
        first = queue[0]
        del self._rows[first.id]
        return first

    async def queue_for(self, guild_id: str) -> Sequence[QueuedTrack]:
        return self._for_guild(guild_id)

    async def clear(self, guild_id: str) -> int:
        to_remove = [t.id for t in self._for_guild(guild_id)]
        for track_id in to_remove:
            del self._rows[track_id]
        return len(to_remove)

    async def aclose(self) -> None:
        return None
