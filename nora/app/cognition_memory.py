"""One wake's frozen graph input and in-call admission. No model driver."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from nora.app.comprehend import GraphWrite, MemoryProposalAdmission, ProfileProposal, _shape_errors
from nora.domain.comprehend import ComprehendItem, Preview
from nora.domain.extraction import partition_claims
from nora.domain.memory_graph import Source, Walk, WorkItem
from nora.language.comprehend_render import preview_text


class WakeMemory:
    def __init__(self, *, graph: Any, records: Any, metrics: Any,
                 private_names: tuple[str, ...] = ()) -> None:
        self.private_names = private_names
        self.graph, self.records = graph, records
        self.admission = MemoryProposalAdmission(graph=graph, records=records, metrics=metrics)

    async def act_key(self, state: Any, name: str, arguments: Any) -> str:
        payload = json.dumps([state.unit, sorted(v.work.id for v in state.memory_items.values()),
                              name, arguments], sort_keys=True, default=str)
        return "act:" + hashlib.sha256(payload.encode()).hexdigest()

    async def link_result(self, state: Any, result: Any) -> None:
        from nora.app.register_graph import RegisterGraphLinks
        for field, kind in (("note_id", "note"), ("memo_id", "memo"), ("event_id", "event_entry")):
            if field not in result:
                continue
            base = next(iter(state.memory_items.values())).work
            from dataclasses import replace
            work = replace(base, id=f"register:{kind}:{result[field]}",
                           source=(kind, str(result[field])))
            item = await self.records.load(work)
            await RegisterGraphLinks(graph=self.graph, records=self.records).project(
                item, state.session_id)

    async def parked(self, unit: str, pending: Any) -> set[int]:
        return {s.id for s in pending if await self.records.failures(unit, s.id) >= 5}

    async def failed(self, unit: str, ids: Any, session: int | None) -> None:
        for sid in ids:
            await self.records.fail_stimulus(unit, sid, session)

    async def prepare(self, state: Any, stimuli: Any, grounding: Any) -> None:
        """Freeze exactly what the agenda shows, including each non-message source."""
        at = datetime.fromtimestamp(state.now_ms / 1000, UTC)
        for index, agenda in state.items_by_index.items():
            sources = []
            rows = []
            for sid in agenda.stimulus_ids:
                stimulus = stimuli[sid]
                row = grounding.records.get(stimulus.ref)
                if row is not None and row.text is not None:
                    sources.append(Source(("message", str(row.message_id)), "record", state.unit,
                                          (row.person_id,) if row.person_id else (), row.text))
                    rows.append({"id": row.message_id, "body": row.text, "ts": row.at_ms,
                                 "person_id": row.person_id, "mentions": []})
                elif stimulus.kind in ("social", "play"):
                    kind, _, ref = stimulus.ref.partition(":")
                    work = WorkItem(f"stimulus:{sid}", (kind, ref), state.unit, "", at)
                    loaded = await self.records.load(work)
                    sources.extend(loaded.sources)
                    rows.extend(loaded.snapshot.get("messages", []))
                else:
                    line = grounding.rollups.get(stimulus.ref)
                    text = json.dumps(asdict(line), default=str) if line else stimulus.ref
                    sources.append(Source((stimulus.kind, str(sid)), "rollup", state.unit,
                        (stimulus.person_id,) if stimulus.person_id else (), text))
            digest = hashlib.sha256(json.dumps([asdict(s) for s in sources],
                                              sort_keys=True).encode()).hexdigest()
            work = WorkItem(f"stimuli:{','.join(map(str, agenda.stimulus_ids))}",
                            ("stimulus", str(agenda.stimulus_ids)), state.unit, digest, at,
                            historical=state.historical)
            item = ComprehendItem(work, tuple(sources), agenda.room.platform,
                                  agenda.room.conversation_id,
                                  snapshot={"messages": rows, "context": [],
                                            "source_keys": {str(r["id"]): ["message", str(r["id"])]
                                                            for r in rows}})
            state.memory_items[index] = item
            for source in sources:
                state.graph_fetched.show(source)
        persons = frozenset(p for s in state.graph_fetched.sources.values() for p in s.persons)
        seeds = tuple(state.graph_fetched.sources)
        aliases = await self.graph.aliases(unit=state.unit)
        text = " ".join(s.text.casefold() for s in state.graph_fetched.sources.values())
        seeds = tuple(dict.fromkeys((*seeds, *(k for a, k in aliases.items()
                                              if a and a.casefold() in text))))
        graph = await self.graph.walk(seeds, Walk(unit=state.unit, persons=persons, depth=2,
                                                valid_at=at))
        if graph.status == "unavailable":
            raise RuntimeError("wake graph unavailable")
        state.graph_text = preview_text(Preview(seeds, graph, (), (at, at)))
        state.graph_text += "\nreading_sources=" + json.dumps(
            {i: v.snapshot for i, v in state.memory_items.items()}, default=str)

    async def propose(
        self, state: Any, *, items: list[int], payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not items or any(i not in state.items_by_index for i in items):
            return {"status": "refused", "admitted": [], "refused": [{"reason": "no_such_item"}]}
        try:
            output = GraphWrite.model_validate(payload)
        except ValidationError as error:
            return {"status": "refused", "admitted": [],
                    "refused": [{"reason": "schema", "errors": _shape_errors(error)}]}
        if any(e.kind in ("person", "room", "unit", "nora") for e in output.entities):
            return {"status": "refused", "admitted": [],
                    "refused": [{"reason": "identity_pointer"}]}
        admitted: list[int] = []
        refused: list[dict[str, Any]] = []
        # Each proposal has one evidence scope. A multi-item proposal is validated
        # against their union, then consumes only the explicitly named items.
        inputs = [state.memory_items[i] for i in items]
        base = inputs[0]
        from dataclasses import replace
        combined = replace(base, sources=tuple(s for v in inputs for s in v.sources), snapshot={
            "messages": [r for v in inputs for r in v.snapshot["messages"]], "context": [],
            "source_keys": {k: source for v in inputs
                            for k, source in v.snapshot.get("source_keys", {}).items()}})
        claims, drops = partition_claims({"claims": [r.model_dump() for r in output.readings]},
                                         combined.snapshot)
        if drops:
            return {"status": "refused", "admitted": [],
                    "refused": [{"reason": r} for r in drops]}
        result, local_refusals = await self.admission._admit(
            combined.work, output, state.graph_fetched, state.session_id)
        refused = [*[asdict(r) for r in result.refused], *local_refusals]
        if not refused:
            await self.records.readings(combined.work, state.session_id, combined, claims)
            admitted = items
        seeds = tuple(dict.fromkeys(k for r in result.relations for k in (r.src, r.dst)))
        graph = await self.graph.walk(seeds, Walk(unit=state.unit, depth=1,
            persons=frozenset(p for s in combined.sources for p in s.persons),
            valid_at=combined.work.as_of))
        if graph.status == "unavailable":
            raise RuntimeError("connected graph unavailable")
        state.admitted_indices.update(admitted)
        state.admitted_relations.update({r.id: r for r in result.relations})
        state.admitted_evidence.extend(result.evidence)
        return {"status": "ok" if admitted else "refused", "admitted": admitted,
                "refused": refused, "connected": json.loads(json.dumps(asdict(graph), default=str)),
                "note": "Read the connected state before deciding whether to act."}

    async def profile(
        self, state: Any, entity: list[str], proposals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        parsed = [ProfileProposal.model_validate(p) for p in proposals]
        shown = state.graph_fetched.sources
        supports = {(e.relation_id, e.source) for e in state.admitted_evidence}
        if any(not set(p.relation_ids) <= set(state.admitted_relations)
               or any(tuple(k) not in shown for k in p.evidence)
               or any(not any((r, tuple(k)) in supports for k in p.evidence)
                      for r in p.relation_ids) for p in parsed):
            return {"status": "refused", "reason": "ungrounded"}
        item = next(iter(state.memory_items.values()))
        await self.records.shadow(item.work, state.session_id, tuple(entity),
                                  [p.model_dump() for p in parsed], state.session_id)
        return {"status": "ok", "note": "Profile proposals recorded in shadow."}
