"""The typed before/after state read, and its diff.

Every kind is read through the store's own production read path over the
sandbox engine — `SqlNoteStore.standing`, `SqlMemoStore.kept`,
`SqlGatheringStore.list_for_conversation`, `SqlReminderStore.all_pending`,
`SqlStandingStore.read_head`, `SqlStandingLedger.read`,
`SqlLessonStore.lessons`, `SqlProfileStore.account_heads`,
`SqlMemoryGraph.aliases`, the graph's full relation set through
`unit_relations`, and the stimulus store's `view`
plus `pending_self_wakes` — never an eval-side SQL projection. Entries
key by the store's own id; the diff classifies created / revised
(before and after) / retired-or-closed / unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.cognition_store import SqlCognitionStore
from nora.infra.sql.entries import SqlEntryStore
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.memory_relations import unit_relations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.standing_ledger import SqlStandingLedger
from nora.infra.sql.stimuli import SqlStimulusStore

KINDS = (
    "graph", "profile", "note", "memo", "event", "topic",
    "lesson", "standing", "reminder", "wake",
)


@dataclass(frozen=True, slots=True)
class UnitState:
    """`kind -> entry_id -> payload`, one instant of one unit's stores."""

    kinds: Mapping[str, Mapping[str, Mapping[str, Any]]]

    def entries(self, kind: str) -> Mapping[str, Mapping[str, Any]]:
        return self.kinds.get(kind, {})

    def entry_ids(self) -> frozenset[str]:
        return frozenset(
            f"{kind}:{eid}" for kind, entries in self.kinds.items() for eid in entries
        )


@dataclass(frozen=True, slots=True)
class Entry:
    status: str  # created | revised | retired | unchanged
    entry_id: str
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class KindDiff:
    kind: str
    created: tuple[Entry, ...] = ()
    revised: tuple[Entry, ...] = ()
    retired: tuple[Entry, ...] = ()
    unchanged: tuple[Entry, ...] = ()

    def entry_ids(self) -> frozenset[str]:
        return frozenset(
            f"{self.kind}:{e.entry_id}"
            for e in (*self.created, *self.revised, *self.retired, *self.unchanged)
        )


@dataclass(frozen=True, slots=True)
class StateDiff:
    kinds: Mapping[str, KindDiff]

    def entry_ids(self) -> frozenset[str]:
        return frozenset().union(*(d.entry_ids() for d in self.kinds.values()))

    def changed(self, kind: str) -> bool:
        d = self.kinds.get(kind)
        return bool(d and (d.created or d.revised or d.retired))


def _payload(obj: Any) -> Mapping[str, Any]:
    out = dict(vars(obj)) if hasattr(obj, "__dict__") else {
        k: getattr(obj, k) for k in getattr(obj, "__dataclass_fields__", {})
    }
    return {k: str(v) for k, v in sorted(out.items())}


def reminder_in_rooms(reminder: Any, rooms: Sequence[tuple[str, str]]) -> bool:
    """A reminder is in scope only through one of the unit's own rooms —
    `all_pending` is global, and a reminder carrying no unit key must
    never read as another unit's."""
    return (reminder.platform, reminder.conversation_id) in set(rooms)


async def snapshot(
    engine: AsyncEngine, *, unit: str,
    rooms: Sequence[tuple[str, str]],
    profile_keys: Iterable[str],
    now_ms: int,
    metrics: Any = None,
) -> UnitState:
    """One instant's read of every owned store for `unit`."""
    kinds: dict[str, Mapping[str, Mapping[str, Any]]] = {k: {} for k in KINDS}

    notes = await SqlNoteStore(engine).standing(unit=unit)
    kinds["note"] = {str(n.id): _payload(n) for n in notes}

    memos = await SqlMemoStore(engine).kept(unit=unit)
    kinds["memo"] = {str(m.id): _payload(m) for m in memos}

    events: dict[str, Mapping[str, Any]] = {}
    event_store = SqlGatheringStore(engine)
    for platform, cid in rooms:
        for e in await event_store.list_for_conversation(
            conversation_id=cid, status=None, unit_id=unit, platform=platform,
        ):
            events[str(e.id)] = _payload(e)
    kinds["event"] = events

    reminders = SqlReminderStore(engine)
    kinds["reminder"] = {
        str(r.id): _payload(r)
        for r in await reminders.all_pending(limit=10000)
        if reminder_in_rooms(r, rooms)
    }

    standing_store = SqlStandingStore(engine)
    standing: dict[str, Mapping[str, Any]] = {}
    for platform, cid in rooms:
        head = await standing_store.read_head(platform=platform, conversation_id=cid)
        if head is not None:
            standing[f"{platform}:{cid}"] = _payload(head)
    kinds["standing"] = standing

    ledger = SqlStandingLedger(engine, metrics=metrics)
    topics: dict[str, Mapping[str, Any]] = {}
    for platform, cid in rooms:
        for entry in await ledger.read(unit=unit, platform=platform, conversation_id=cid):
            key = f"{entry.kind}:{getattr(entry, 'id', '')}:{platform}:{cid}"
            topics[key] = _payload(entry)
    kinds["topic"] = topics

    lessons = await SqlLessonStore(engine).lessons(unit=unit)
    kinds["lesson"] = {str(lesson.id): _payload(lesson) for lesson in lessons}

    profiles = SqlProfileStore(engine, screen=lambda _: None)
    profile: dict[str, Mapping[str, Any]] = {}
    for key in profile_keys:
        for line in await profiles.account_heads(key=key):
            profile[f"{key}:{line.id}"] = _payload(line)
    kinds["profile"] = profile

    graph = SqlMemoryGraph(engine)
    kinds["graph"] = {
        f"{r.rel}:{r.id}": _payload(r)
        for r in await unit_relations(engine, unit=unit)
    }
    anchors = {f"anchor:{n}": {"kind": k[0], "id": k[1]}
               for n, k in (await graph.aliases(unit=unit)).items()}
    notebook = {f"entry:{e.id}": _payload(e)
                for e in await SqlEntryStore(engine).held(unit=unit) if e.kind == "topic"}
    kinds["topic"] = {**topics, **anchors, **notebook}

    stimuli = SqlStimulusStore(engine)
    view = await stimuli.view(unit=unit, mode="live", now_ms=now_ms, limit=500)
    self_wakes = await SqlCognitionStore(engine).pending_self_wakes(
        unit=unit, since_ms=0,
    )
    kinds["wake"] = {
        f"stimulus:{s.id}": _payload(s) for s in view.pending
    } | {f"self:{w.wake_ref}": _payload(w) for w in self_wakes}

    return UnitState(kinds=kinds)


def diff(before: UnitState, after: UnitState) -> StateDiff:
    out: dict[str, KindDiff] = {}
    for kind in KINDS:
        old, new = dict(before.entries(kind)), dict(after.entries(kind))
        created, revised, retired, unchanged = [], [], [], []
        for entry_id, payload in sorted(new.items()):
            if entry_id not in old:
                created.append(Entry("created", entry_id, after=payload))
            elif old[entry_id] != payload:
                revised.append(Entry("revised", entry_id, before=old[entry_id], after=payload))
            else:
                unchanged.append(Entry("unchanged", entry_id, before=payload, after=payload))
        for entry_id, payload in sorted(old.items()):
            if entry_id not in new:
                retired.append(Entry("retired", entry_id, before=payload))
        out[kind] = KindDiff(kind, tuple(created), tuple(revised),
                             tuple(retired), tuple(unchanged))
    return StateDiff(kinds=out)
