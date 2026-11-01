"""Register first, links next; a retained work item owns projection retries."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from nora.domain.memory_graph import (
    Correction,
    Entity,
    Evidence,
    Key,
    MemoryGraph,
    Relation,
    Walk,
)
from nora.obs.metrics import Metrics


class RegisterGraphLinks:
    def __init__(self, *, graph: MemoryGraph, records: Any, metrics: Any = None) -> None:
        self.graph, self.records = graph, records
        self.metrics = metrics if metrics is not None else Metrics()

    async def project(
        self, item: Any, session_id: int, *, selected: tuple[Key, ...] = ()
    ) -> None:
        """Link one Register row to its matters, per its own link verb.

        `selected` is the association the session admitted and named on the
        write: an evidence-grounded target the body need not spell out. Alias
        text remains only a discovery aid beside it.
        """
        work = item.work
        source, unit, at = work.source, work.unit, work.as_of
        kind, ref = source
        body = next(s.text for s in item.sources if s.key == source)
        aliases = await self.graph.aliases(unit=unit)
        targets = list(
            dict.fromkeys(
                k for a, k in aliases.items() if a and a.casefold() in body.casefold()
            )
        )
        entities: tuple[Entity, ...] = ()
        verb = "settles" if kind == "memo" else "concerns"
        wanted: dict[Key, str] = dict.fromkeys(dict.fromkeys([*targets, *selected]), verb)
        managed = {verb}
        if kind == "event_entry":
            entity = Entity(f"{unit}:event:{ref}", "event", unit, body)
            entities = (entity,)
            wanted = {entity.key: "records", **dict.fromkeys(selected, "concerns")}
            managed = {"records", "concerns"}
        sources = {s.key: s for s in item.sources if s.layer in ("record", "rollup")}
        current = await self.graph.walk(
            (source,),
            Walk(
                unit=unit,
                persons=frozenset(p for s in sources.values() for p in s.persons),
                depth=1,
                nodes=100,
                fan_out=20,
                # `managed`, not `wanted`: an empty wanted must still see the
                # edges it is here to close.
                relations=tuple(dict.fromkeys((*wanted.values(), *managed))),
            ),
        )
        if current.status != "ok":
            raise RuntimeError("Register links unavailable")
        # A valid_at-less walk reaches closed edges too: a target re-selected
        # after a close gets a new deterministic id for its generation, since
        # the closed row keeps the bare one.
        mine = [
            e.relation for e in current.relations
            if e.relation.src == source and e.relation.rel in managed
        ]
        existing = {
            (r.rel, r.dst): r
            for r in mine
            if (r.valid_from is None or r.valid_from <= at)
            and (r.valid_to is None or at < r.valid_to)
        }
        closed_counts: dict[tuple[str, Any], int] = {}
        for r in mine:
            if r.valid_to is not None:
                key = (r.rel, r.dst)
                closed_counts[key] = closed_counts.get(key, 0) + 1
        corrections = tuple(
            Correction(r.id, "close", at, evidence=tuple(sources))
            for (rel, target), r in existing.items()
            if wanted.get(target) != rel
        )
        relations: list[Relation] = []
        evidence: list[Evidence] = []
        for target, verb in wanted.items():
            if (verb, target) in existing:
                continue
            base = f"{work.id}:{verb}:{target}"
            prior = closed_counts.get((verb, target), 0)
            rid = str(uuid5(NAMESPACE_URL, base if prior == 0 else f"{base}:{prior}"))
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
        if result.refused:
            raise ValueError("Register link projection refused")
