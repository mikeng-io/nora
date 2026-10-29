"""Shared deterministic admission; persistence commits the resulting state atomically."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, get_args

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
    validate_relation,
)
from nora.domain.memory_index import IndexedNode, in_scope, narrowed

GraphBatchOutcome = Literal["ok", "nothing_found", "unavailable"]
GRAPH_BATCH_OUTCOMES: tuple[str, ...] = get_args(GraphBatchOutcome)
GraphProjectionOutcome = Literal["excluded"]
GRAPH_PROJECTION_OUTCOMES: tuple[str, ...] = get_args(GraphProjectionOutcome)


class AdmissionState:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.relations: dict[str, Relation] = {}
        self.evidence: list[Evidence] = []
        self.available = True
        self.indexed: dict[Key, IndexedNode] = {}
        self.fields: dict[Key, Mapping[str, object]] = {}

    async def _upsert_entity(self, entity: Entity) -> Entity:
        if entity.kind not in ENTITY_KINDS:
            raise ValueError("unknown entity kind")
        held = next((e for e in self.entities.values() if e.id == entity.id), None)
        if held is not None and (held.unit != entity.unit or held.kind != entity.kind):
            # An entity never changes unit; a second unit gets its own entity.
            raise ValueError("an entity's unit is fixed")
        self.entities[entity.id] = entity
        return entity

    async def _admit(
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
                candidates[entity.id] = entity
        accepted: list[Relation] = []
        for relation in relations:
            ev = tuple(e for e in evidence if e.relation_id == relation.id)
            reason = validate_relation(relation, ev, sources)
            if relation.id in self.relations or any(r.id == relation.id for r in accepted):
                reason = "duplicate_relation"
            if reason is None:
                for key in (relation.src, relation.dst):
                    if key[0] in ENTITY_KINDS and (
                        key[1] not in candidates
                        or candidates[key[1]].unit != relation.unit
                        or candidates[key[1]].kind != key[0]
                    ):
                        reason = "wrong_ends"
            if reason:
                refused.append(Refusal("relation", relation.id, reason))
            else:
                accepted.append(relation)
        endpoints = {k for r in accepted for k in (r.src, r.dst)}
        committed = []
        for entity in entities:
            if candidates.get(entity.id) != entity:
                continue
            if entity.key not in endpoints:
                refused.append(Refusal("entity", entity.id, "orphan_entity"))
            else:
                committed.append(await self._upsert_entity(entity))
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
            held = before.get(entity.id)
            attrs = dict(held.attributes) if held is not None else {}
            for attribute, value in entity.attributes.items():
                if any(
                    e.entity_id == entity.id and e.attribute == attribute for e in admitted_evidence
                ):
                    attrs[attribute] = value
                else:
                    refused.append(Refusal("entity", entity.id, "missing_evidence"))
            cleaned = replace(entity, attributes=attrs)
            self.entities[entity.id] = cleaned
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

    async def _walk(self, seeds: tuple[Key, ...], query: Walk) -> Subgraph:
        if not self.available:
            return Subgraph("unavailable")

        def scoped(r: Relation) -> bool:
            scope = query.scope
            if scope is None:
                return True
            only = scope.only
            at_ms = int(r.asserted_at.timestamp() * 1000)
            if only and (
                (only.since_ms is not None and at_ms < only.since_ms)
                or (only.until_ms is not None and at_ms > only.until_ms)
            ):
                return False
            subjects = set(r.persons)
            for key in (r.src, r.dst):
                entity = self.entities.get(key[1])
                if entity and entity.kind == "person":
                    subjects.add(str(entity.attributes.get("ref", entity.id)))
            if (r.location or r.rel in {"stayed_at", "co_located_with", "during"}) and (
                not subjects <= scope.presence_persons
            ):
                return False
            keys = [
                r.src,
                r.dst,
                *(
                    e.source
                    for e in self.evidence
                    if e.relation_id == r.id or e.entity_id in {r.src[1], r.dst[1]}
                ),
            ]
            if (
                only
                and only.person
                and all(
                    key[1] in self.entities and self.entities[key[1]].key == key for key in keys
                )
                and only.person not in subjects
            ):
                return False
            for key in keys:
                entity = self.entities.get(key[1])
                if entity and entity.key == key:
                    if entity.unit != query.unit:
                        return False
                    if (
                        entity.kind == "person"
                        and str(entity.attributes.get("ref", entity.id)) not in scope.listed_here
                    ):
                        return False
                    continue
                node = self.indexed.get(key)
                if node is None or not in_scope(node, scope) or not narrowed(node, scope):
                    return False
                # Evidence about a person only counts in a room that lists them.
                if node.origin_room and not subjects <= scope.room_persons.get(
                    node.origin_room, frozenset()
                ):
                    return False
            return True

        eligible = [
            r
            for r in self.relations.values()
            if r.unit == query.unit
            and scoped(r)
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
            if r.layer != "record" or r.src in seeds:
                adjacency[r.src].append(r)
                adjacency[r.dst].append(r)
        reachable = {k for r in eligible for k in (r.src, r.dst)}
        nodes: dict[Key, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        partial = False

        def add(key: Key) -> bool:
            nonlocal partial
            entity = self.entities.get(key[1])
            if key in nodes:
                return True
            if key not in reachable or (entity and entity.unit != query.unit):
                return False
            if len(nodes) >= query.nodes:
                partial = True
                return False
            nodes[key] = GraphNode(
                key,
                entity,
                sum(e.entity_id == key[1] for e in self.evidence) if entity else 0,
                self.indexed.get(key),
                self.fields.get(key, {}),
            )
            return True

        frontier = [k for k in dict.fromkeys(seeds) if add(k)]
        if query.depth == 0 and query.scope is not None:
            incident = [r for r in eligible if r.src in nodes or r.dst in nodes]
            for r in incident[: query.fan_out]:
                evidence = tuple(e for e in self.evidence if e.relation_id == r.id)
                edges[r.id] = GraphEdge(
                    r,
                    len(evidence),
                    evidence,
                    tuple(self.indexed[e.source] for e in evidence if e.source in self.indexed),
                )
            partial = len(incident) > query.fan_out
            return Subgraph(
                "partial" if partial else "ok", tuple(nodes.values()), tuple(edges.values())
            )
        for depth in range(query.depth + 1):
            following = []
            for key in frontier:
                if (query.scope is not None and depth > 0
                        and key[0] in {"person", "room", "unit", "nora", "place"}):
                    continue
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
                            r,
                            sum(e.relation_id == r.id for e in self.evidence),
                            tuple(e for e in self.evidence if e.relation_id == r.id),
                            tuple(
                                self.indexed[e.source]
                                for e in self.evidence
                                if e.relation_id == r.id and e.source in self.indexed
                            ),
                        )
                        if not seen:
                            following.append(other)
            frontier = following
        return Subgraph(
            "partial" if partial else "ok", tuple(nodes.values()), tuple(edges.values())
        )
