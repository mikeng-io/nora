"""In-memory implementation of the shared entry contract."""

from uuid import uuid4

from nora.domain.entries import Entry, EntryContent, EntryRevision, check_entry


class FakeEntryStore:
    def __init__(self) -> None:
        self.entries: dict[str, Entry] = {}
        self.history: dict[str, list[EntryRevision]] = {}

    async def create(self, *, kind: str, unit: str, content: EntryContent) -> Entry:
        check_entry(kind, content)
        if not unit.strip():
            raise ValueError("unit")
        eid = str(uuid4())
        head = EntryRevision(str(uuid4()), eid, None, 1, content)
        entry = Entry(eid, kind, unit, head)
        self.entries[eid] = entry
        self.history[eid] = [head]
        return entry

    async def get(self, *, unit: str, entry_id: str) -> Entry | None:
        entry = self.entries.get(entry_id)
        return entry if entry and entry.unit == unit else None

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
        head = EntryRevision(str(uuid4()), entry_id, base_revision_id, entry.head.seq + 1, content)
        self.history[entry_id].append(head)
        self.entries[entry_id] = Entry(entry_id, entry.kind, unit, head)
        return head
