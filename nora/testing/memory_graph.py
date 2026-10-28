"""Contract implementation; phase 1 replaces storage, not these semantics."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

from nora.domain.memory_admission import AdmissionState
from nora.domain.memory_graph import (
    Admission,
    Correction,
    Entity,
    Evidence,
    Key,
    Relation,
    Source,
    Subgraph,
    Walk,
    WorkItem,
)


class FakeMemoryGraph(AdmissionState):
    def __init__(self) -> None:
        self.entities: dict[Key, Entity] = {}
        self.relations: dict[str, Relation] = {}
        self.evidence: list[Evidence] = []
        self.work: dict[str, WorkItem] = {}
        self.available = True

    async def upsert_entity(self, entity: Entity) -> Entity:
        return await self._upsert_entity(entity)

    async def stage_and_admit(
        self,
        *,
        entities: tuple[Entity, ...],
        relations: tuple[Relation, ...],
        evidence: tuple[Evidence, ...],
        sources: Mapping[Key, Source],
        corrections: tuple[Correction, ...] = (),
    ) -> Admission:
        return await self._admit(
            entities=entities,
            relations=relations,
            evidence=evidence,
            sources=sources,
            corrections=corrections,
        )

    async def walk(self, seeds: tuple[Key, ...], query: Walk) -> Subgraph:
        return await self._walk(seeds, query)

    async def aliases(self, *, unit: str) -> Mapping[str, Key]:
        return {
            name.casefold(): e.key
            for e in self.entities.values()
            if e.unit == unit and e.state == "live" and e.merged_into is None
            for name in (e.name, *e.aliases)
        }

    async def candidates(
        self,
        *,
        unit: str,
        kind: str,
        name: str,
        near: Key | None = None,
        window: tuple[datetime, datetime] | None = None,
        limit: int = 5,
    ) -> tuple[Entity, ...]:
        """Substring stands in for SQL pg_trgm similarity on names and aliases.

        Place attributes use location=(latitude, longitude); event start/end
        are datetimes. Proximity and window overlap break lexical ties.
        """
        anchor = self.entities.get(near) if near else None
        location = anchor.attributes.get("location") if anchor and anchor.unit == unit else None

        def rank(e: Entity) -> tuple:
            names = tuple(n.casefold() for n in (e.name, *e.aliases))
            exact = name.casefold() in names
            fuzzy = any(name.casefold() in n for n in names)
            distance = float("inf")
            point = e.attributes.get("location")
            if (
                kind == "place"
                and isinstance(location, (tuple, list))
                and isinstance(point, (tuple, list))
            ):
                distance = sum(
                    (float(a) - float(b)) ** 2 for a, b in zip(location, point, strict=True)
                )
            overlap = False
            start, end = e.attributes.get("start"), e.attributes.get("end")
            if (
                kind == "event"
                and window
                and isinstance(start, datetime)
                and isinstance(end, datetime)
            ):
                overlap = start <= window[1] and end >= window[0]
            return (not exact, not fuzzy, distance, not overlap, e.id)

        matches = [
            e
            for e in self.entities.values()
            if e.unit == unit
            and e.kind == kind
            and e.state == "live"
            and e.merged_into is None
            and (
                any(name.casefold() in n.casefold() for n in (e.name, *e.aliases))
                or (kind == "place" and location is not None)
                or (kind == "event" and window is not None)
            )
        ]
        return tuple(sorted(matches, key=rank)[: max(0, min(limit, 5))])

    async def requeue_failed(self, *, unit: str | None = None, now: datetime) -> int:
        count = 0
        for key, item in self.work.items():
            if item.state == "failed" and (unit is None or item.unit == unit):
                self.work[key] = replace(
                    item, state="pending", attempts=0, last_error=None, due_at=now, updated_at=now
                )
                count += 1
        return count

    async def enqueue(self, item: WorkItem) -> WorkItem:
        existing = next(
            (
                w
                for w in self.work.values()
                if (w.source, w.unit, w.input_hash) == (item.source, item.unit, item.input_hash)
            ),
            None,
        )
        if existing:
            return existing
        if item.id in self.work:
            raise ValueError("work id already exists")
        self.work[item.id] = replace(item, state="pending", attempts=0, last_error=None)
        return self.work[item.id]

    async def claim(
        self, *, historical: bool, now: datetime, lease_seconds: float
    ) -> WorkItem | None:
        if lease_seconds <= 0:
            raise ValueError("positive lease required")
        for w in sorted(self.work.values(), key=lambda w: (w.as_of, w.id)):
            if (
                w.historical != historical
                or w.state not in ("pending", "running")
                or (w.due_at and w.due_at > now)
            ):
                continue
            if w.attempts >= 5:
                self.work[w.id] = replace(w, state="failed", updated_at=now)
                continue
            claimed = replace(
                w,
                state="running",
                attempts=w.attempts + 1,
                updated_at=now,
                due_at=now + timedelta(seconds=lease_seconds),
            )
            self.work[w.id] = claimed
            return claimed
        return None

    def _owns(self, item: WorkItem) -> bool:
        current = self.work.get(item.id)
        return bool(
            current
            and current.state == "running"
            and current.attempts == item.attempts
            and current.input_hash == item.input_hash
        )

    async def settle(self, item: WorkItem, *, now: datetime) -> bool:
        if not self._owns(item):
            return False
        self.work[item.id] = replace(
            self.work[item.id], state="settled", settled_at=now, updated_at=now
        )
        return True

    async def fail(self, item: WorkItem, *, error: str, now: datetime) -> bool:
        if not self._owns(item):
            return False
        self.work[item.id] = replace(
            self.work[item.id],
            state="failed" if item.attempts >= 5 else "pending",
            last_error=error,
            updated_at=now,
            due_at=now + timedelta(seconds=min(3600, 2**item.attempts)),
        )
        return True
