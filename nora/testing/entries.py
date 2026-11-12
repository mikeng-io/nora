"""In-memory implementation of the shared entry contract."""

import time
from collections.abc import Callable, Mapping
from uuid import uuid4

from nora.domain.entries import Entry, EntryContent, EntryRevision, check_entry
from nora.domain.lineage import topic_revision_lineage


class FakeEntryStore:
    """Mirrors `SqlEntryStore`: check-then-write, store-derived lineage, and the
    one-live-subject-per-unit rule the unique index enforces for real.

    `rooms` is the fake's stand-in for the Record lookups the SQL store runs:
    each citation key to the room it rests on; an unmapped key is unplaceable
    and lands `unresolved` at no origin. `clock` supplies `written_ms`.
    """

    def __init__(
        self,
        *,
        rooms: Mapping[tuple[str, str], str] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.entries: dict[str, Entry] = {}
        self.history: dict[str, list[EntryRevision]] = {}
        self._rooms = dict(rooms or {})
        self._clock = clock or (lambda: int(time.time() * 1000))

    def _live(self, *, unit: str) -> dict[str, Entry]:
        return {
            eid: e
            for eid, e in self.entries.items()
            if e.unit == unit and e.head.content.state != "retired"
        }

    def _head(
        self, entry_id: str, base: str | None, seq: int, content: EntryContent
    ) -> EntryRevision:
        return EntryRevision(
            str(uuid4()), entry_id, base, seq, content,
            written_ms=self._clock(),
            lineage=topic_revision_lineage(
                cited=content.citations, rooms=self._rooms
            ),
        )

    async def create(self, *, kind: str, unit: str, content: EntryContent) -> Entry:
        check_entry(kind, content)
        if not unit.strip():
            raise ValueError("unit")
        subject = content.about[0][1].casefold()
        if any(
            e.head.content.about[0][1].casefold() == subject
            for e in self._live(unit=unit).values()
        ):
            raise ValueError("duplicate")
        eid = str(uuid4())
        head = self._head(eid, None, 1, content)
        entry = Entry(eid, kind, unit, head)
        self.entries[eid] = entry
        self.history[eid] = [head]
        return entry

    async def get(self, *, unit: str, entry_id: str) -> Entry | None:
        entry = self.entries.get(entry_id)
        return entry if entry and entry.unit == unit else None

    async def held(self, *, unit: str) -> tuple[Entry, ...]:
        return tuple(
            sorted(
                self._live(unit=unit).values(),
                key=lambda e: e.head.content.about[0][1].casefold(),
            )
        )

    async def revisions(self, *, unit: str, entry_id: str) -> tuple[EntryRevision, ...]:
        return (
            tuple(reversed(self.history[entry_id]))
            if await self.get(unit=unit, entry_id=entry_id)
            else ()
        )

    async def append(
        self, *, unit: str, entry_id: str, base_revision_id: str, content: EntryContent
    ) -> EntryRevision:
        entry = await self.get(unit=unit, entry_id=entry_id)
        if entry is None:
            raise LookupError(entry_id)
        if entry.head.id != base_revision_id:
            raise ValueError("stale_base")
        check_entry(entry.kind, content, entry.head.content)
        head = self._head(entry_id, base_revision_id, entry.head.seq + 1, content)
        self.history[entry_id].append(head)
        self.entries[entry_id] = Entry(entry_id, entry.kind, unit, head)
        return head
