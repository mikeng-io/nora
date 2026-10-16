"""`InMemoryCharacterEvidence` — the double for
`nora.infra.sql.character.SqlCharacterEvidence`, bound to it by
`tests/contract/test_character_evidence_contract.py` so the two cannot drift.

It holds message rows the way the Record does (a global ascending id, a room,
a resolved `person_id`, Nora's own rows, hidden rows) and answers the same
counts and reads with the same filters: a fake that returned every row for
every person would pass a test that the real query fails.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from nora.domain.culture import Episode, RoomMessage
from nora.domain.lessons import NAMES_HER
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
    quoted_ts: int | None = None
    mentions: frozenset[str] = frozenset()


class InMemoryCharacterEvidence:
    def __init__(self) -> None:
        self._messages: list[_Message] = []
        #: Culture revisions, as (room key, written_at), in write order.
        self._revisions: list[tuple[str, int]] = []
        self._episodes: list[tuple[str, Episode, bool]] = []
        self._last_run: dict[tuple[str, str], int] = {}
        self._social_cursor: dict[tuple[str, str], int] = {}

    def add_episode(self, *, unit: str, episode: Episode, open_: bool = False) -> None:
        """One `response` episode of the current build; `open_` is one whose
        window has not closed (never counted, never read). With
        `evidence_only` (the default, Character · Nora) only an evidence episode
        (`Episode.is_evidence`) is counted or read, as in the SQL; without it
        (Lessons) every closed one is."""
        self._episodes.append((unit, episode, open_))

    def _eps(self, unit: str, after: int, evidence_only: bool) -> list[Episode]:
        return sorted(
            (e for u, e, o in self._episodes if u == unit and not o
             and e.outcome not in ("open", "unknown")
             and (e.is_evidence or not evidence_only)
             and e.send_id > after),
            key=lambda e: e.send_id,
        )

    async def episode_count(
        self, *, unit: str, after: int, evidence_only: bool = True,
    ) -> int:
        return len(self._eps(unit, after, evidence_only))

    async def episodes(
        self, *, unit: str, after: int, limit: int, evidence_only: bool = True,
    ) -> tuple[list[Episode], int]:
        found = self._eps(unit, after, evidence_only)
        cursor = found[-1].send_id if found else after
        return (found[-limit:] if limit > 0 else []), cursor

    async def addressed_since(
        self, *, rooms: Sequence[tuple[str, str]], since_ms: int, until_ms: int,
        limit: int, self_ids: frozenset[str] = frozenset(),
    ) -> list[RoomMessage]:
        """`SqlCharacterEvidence.addressed_since`: a human message in `rooms`
        in `(since_ms, until_ms]` that quotes one of her messages in the same
        room or names «nora»."""
        if limit <= 0 or until_ms <= since_ms:
            return []
        wanted = set(rooms)
        hers = {(m.platform, m.room, m.ts) for m in self._messages if m.is_nora}
        found = [
            m for m in sorted(self._messages, key=lambda m: m.id)
            if (m.platform, m.room) in wanted and not m.is_nora and not m.hidden
            and since_ms < m.ts <= until_ms
            and ((m.quoted_ts is not None and (m.platform, m.room, m.quoted_ts) in hers)
                 or re.search(NAMES_HER, m.body or "", re.IGNORECASE) is not None
                 or bool(m.mentions & self_ids))
        ]
        return [RoomMessage(room=m.room, row=self._row(m)) for m in found[-limit:]]

    async def social_cursor(self, *, adaptor: str, key: str) -> int:
        return self._social_cursor.get((adaptor, key), 0)

    async def advance_social_cursor(self, *, adaptor: str, key: str, cursor: int) -> None:
        held = self._social_cursor.get((adaptor, key), 0)
        self._social_cursor[(adaptor, key)] = max(held, cursor)

    def set_last_run(self, *, adaptor: str, key: str, at_ms: int) -> None:
        self._last_run[(adaptor, key)] = at_ms

    async def last_run_ms(self, *, adaptor: str, key: str) -> int | None:
        return self._last_run.get((adaptor, key))

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
        hidden: bool = False, quoted_ts: int | None = None,
        mentions: frozenset[str] = frozenset(),
    ) -> None:
        self._messages.append(_Message(
            id=id, platform=platform, room=room, person_id=person_id, body=body,
            ts=ts, is_nora=is_nora, hidden=hidden, quoted_ts=quoted_ts,
            mentions=mentions,
        ))

    @staticmethod
    def _row(m: _Message) -> TranscriptRow:
        return replace(_ROW, id=m.id, ts=m.ts, body=m.body, person_id=m.person_id)

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
