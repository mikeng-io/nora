"""Cognition steps one and two. Only the graph and Record/shadow rows are written."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, get_args
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from nora.agent.fetch_watch import GraphFetched
from nora.app.extraction import ExtractionClaim
from nora.domain.comprehend import PARTS, REFUSAL_REASONS
from nora.domain.memory_graph import (
    POINTER_KINDS,
    Admission,
    Correction,
    Entity,
    Evidence,
    Key,
    MemoryGraph,
    Relation,
    Walk,
    WorkItem,
)

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

    @model_validator(mode="after")
    def _targets_something(self) -> ProposedEvidence:
        # Evidence that names neither a relation nor an attribute supports
        # nothing; refused here so the model is told, not silently orphaned.
        if self.relation_id is None and (self.entity_id is None or self.attribute is None):
            raise PydanticCustomError("evidence_target_missing", "evidence names no target")
        return self


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


_FIELD = re.compile(r"[a-z][a-z0-9_]{0,39}")


def _field_names(model: type[BaseModel]) -> set[str]:
    names = set(model.model_fields)
    for field in model.model_fields.values():
        for arg in (field.annotation, *get_args(field.annotation)):
            for inner in (arg, *get_args(arg)):
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    names |= _field_names(inner)
    return names


def _shape_errors(error: ValidationError, limit: int = 20) -> list[str]:
    """Where the shape is wrong, in the schema's own words only.

    A path part the schema does not define is a key the model wrote. It is
    echoed only when shaped like a field name (`target`); anything else is
    redacted. A one-word key from the source still echoes, which reveals
    nothing new: the same model wrote it in this call. The reason is pydantic's
    error type, never its message, which a validator may build from the value.
    """
    known = _field_names(GraphWrite)
    return [
        ".".join(
            str(p) if isinstance(p, int) or p in known or _FIELD.fullmatch(p) else "<unknown field>"
            for p in e["loc"]
        )
        + ": "
        + e["type"]
        for e in error.errors(include_input=False, include_url=False)[:limit]
    ]


def _endpoint(value: list[str], unit: str) -> Key:
    """A relation end; a person, room, unit or Nora named by its authored id
    becomes the unit's pointer (27 §3), which code supplies, not the model."""
    kind, ref = _key(value)
    if kind in POINTER_KINDS and not ref.startswith(f"{unit}:{kind}:"):
        return kind, f"{unit}:{kind}:{ref}"
    return kind, ref


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


class MemoryProposalAdmission:
    """Admission only; the wake owns the model session and its clock."""

    def __init__(self, *, graph: MemoryGraph, records: Any, metrics: Any) -> None:
        self.graph, self.records, self.metrics = graph, records, metrics

    async def _refuse(self, session: int, part: str, reason: str) -> None:
        if part not in PARTS or reason not in REFUSAL_REASONS:
            raise ValueError("unknown comprehend refusal")
        await self.records.record_refusal(session, part, reason)
        self.metrics.inc("nora_comprehend_refused_parts_total", part=part, reason=reason)

    async def _admit(
        self, work: WorkItem, output: GraphWrite, fetched: GraphFetched, session: int
    ) -> tuple[Admission, list[dict[str, str]]]:
        local_refusals: list[dict[str, str]] = []

        async def refuse(part: Literal["entity", "relation", "evidence", "correction"],
                         reason: str) -> None:
            local_refusals.append({"kind": part, "reason": reason})
            await self._refuse(session, part, reason)

        def stable(label: str) -> str:
            return str(uuid5(NAMESPACE_URL, f"{work.id}:{work.input_hash}:{label}"))

        ids = {r.id: stable(json.dumps(r.model_dump(), sort_keys=True)) for r in output.relations}
        entities = []
        for e in output.entities:
            if e.kind in ("person", "room", "unit", "nora"):
                await refuse("entity", "identity_pointer")
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
                await refuse("evidence", "schema")
        evidence = tuple(evidence_parts)
        sources = fetched.hydrate(
            [e.source for e in evidence]
        )
        relations = []
        for r in output.relations:
            src, dst = _endpoint(r.src, work.unit), _endpoint(r.dst, work.unit)
            cited = [
                sources[e.source]
                for e in evidence
                if e.relation_id == ids[r.id] and e.source in sources
            ]
            if any(s.key[0] == "web_page" for s in cited) and (
                "person" in (src[0], dst[0]) or r.persons
            ):
                await refuse("relation", "private_web_evidence")
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
                await refuse("relation", "schema")
        correction_parts = []
        for c in output.corrections:
            try:
                correction_parts.append(Correction(
                    c.relation_id, c.kind, _time(c.at) or work.as_of,
                    ids.get(c.by, c.by) if c.by is not None else None,
                    tuple(_key(k) for k in c.evidence),
                ))
            except ValueError:
                await refuse("correction", "schema")
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
        return admission, local_refusals

