"""Contract implementation; phase 1 replaces storage, not these semantics."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

from nora.domain.memory_graph import (
    ENTITY_KINDS,
    Admission,
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
        entities: tuple[Entity, ...],
        relations: tuple[Relation, ...],
        evidence: tuple[Evidence, ...],
        sources: Mapping[Key, Source],
    ) -> Admission:
        refused: list[Refusal] = []
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
            attrs = {}
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
        for e in admitted_evidence:
            if e not in self.evidence:
                self.evidence.append(e)
        return Admission(
            tuple(committed), tuple(accepted), tuple(admitted_evidence), tuple(refused)
        )

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
