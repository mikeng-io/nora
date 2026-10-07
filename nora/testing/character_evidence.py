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

from nora.domain.culture import RoomMessage
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
