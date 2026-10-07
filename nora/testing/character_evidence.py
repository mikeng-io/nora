"""`InMemoryCharacterEvidence` — the double for
`nora.infra.sql.character.SqlCharacterEvidence`, bound to it by
`tests/contract/test_character_evidence_contract.py` so the two cannot drift.

It holds message rows the way the Record does (a global ascending id, a room,
a resolved `person_id`, Nora's own rows, hidden rows) and answers the same
counts and reads with the same filters: a fake that returned every row for
every person would pass a test that the real query fails.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from nora.domain.culture import Episode, RoomMessage
from nora.domain.models import DeliveryStatus, TranscriptRow


@dataclass(frozen=True)
class _Message:
    id: int
    platform: str
    room: str
    person_id: str | None
    body: str
    ts: int
    is_nora: bool
    hidden: bool


class InMemoryCharacterEvidence:
    def __init__(self) -> None:
        self._messages: list[_Message] = []
        #: Culture revisions, as (room key, written_at), in write order.
        self._revisions: list[tuple[str, int]] = []
        self._episodes: list[tuple[str, Episode, bool]] = []

    def add_episode(self, *, unit: str, episode: Episode, open_: bool = False) -> None:
        """One `response` episode of the current build; `open_` is one whose
        window has not closed (never counted, never read)."""
        self._episodes.append((unit, episode, open_))

    def _eps(self, unit: str, after: int) -> list[Episode]:
        return sorted(
            (e for u, e, o in self._episodes if u == unit and not o
             and e.outcome not in ("open", "unknown") and e.send_id > after),
            key=lambda e: e.send_id,
        )

    async def episode_count(self, *, unit: str, after: int) -> int:
        return len(self._eps(unit, after))

    async def episodes(
        self, *, unit: str, after: int, limit: int,
    ) -> tuple[list[Episode], int]:
        found = self._eps(unit, after)
        cursor = found[-1].send_id if found else after
        return (found[-limit:] if limit > 0 else []), cursor

    def add_revision(self, *, key: str, written_at: int) -> None:
        self._revisions.append((key, written_at))

    def _of(self, keys: Sequence[str]) -> list[str]:
        wanted = set(keys)
        return [k for k, _at in sorted(
            (r for r in self._revisions if r[0] in wanted), key=lambda r: r[1],
        )]

    async def revision_count(self, *, keys: Sequence[str]) -> int:
        return len(self._of(keys))

    async def revisions_after(
        self, *, keys: Sequence[str], after: int, limit: int,
    ) -> tuple[list[str], int]:
        found = self._of(keys)
        total = len(found)
        if limit <= 0 or total <= after:
            return [], max(total, after)
        return found[max(after, total - limit):], total

    def add_message(
        self, *, id: int, platform: str, room: str, person_id: str | None,
        body: str = "a line", ts: int = 0, is_nora: bool = False,
        hidden: bool = False,
    ) -> None:
        self._messages.append(_Message(
            id=id, platform=platform, room=room, person_id=person_id, body=body,
            ts=ts, is_nora=is_nora, hidden=hidden,
        ))

    def _person(
        self, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> list[_Message]:
        wanted = set(rooms)
        return sorted(
            (m for m in self._messages
             if (m.platform, m.room) in wanted and m.person_id == person_id
             and not m.is_nora and not m.hidden and m.id > after),
            key=lambda m: m.id,
        )

    async def message_head(self) -> int:
        return max((m.id for m in self._messages), default=0)

    async def person_count(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> int:
        return len(self._person(rooms, person_id, after))

    async def person_since(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
        limit: int,
    ) -> tuple[list[RoomMessage], int]:
        if limit <= 0:
            return [], after
        found = self._person(rooms, person_id, after)
        cursor = found[-1].id if found else after
        return [
            RoomMessage(room=m.room, row=replace(_ROW, id=m.id, ts=m.ts, body=m.body,
                                                 person_id=m.person_id))
            for m in found[-limit:]
        ], cursor


_ROW = TranscriptRow(
    id=0, sender_id="sender", sender_name=None, ts=0, body=None, is_nora=False,
    delivery_status=DeliveryStatus.SENT, raw_json=None,
)
