"""Contract implementation; phase 1 replaces storage, not these semantics."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

from nora.domain.memory_graph import (
    ENTITY_KINDS,
    Admission,
    Correction,
    Entity,
    Evidence,
    GraphEdge,
    GraphNode,
    Key,
    Refusal,
    Relation,
    Source,
    Subgraph,
    Walk,
    WorkItem,
    validate_relation,
)


class FakeMemoryGraph:
    def __init__(self) -> None:
        self.entities: dict[Key, Entity] = {}
        self.relations: dict[str, Relation] = {}
        self.evidence: list[Evidence] = []
        self.work: dict[str, WorkItem] = {}
        self.available = True

    async def upsert_entity(self, entity: Entity) -> Entity:
        if entity.kind not in ENTITY_KINDS:
            raise ValueError("unknown entity kind")
        self.entities[entity.key] = entity
        return entity

    async def stage_and_admit(
        self,
        *,
        entities: tuple[Entity, ...],
        relations: tuple[Relation, ...],
        evidence: tuple[Evidence, ...],
        sources: Mapping[Key, Source],
        corrections: tuple[Correction, ...] = (),
    ) -> Admission:
        refused: list[Refusal] = []
        before = dict(self.entities)
        existing_relations = set(self.relations)
        candidates = dict(self.entities)
        for entity in entities:
            if entity.kind not in ENTITY_KINDS:
                refused.append(Refusal("entity", entity.id, "vocabulary"))
            elif any(
                e.key != entity.key
                and e.kind == entity.kind
                and e.unit == entity.unit
                and entity.name in (e.name, *e.aliases)
                for e in candidates.values()
            ):
                refused.append(Refusal("entity", entity.id, "duplicate_entity"))
            else:
                candidates[entity.key] = entity
        accepted: list[Relation] = []
        for relation in relations:
            ev = tuple(e for e in evidence if e.relation_id == relation.id)
            reason = validate_relation(relation, ev, sources)
            if relation.id in self.relations or any(r.id == relation.id for r in accepted):
                reason = "duplicate_relation"
            if reason is None:
                for key in (relation.src, relation.dst):
                    if key[0] in ENTITY_KINDS and (
                        key not in candidates or candidates[key].unit != relation.unit
                    ):
                        reason = "wrong_ends"
            if reason:
                refused.append(Refusal("relation", relation.id, reason))
            else:
                accepted.append(relation)
        endpoints = {k for r in accepted for k in (r.src, r.dst)}
        committed = []
        for entity in entities:
            if candidates.get(entity.key) != entity:
                continue
            if entity.key not in endpoints:
                refused.append(Refusal("entity", entity.id, "orphan_entity"))
            else:
                committed.append(await self.upsert_entity(entity))
        ids = {r.id for r in accepted}
        admitted_evidence = []
        for e in evidence:
            if e.relation_id in ids:
                admitted_evidence.append(e)
            elif e.relation_id is not None:
                refused.append(Refusal("evidence", e.relation_id, "orphan_evidence"))
            elif e.entity_id is not None:
                target_entity = next((v for v in committed if v.id == e.entity_id), None)
                source = sources.get(e.source)
                reason = None
                if target_entity is None or e.attribute not in target_entity.attributes:
                    reason = "wrong_ends"
                elif source is None:
                    reason = "unseen_source"
                elif source.layer == "notebook":
                    reason = "notebook_evidence"
                elif e.span is not None and e.span not in source.text:
                    reason = "span_not_in_source"
                elif source.unit != target_entity.unit or source.persons:
                    reason = "scope_wider_than_evidence"
                if reason:
                    refused.append(Refusal("evidence", e.entity_id, reason))
                else:
                    admitted_evidence.append(e)
        # Attributes commit only with their own admitted evidence. The entity
        # endpoint may survive a refused attribute, just as valid relations do.
        clean_entities = []
        for entity in committed:
            # Attributes accumulate: a later batch adds to what earlier
            # admitted evidence established, it never erases it.
            held = before.get(entity.key)
            attrs = dict(held.attributes) if held is not None else {}
            for attribute, value in entity.attributes.items():
                if any(
                    e.entity_id == entity.id and e.attribute == attribute for e in admitted_evidence
                ):
                    attrs[attribute] = value
                else:
                    refused.append(Refusal("entity", entity.id, "missing_evidence"))
            cleaned = replace(entity, attributes=attrs)
            self.entities[entity.key] = cleaned
            clean_entities.append(cleaned)
        committed = clean_entities
        for r in accepted:
            self.relations[r.id] = r
        admitted_corrections = []
        for correction in corrections:
            target = (
                self.relations.get(correction.relation_id)
                if correction.relation_id in existing_relations
                else None
            )
            reason = None
            ev = tuple(
                Evidence(key, relation_id=correction.relation_id) for key in correction.evidence
            )
            if target is None:
                reason = "unknown_relation"
            elif target.status != "current":
                reason = "not_current"
            elif any(
                unit != target.unit
                for unit in [e.unit for e in entities] + [r.unit for r in relations]
            ):
                reason = "scope_wider_than_evidence"
            elif not ev:
                reason = "missing_evidence"
            elif correction.kind == "supersede" and correction.by not in ids:
                reason = "missing_replacement"
            elif (
                correction.kind == "supersede"
                and self.relations[correction.by or ""].unit != target.unit
            ):
                reason = "scope_wider_than_evidence"
            else:
                reason = validate_relation(target, ev, sources)
            if reason:
                refused.append(Refusal("correction", correction.relation_id, reason))
                continue
            if target is None:
                raise RuntimeError("admitted correction has no target")
            if correction.kind == "close":
                updated = replace(target, valid_to=correction.at)
            elif correction.kind == "supersede":
                updated = replace(target, status="superseded", superseded_by=correction.by)
            elif correction.kind == "dispute":
                updated = replace(target, status="disputed")
            else:
                updated = replace(target, status="retired")
            self.relations[target.id] = updated
            admitted_corrections.append(correction)
            admitted_evidence.extend(ev)
        for e in admitted_evidence:
            if e not in self.evidence:
                self.evidence.append(e)
        return Admission(
            tuple(committed),
            tuple(accepted),
            tuple(admitted_evidence),
            tuple(refused),
            tuple(admitted_corrections),
        )

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

    async def walk(self, seeds: tuple[Key, ...], query: Walk) -> Subgraph:
        if not self.available:
            return Subgraph("unavailable")
        eligible = [
            r
            for r in self.relations.values()
            if r.unit == query.unit
            and set(r.persons) <= query.persons
            and r.status == "current"
            and r.layer in query.layers
            and (not query.relations or r.rel in query.relations)
            and (
                query.valid_at is None
                or (
                    (r.valid_from is None or r.valid_from <= query.valid_at)
                    and (r.valid_to is None or query.valid_at < r.valid_to)
                )
            )
        ]
        # Record pointers are never adjacency, even when explicitly requested.
        adjacency: dict[Key, list[Relation]] = defaultdict(list)
        for r in eligible:
            if r.layer != "record":
                adjacency[r.src].append(r)
                adjacency[r.dst].append(r)
        reachable = {k for r in eligible for k in (r.src, r.dst)}
        nodes: dict[Key, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        partial = False

        def add(key: Key) -> bool:
            nonlocal partial
            entity = self.entities.get(key)
            if key in nodes:
                return True
            if key not in reachable or (entity and entity.unit != query.unit):
                return False
            if len(nodes) >= query.nodes:
                partial = True
                return False
            nodes[key] = GraphNode(
                key, entity, sum(e.entity_id == key[1] for e in self.evidence) if entity else 0
            )
            return True

        frontier = [k for k in dict.fromkeys(seeds) if add(k)]
        for depth in range(query.depth + 1):
            following = []
            for key in frontier:
                counts: dict[str, int] = defaultdict(int)
                for r in sorted(adjacency[key], key=lambda r: r.id):
                    if r.id in edges:
                        continue
                    if depth == query.depth or counts[r.rel] >= query.fan_out:
                        partial = True
                        continue
                    counts[r.rel] += 1
                    other = r.dst if r.src == key else r.src
                    seen = other in nodes
                    if add(other):
                        edges[r.id] = GraphEdge(
                            r, sum(e.relation_id == r.id for e in self.evidence)
                        )
                        if not seen:
                            following.append(other)
            frontier = following
        return Subgraph(
            "partial" if partial else "ok", tuple(nodes.values()), tuple(edges.values())
        )

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
