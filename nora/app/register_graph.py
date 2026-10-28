"""Register first, links next; a retained work item owns projection retries."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from nora.domain.memory_graph import (
    Correction,
    Entity,
    Evidence,
    Key,
    MemoryGraph,
    Relation,
    Source,
    Walk,
    WorkItem,
)
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)


class RegisterGraphLinks:
    def __init__(self, *, graph: MemoryGraph, records: Any, metrics: Any = None) -> None:
        self.graph, self.records = graph, records
        self.metrics = metrics if metrics is not None else Metrics()

    async def follow(
        self,
        *,
        kind: str,
        ref: str,
        unit: str,
        session_id: int,
        body: str,
        at: datetime,
        shown_sources: Mapping[Key, Source] | None = None,
        version: str = "",
    ) -> None:
        source = (kind, ref)
        digest = hashlib.sha256(json.dumps([body, version]).encode()).hexdigest()
        work = await self.graph.enqueue(
            WorkItem(
                str(uuid5(NAMESPACE_URL, json.dumps([unit, source, digest]))),
                source,
                unit,
                digest,
                at,
            )
        )
        # Enqueue BEFORE attempting links. An unavailable graph leaves an owed
        # projection; no Register row is rolled back and no item is settled here.
        try:
            item = await self.records.load(work)
            aliases = await self.graph.aliases(unit=unit)
            targets = list(
                dict.fromkeys(
                    k for a, k in aliases.items() if a and a.casefold() in body.casefold()
                )
            )
            entities: tuple[Entity, ...] = ()
            verb = "settles" if kind == "memo" else "concerns"
            if kind == "event_entry":
                entity = Entity(f"{unit}:event:{ref}", "event", unit, body)
                targets, entities, verb = [entity.key], (entity,), "records"
            cited = {s.key for s in item.sources if s.layer in ("record", "rollup")}
            sources = {key: s for key, s in (shown_sources or {}).items() if key in cited}
            current = await self.graph.walk(
                (source,),
                Walk(
                    unit=unit,
                    persons=frozenset(p for s in sources.values() for p in s.persons),
                    depth=1,
                    nodes=100,
                    fan_out=20,
                    relations=(verb,),
                ),
            )
            if current.status == "unavailable":
                raise RuntimeError("Register links unavailable")
            existing = {
                e.relation.dst: e.relation
                for e in current.relations
                if e.relation.src == source and e.relation.rel == verb
            }
            corrections = tuple(
                Correction(r.id, "close", at, evidence=tuple(sources))
                for target, r in existing.items()
                if target not in targets
            )
            relations: list[Relation] = []
            evidence: list[Evidence] = []
            for target in targets:
                if target in existing:
                    continue
                rid = str(uuid5(NAMESPACE_URL, f"{work.id}:{verb}:{target}"))
                relations.append(
                    Relation(
                        rid,
                        source,
                        verb,
                        target,
                        "notebook",
                        unit,
                        tuple(sorted({p for s in sources.values() for p in s.persons})),
                        asserted_at=at,
                        written_by=f"cognition:register:{session_id}",
                    )
                )
                evidence.extend(Evidence(s.key, relation_id=rid) for s in sources.values())
            if entities and not relations:
                for entity in entities:
                    await self.graph.upsert_entity(entity)
                entities = ()
            result = await self.graph.stage_and_admit(
                entities=entities,
                relations=tuple(relations),
                evidence=tuple(evidence),
                sources=sources,
                corrections=corrections,
            )
            await self.records.graph_write(work, session_id, result)
            for refusal in result.refused:
                await self.records.record_refusal(session_id, refusal.kind, refusal.reason)
                self.metrics.inc(
                    "nora_comprehend_refused_parts_total", part=refusal.kind, reason=refusal.reason
                )
        except Exception:  # noqa: BLE001 - the retained work item retries this projection
            log.warning("Register links unavailable; work item retained")
