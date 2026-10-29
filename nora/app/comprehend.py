"""Cognition steps one and two. Only the graph and Record/shadow rows are written."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nora.agent.comprehend_tools import READ_NAMES, ComprehendRead, LookupState
from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_session import AdaptorBudget, CognitionAgentRunner
from nora.app.extraction import ExtractionClaim
from nora.domain.comprehend import PARTS, REFUSAL_REASONS, ComprehendItem, build_preview
from nora.domain.extraction import partition_claims
from nora.domain.memory_graph import (
    PROPOSED_KINDS,
    Admission,
    Correction,
    Entity,
    Evidence,
    Key,
    MemoryGraph,
    Relation,
    Walk,
    WorkItem,
    pointer_entity,
)
from nora.domain.ports import CognitionStore, NewCognitionSession, NewCognitionSessionClose
from nora.language.comprehend_render import (
    COMPREHEND,
    RESHAPE,
    preview_text,
    render_comprehend,
    render_reshape,
)
from nora.obs.tracing import TRACE_KIND, span

log = logging.getLogger(__name__)


class Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProposedEntity(Shape):
    id: str
    kind: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProposedRelation(Shape):
    id: str
    src: list[str] = Field(min_length=2, max_length=2)
    rel: str
    dst: list[str] = Field(min_length=2, max_length=2)
    persons: list[str] = Field(default_factory=list)
    valid_from: str | None = None
    valid_to: str | None = None


class ProposedEvidence(Shape):
    source: list[str] = Field(min_length=2, max_length=2)
    relation_id: str | None = None
    entity_id: str | None = None
    attribute: str | None = None
    role: Literal["supports", "contradicts"] = "supports"
    span: str | None = None


class ProposedCorrection(Shape):
    relation_id: str
    kind: Literal["close", "supersede", "dispute", "retire"]
    at: str
    by: str | None = None
    evidence: list[list[str]] = Field(default_factory=list)


class GraphWrite(Shape):
    entities: list[ProposedEntity]
    relations: list[ProposedRelation]
    evidence: list[ProposedEvidence]
    corrections: list[ProposedCorrection]
    readings: list[ExtractionClaim]


class ProfileProposal(Shape):
    part: Literal["trait", "guess", "culture", "lesson", "topic"]
    text: str = Field(min_length=1)
    relation_ids: list[str] = Field(min_length=1)
    evidence: list[list[str]] = Field(min_length=1)


def _key(value: list[str]) -> Key:
    if len(value) != 2:
        raise ValueError("source key requires kind and id")
    return value[0], value[1]


def _time(value: str | None) -> datetime | None:
    if value is None:
        return None
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("time requires offset")
    return at


class ComprehendRunner:
    def __init__(
        self,
        *,
        graph: MemoryGraph,
        records: Any,
        sessions: CognitionStore,
        agent: CognitionAgentRunner,
        tools: Any,
        config: Any,
        graph_enabled: bool,
        metrics: Any,
        private_names: tuple[str, ...],
        model_id: str,
        model_route: str,
        ai_peer: str = "nora",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rooms_by_person: Mapping[tuple[str, str], tuple[str, ...]] | None = None,
    ) -> None:
        self.graph, self.records, self.sessions = graph, records, sessions
        self.agent, self.tools, self.config = agent, tools, config
        self.enabled = graph_enabled and config.enabled
        self.metrics, self.private_names = metrics, private_names
        self.model_id, self.model_route, self.ai_peer = model_id, model_route, ai_peer
        self.clock = clock
        # A Mapping, kept live: composition passes a LiveMapping over the
        # policy holder, so a channel a reload discovers is seen next read.
        self.rooms_by_person = rooms_by_person or {}
        self.budget = AdaptorBudget(
            turns=config.turns,
            total_tokens=config.total_tokens,
            output_tokens=config.output_tokens,
            timeout_seconds=config.timeout_seconds,
        )

    async def run_once(self, *, historical: bool = False) -> bool:
        if not self.enabled:
            return False
        item = await self.graph.claim(
            historical=historical,
            now=self.clock(),
            lease_seconds=self.config.timeout_seconds * 10 + 60,
        )
        if item is None:
            return False
        with span(
            "cognition:comprehend",
            **{
                TRACE_KIND: "generation",
                "nora.work_item": item.id,
                "nora.unit": item.unit,
                "nora.historical": historical,
                "nora.attempt": item.attempts,
            },
        ):
            try:
                # Cover input, model and persistence with the item's wall clock.
                async with asyncio.timeout(self.config.timeout_seconds):
                    await self._comprehend(item)
                if not await self.graph.settle(item, now=self.clock()):
                    raise RuntimeError("work item lease lost")
                self.metrics.inc(
                    "nora_comprehend_items_total",
                    outcome="settled",
                    lane="historical" if historical else "live",
                )
            except asyncio.CancelledError:
                # No deletion: the durable lease makes this item claimable again.
                raise
            except Exception:  # noqa: BLE001 - durable work is retained
                await self.graph.fail(item, error="comprehend_unavailable", now=self.clock())
                self.metrics.inc(
                    "nora_comprehend_items_total",
                    outcome="failed",
                    lane="historical" if historical else "live",
                )
                log.warning("comprehend attempt failed; work retained")
        return True

    async def run(self) -> None:
        """Independent worker pools: history can never consume a live slot."""

        async def lane(historical: bool) -> None:
            while True:
                try:
                    if not await self.run_once(historical=historical):
                        await asyncio.sleep(1)
                except Exception:  # noqa: BLE001 - queue failure must not stop the worker  # noqa: BLE001 - durable work is retained
                    log.warning("comprehend queue unavailable; retrying")
                    await asyncio.sleep(1)

        async with asyncio.TaskGroup() as group:
            for historical, concurrency in (
                (False, self.config.live_concurrency),
                (True, self.config.historical_concurrency),
            ):
                for _ in range(concurrency):
                    group.create_task(lane(historical))

    async def _session(self, item: WorkItem, step: str, prompt: str, instruction: str) -> int:
        now = int(self.clock().timestamp() * 1000)
        return await self.sessions.record_session(
            session=NewCognitionSession(
                unit=item.unit,
                mode="live",
                started_at_ms=now,
                from_stimulus_id=0,
                through_stimulus_id=0,
                situation={
                    "step": step,
                    "work_item": item.id,
                    "input_hash": item.input_hash,
                    "as_of": item.as_of.isoformat(),
                    "historical": item.historical,
                    "rendered_input": prompt,
                },
                agenda={"items": [list(item.source)]},
                doctrine_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
                model_id=self.model_id,
                model_route=self.model_route,
            ),
            recorded_at_ms=now,
        )

    async def _close(self, session: int, changed: bool) -> None:
        await self.sessions.record_close(
            close=NewCognitionSessionClose(
                session_id=session,
                item_index=0,
                kind="graph",
                reason="written" if changed else "no_change",
            ),
            recorded_at_ms=int(self.clock().timestamp() * 1000),
        )

    async def _refuse(self, session: int, part: str, reason: str) -> None:
        if part not in PARTS or reason not in REFUSAL_REASONS:
            raise ValueError("unknown comprehend refusal")
        await self.records.record_refusal(session, part, reason)
        self.metrics.inc("nora_comprehend_refused_parts_total", part=part, reason=reason)

    async def _comprehend(self, work: WorkItem) -> None:
        with span("comprehend:input"):
            item: ComprehendItem = await self.records.load(work)
            preview = await build_preview(self.graph, item)
            prompt = render_comprehend(item, preview, tokens=self.config.preview_tokens)
            fetched = GraphFetched()
            for source in item.sources:
                fetched.show(source)
        session = await self._session(work, "comprehend", prompt, COMPREHEND)
        if work.source[0] in ("note", "memo", "event_entry"):
            from nora.app.register_graph import RegisterGraphLinks
            await RegisterGraphLinks(graph=self.graph, records=self.records,
                                     metrics=self.metrics).project(item, session)
        aliases = await self.graph.aliases(unit=work.unit)
        private = (*self.private_names, *(a for a, k in aliases.items() if k[0] == "person"))
        state = LookupState(
            fetched,
            work.unit,
            session,
            self.records,
            self.metrics,
            private,
            tuple(sorted({p for s in item.sources for p in s.persons})),
        )
        state.limits = {
            "web_search": self.config.web_searches,
            "web_fetch": self.config.page_fetches,
        }
        held = self.tools.build(
            key=item.room,
            names=READ_NAMES,
            unit=None,
            adaptor="cognition",
            grant=self.tools_grant(),
        )
        if any(t.tool_name not in READ_NAMES for t in held):
            raise ValueError("unexpected read tool")

        async def memory_search(query: str) -> dict[str, Any]:
            """Search this unit's graph. Call this when a reference needs more context.

            Returns ok, nothing_found or unavailable with a labelled graph preview.

            Args:
                query: A name or phrase from the item to resolve locally.
            """
            aliases = await self.graph.aliases(unit=work.unit)
            seeds = [
                key
                for alias, key in aliases.items()
                if alias and alias.casefold() in query.casefold()
            ]
            for kind in PROPOSED_KINDS:
                seeds.extend(
                    e.key
                    for e in await self.graph.candidates(
                        unit=work.unit, kind=kind, name=query, limit=5
                    )
                )
            graph = await self.graph.walk(
                tuple(dict.fromkeys(seeds)),
                Walk(
                    unit=work.unit,
                    persons=frozenset(state.persons),
                    depth=2,
                    nodes=100,
                    fan_out=20,
                    valid_at=work.as_of,
                ),
            )
            if graph.status == "unavailable":
                return {"status": "unavailable", "note": "The graph could not be read."}
            from nora.domain.comprehend import Preview

            return {
                "status": "ok" if graph.nodes else "nothing_found",
                "preview": preview_text(
                    Preview(tuple(seeds), graph, (), preview.window),
                    tokens=self.config.preview_tokens,
                ),
            }

        # Shadow mode does not turn on the tag path's index-read switch. If
        # that registry surface is withheld, the same port supplies a local
        # graph-only search; it creates no second model or retrieval store.
        local_reads = [] if any(t.tool_name == "memory_search" for t in held) else [memory_search]
        staged: GraphWrite | None = None

        async def memory_stage(
            entities: list[dict[str, Any]],
            relations: list[dict[str, Any]],
            evidence: list[dict[str, Any]],
            corrections: list[dict[str, Any]],
            readings: list[dict[str, Any]],
        ) -> dict[str, Any]:
            """Stage the reading and graph write. Call this when the item is understood.

            Returns ok or refused with a reason. An empty write records no change.

            Args:
                entities: New non-person entities, resolved against candidates.
                relations: Notebook relations with evidence.
                evidence: Source keys and exact spans, never source snapshots.
                corrections: Changes to earlier relations, with evidence.
                readings: Record claims using the numeric source slots shown.
            """
            nonlocal staged
            if staged is not None:
                await self._refuse(session, "write", "already_staged")
                return {"status": "refused", "reason": "already_staged"}
            try:
                staged = GraphWrite.model_validate(
                    {
                        "entities": entities,
                        "relations": relations,
                        "evidence": evidence,
                        "corrections": corrections,
                        "readings": readings,
                    }
                )
            except ValidationError:
                await self._refuse(session, "write", "schema")
                return {"status": "refused", "reason": "schema"}
            return {"status": "ok"}

        with span("comprehend:model"):
            result = await self.agent.run(
                system_prompt=COMPREHEND,
                prompt=prompt,
                tools=[*(ComprehendRead(t, state) for t in held), *local_reads, memory_stage],
                budget=self.budget,
                trace_attributes={"cognition.session_id": str(session), "unit": work.unit},
            )
        await self.records.receipt(
            work,
            session,
            "model",
            {
                "total_tokens": result.total_tokens,
                "stop_reason": result.stop_reason,
                "messages": list(result.messages),
                "requirements": result.requirements,
            },
        )
        await self.records.receipt(
            work,
            session,
            "shown_sources",
            {
                "sources": [asdict(source) for source in fetched.sources.values()],
            },
        )
        if staged is None:
            raise ValueError("no explicit staged write")
        with span("comprehend:admit"):
            corrected_keys: dict[str, tuple[Key, Key]] = {}
            if staged.corrections:
                # Corrections may name a relation fetched after the preview. Resolve
                # its endpoints through the same scoped port, before closing it.
                aliases = await self.graph.aliases(unit=work.unit)
                correction_graph = await self.graph.walk(
                    tuple(dict.fromkeys((*aliases.values(), work.source))),
                    Walk(unit=work.unit, persons=frozenset(p for source in fetched.sources.values()
                         for p in source.persons), depth=1, nodes=10000, fan_out=10000,
                         layers=("record", "rollup", "notebook")),
                )
                if correction_graph.status in ("partial", "unavailable"):
                    raise RuntimeError("correction endpoints unavailable")
                corrected_keys = {e.relation.id: (e.relation.src, e.relation.dst)
                                  for e in correction_graph.relations}
            admission = await self._admit(work, staged, fetched, session)
            changed = tuple(k for c in admission.corrections
                            for k in corrected_keys.get(c.relation_id, ()))
            if changed:
                await self.records.receipt(work, session, "corrected_entities",
                                           {"keys": [list(k) for k in changed]})
            claims, refused = partition_claims(
                {"claims": [r.model_dump() for r in staged.readings]}, item.snapshot
            )
            for reason in refused:
                await self._refuse(session, "reading", reason)
            await self.records.readings(work, session, item, claims)
            await self._close(session, bool(admission.relations or admission.corrections or claims))
        # Changes already committed are never rolled back by a reshape failure.
        await self._reshape(item, admission, session, changed)

    @staticmethod
    def tools_grant() -> Any:
        from nora.agent.dreamer import cognition_grant

        return cognition_grant()

    async def _admit(
        self, work: WorkItem, output: GraphWrite, fetched: GraphFetched, session: int
    ) -> Admission:
        def stable(label: str) -> str:
            return str(uuid5(NAMESPACE_URL, f"{work.id}:{work.input_hash}:{label}"))

        ids = {r.id: stable(json.dumps(r.model_dump(), sort_keys=True)) for r in output.relations}
        entities = []
        for e in output.entities:
            if e.kind in ("person", "room", "unit", "nora"):
                await self._refuse(session, "entity", "identity_pointer")
            else:
                entities.append(
                    Entity(e.id, e.kind, work.unit, e.name, tuple(e.aliases), e.attributes)
                )
        evidence_parts = []
        for proposed in output.evidence:
            try:
                evidence_parts.append(Evidence(
                _key(proposed.source),
                (ids.get(proposed.relation_id, proposed.relation_id)
                 if proposed.relation_id is not None else None),
                proposed.entity_id,
                proposed.attribute,
                proposed.role,
                proposed.span,
            ))
            except ValueError:
                await self._refuse(session, "evidence", "schema")
        evidence = tuple(evidence_parts)
        sources = fetched.hydrate(
            [e.source for e in evidence]
        )
        relations = []
        for r in output.relations:
            src, dst = _key(r.src), _key(r.dst)
            cited = [
                sources[e.source]
                for e in evidence
                if e.relation_id == ids[r.id] and e.source in sources
            ]
            if any(s.key[0] == "web_page" for s in cited) and (
                "person" in (src[0], dst[0]) or r.persons
            ):
                await self._refuse(session, "relation", "private_web_evidence")
                continue
            try:
                relations.append(
                    Relation(
                        ids[r.id],
                        src,
                        r.rel,
                        dst,
                        "notebook",
                        work.unit,
                        tuple(r.persons),
                        _time(r.valid_from),
                        _time(r.valid_to),
                        asserted_at=work.as_of,
                        written_by=f"cognition:comprehend:{session}",
                    )
                )
            except ValueError:
                await self._refuse(session, "relation", "schema")
        correction_parts = []
        for c in output.corrections:
            try:
                correction_parts.append(Correction(
                    c.relation_id, c.kind, _time(c.at) or work.as_of,
                    ids.get(c.by, c.by) if c.by is not None else None,
                    tuple(_key(k) for k in c.evidence),
                ))
            except ValueError:
                await self._refuse(session, "correction", "schema")
        corrections = tuple(correction_parts)
        sources.update(fetched.hydrate([key for c in corrections for key in c.evidence]))
        admission = await self.graph.stage_and_admit(
            entities=tuple(entities),
            relations=tuple(relations),
            evidence=evidence,
            sources=sources,
            corrections=corrections,
        )
        duplicates = {r.id for r in admission.refused if r.reason == "duplicate_relation"}
        if duplicates:
            existing = await self.graph.walk(
                tuple(dict.fromkeys(k for r in relations for k in (r.src, r.dst))),
                Walk(
                    unit=work.unit,
                    persons=frozenset(p for r in relations for p in r.persons),
                    depth=1,
                    nodes=max(100, len(relations) * 2),
                    fan_out=max(20, len(relations)),
                    valid_at=work.as_of,
                ),
            )
            recovered = tuple(e.relation for e in existing.relations if e.relation.id in duplicates)
            recovered_ids = {r.id for r in recovered}
            admission = replace(
                admission,
                relations=(*admission.relations, *recovered),
                evidence=(
                    *admission.evidence,
                    *(e for e in evidence if e.relation_id in recovered_ids),
                ),
                refused=tuple(
                    r
                    for r in admission.refused
                    if not (
                        r.id in recovered_ids
                        and r.reason in ("duplicate_relation", "orphan_evidence")
                    )
                ),
            )
        for refusal in admission.refused:
            await self._refuse(session, refusal.kind, refusal.reason)
        await self.records.graph_write(work, session, admission)
        return admission

    async def _reshape(self, item: ComprehendItem, admission: Admission, parent: int,
                       corrected: tuple[Key, ...] = ()) -> None:
        work = item.work
        admission = replace(
            admission,
            evidence=tuple(
                dict.fromkeys((*admission.evidence, *await self.records.prior_evidence(work)))
            ),
        )
        changed = {k for r in admission.relations for k in (r.src, r.dst)} | set(corrected)
        # A retry after a committed graph write recovers the changed endpoints.
        changed |= set(await self.records.changed_graph_keys(work))
        if not changed and not admission.corrections and not item.response_closed:
            return
        scope = Walk(
            unit=work.unit,
            persons=frozenset(p for s in item.sources for p in s.persons),
            depth=2,
            nodes=100,
            fan_out=20,
            layers=("record", "rollup", "notebook"),
            valid_at=work.as_of,
        )
        context = await self.graph.walk(tuple(changed), scope)
        if context.status == "unavailable":
            raise RuntimeError("reshape graph unavailable")
        for r in admission.corrections:
            changed.update(
                k
                for e in context.relations
                if e.relation.id == r.relation_id
                for k in (e.relation.src, e.relation.dst)
            )
        persons = sorted(k for k in changed if k[0] == "person")
        rooms_units = sorted(
            {k for k in changed if k[0] in ("room", "unit")}
            | {
                e.relation.dst
                for e in context.relations
                if e.relation.src in persons and e.relation.dst[0] in ("room", "unit")
            }
        )
        if persons:
            rooms_units = sorted(
                set(rooms_units)
                | {
                    pointer_entity("room", work.unit, room, room).key
                    for person in persons
                    for room in self.rooms_by_person.get(
                        (work.unit, person[1].removeprefix(f"{work.unit}:person:")), ()
                    )
                }
            )
            rooms_units = sorted(
                set(rooms_units) | {pointer_entity("unit", work.unit, work.unit, work.unit).key}
            )
            if item.room:
                rooms_units = sorted(
                    set(rooms_units)
                    | {
                        pointer_entity(
                            "room", work.unit, f"{item.platform}:{item.room}", item.room
                        ).key
                    }
                )
        nora = (
            [pointer_entity("nora", work.unit, self.ai_peer, self.ai_peer).key]
            if item.response_closed
            else []
        )
        topics = sorted(k for k in changed if k[0] == "topic")
        for entity in [*persons, *rooms_units, *nora, *topics]:
            with span("comprehend:reshape", **{"nora.entity_kind": entity[0]}):
                subgraph = await self.graph.walk(
                    (entity, work.source) if entity[0] == "nora" else (entity,), scope
                )
                if subgraph.status == "unavailable":
                    raise RuntimeError("reshape subgraph unavailable")
                await self._reshape_entity(item, entity, subgraph, admission, parent)

    async def _reshape_entity(
        self, item: ComprehendItem, entity: Key, subgraph: Any, admission: Admission, parent: int
    ) -> None:
        work = item.work
        projected = tuple(
            Evidence(item.work.source, relation_id=e.relation.id)
            for e in subgraph.relations
            if e.relation.layer != "notebook"
            and item.work.source in (e.relation.src, e.relation.dst)
        )
        admission = replace(admission, evidence=(*admission.evidence, *projected))
        activity = None
        if entity[0] == "topic":
            activity = await self.graph.topic_activity(unit=work.unit, topic=entity, at=work.as_of)
            if activity.status == "unavailable":
                raise RuntimeError("reshape topic activity unavailable")
        prompt = render_reshape(
            entity,
            subgraph,
            work.as_of.isoformat(),
            evidence=admission.evidence,
            sources=item.sources,
            activity=activity,
        )
        session = await self._session(work, "reshape", prompt, RESHAPE)
        proposals: list[dict[str, Any]] | None = None

        async def profile_stage(proposals_in: list[dict[str, Any]]) -> dict[str, Any]:
            """Stage shadow profile proposals. Call this when the subgraph is understood.

            Returns ok or refused with a reason. Empty records no change.

            Args:
                proposals_in: Shadow lines with relation ids and evidence source keys.
            """
            nonlocal proposals
            if proposals is not None:
                await self._refuse(session, "profile", "already_staged")
                return {"status": "refused", "reason": "already_staged"}
            allowed = {
                "person": {"trait", "guess"},
                "room": {"culture"},
                "unit": {"culture"},
                "nora": {"lesson"},
                "topic": {"topic"},
            }[entity[0]]
            relations = {
                e.relation.id for e in subgraph.relations if e.relation.status == "current"
            }
            shown = {s.key for s in item.sources}
            supports = {(e.relation_id, e.source) for e in admission.evidence}
            kept = []
            for proposed in proposals_in:
                try:
                    p = ProfileProposal.model_validate(proposed)
                    keys = {_key(k) for k in p.evidence}
                except ValueError:
                    await self._refuse(session, "profile", "schema")
                    continue
                if (
                    p.part not in allowed
                    or not set(p.relation_ids) <= relations
                    or not keys <= shown
                    or not all(
                        any((r, key) in supports for key in keys) for r in p.relation_ids
                    )
                ):
                    await self._refuse(session, "profile", "ungrounded")
                else:
                    kept.append(p.model_dump())
            proposals = kept
            return {"status": "ok"}

        result = await self.agent.run(
            system_prompt=RESHAPE,
            prompt=prompt,
            tools=[profile_stage],
            budget=self.budget,
            trace_attributes={"cognition.session_id": str(session)},
        )
        await self.records.receipt(
            work,
            session,
            "model",
            {
                "total_tokens": result.total_tokens,
                "stop_reason": result.stop_reason,
                "messages": list(result.messages),
                "requirements": result.requirements,
            },
        )
        if proposals is None:
            raise ValueError("no explicit reshape result")
        # This service exposes no live profile/culture/lesson/entry collaborator.
        await self.records.shadow(work, session, entity, proposals, parent)
        await self._close(session, bool(proposals))
