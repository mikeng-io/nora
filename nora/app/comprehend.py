"""Cognition's one write verb: statement-shaped proposals.

The model POINTS — which meaning, which shown source row, which shown entity —
and code CONNECTS: ids, identity pointers, unit, persons, evidence links,
source_ids, dates. Code never invents meaning (no relation, subject, place or
date derived from a citation); the model never maintains bookkeeping (no
local ids, no foreign keys between lists, no persons unions).

Admission is unchanged: `stage_and_admit` → `AdmissionState._admit` →
`validate_relation`; what changed is only what the model is asked for.
Only the graph and Record/shadow rows are written.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from typing import Annotated, Any, Literal, get_args
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nora.agent.fetch_watch import GraphFetched
from nora.domain.comprehend import PARTS, REFUSAL_REASONS, named_candidates
from nora.domain.memory_graph import (
    MEMORY_KINDS,
    POINTER_KINDS,
    PROPOSED_KINDS,
    RELATION_RULES,
    Admission,
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
from nora.domain.prompt import contains_source_span

log = logging.getLogger(__name__)


class Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Citation(Shape):
    """One shown source row: its `n` and an exact quote from its body."""

    source: int = Field(description="The `n` of a shown source row.")
    quote: str | None = Field(default=None, description=(
        "Words copied exactly from that row's shown body, or null only where "
        "the statement kind allows an unquoted citation: a reading requires "
        "a quote covering every cited row; a relation or correction may cite "
        "a row without one."))


class CitedValue(Shape):
    """A public attribute value with the citations that establish it."""

    value: Any
    cite: list[Citation] = Field(default_factory=list)


ProposedKind = Literal[tuple(PROPOSED_KINDS)]  # type: ignore[valid-type]
ProposedRelation = Literal[tuple(
    name for name, rule in RELATION_RULES.items() if rule.layer == "notebook"
)]  # type: ignore[valid-type]


class NewEntity(Shape):
    """An entity with no matching shown candidate, nested inside an endpoint."""

    kind: ProposedKind
    name: str
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, CitedValue] = Field(default_factory=dict)
    distinct: str | None = Field(default=None, description=(
        "Why this entity is not one of the candidates a `possible_existing` "
        "refusal listed, or null; honoured only to re-propose an endpoint "
        "the session already refused possible_existing for the same name "
        "and kind — it never skips the lookup."))


class NewEndpoint(Shape):
    new: NewEntity


# An endpoint is a shown `@kind:id` handle (a preview row, an admitted
# relation's end, a person id as `@person:<id>`) or an inline new entity.
Endpoint = str | NewEndpoint


class RelationStatement(Shape):
    type: Literal["relation"]
    rel: ProposedRelation
    subject: Endpoint = Field(description=(
        "A shown `@kind:id` handle — a preview row, an admitted relation's "
        "end, or `@person:<id>` for a person a shown row authored — or "
        "{new:{kind,name,...}} for a new non-person entity."))
    object: Endpoint = Field(description="An endpoint in the same grammar as `subject`.")
    valid_from: str | None = Field(default=None, description=(
        "An ISO date or datetime the relation begins to hold, or null; unlike "
        "a reading's happened_at.expression this is a resolved calendar value "
        "you write yourself, never quoted words."))
    valid_to: str | None = Field(default=None, description=(
        "An ISO date or datetime the relation stops holding, or null; the "
        "same resolved-value contract as valid_from."))
    cite: list[Citation] = Field(min_length=1)


class CorrectionStatement(Shape):
    type: Literal["correction"]
    relation: str
    change: Literal["close", "supersede", "dispute", "retire"]
    at: str | None = Field(default=None, description=(
        "An ISO datetime the change takes effect, or null to mean now; a "
        "resolved value like valid_from, not quoted time words."))
    replacement: RelationStatement | None = None
    cite: list[Citation] = Field(min_length=1)


class HappenedAt(Shape):
    """When what a reading describes happened; the object is required even
    when the event time is unknown — only `expression` may be null."""

    source: int = Field(description=(
        "The `n` of one of this statement's cited rows — the row its time "
        "words come from."))
    expression: str | None = Field(default=None, description=(
        "The exact time words copied from that row's body — «tomorrow», «on "
        "Saturday» — or null when the row states none. Never the row's `at` "
        "metadata and never a date you computed: code resolves the quoted "
        "words against that row's send time. The only nullable field here."))


class ReadingSubject(Shape):
    person_id: str | None = Field(default=None, description=(
        "A shown canonical person id — a `person=` a rendered row carried — "
        "or null when the subject's identity is unresolved."))
    reference: str = Field(description=(
        "The subject as the source wrote them — «she», «Ben's sister»; "
        "required whether or not person_id resolves."))


class ReadingStatement(Shape):
    type: Literal["reading"]
    about: list[ReadingSubject] = Field(default_factory=list)
    what: str
    happened_at: HappenedAt = Field(description=(
        "When what the reading describes happened; required as an object "
        "even when the event time is unknown."))
    where: str | None = Field(default=None, description=(
        "A place copied exactly from a cited row's body, or null when no "
        "place was stated; never expanded into a canonical place name."))
    how: Literal["stated", "said_by_another", "planned", "asked", "reported"]
    cite: list[Citation] = Field(min_length=1)


Statement = Annotated[
    RelationStatement | CorrectionStatement | ReadingStatement,
    Field(discriminator="type"),
]


class Proposal(Shape):
    statements: list[Statement] = Field(default_factory=list)


_FIELD = re.compile(r"[a-z][a-z0-9_]{0,39}")


def _field_names(model: type[BaseModel]) -> set[str]:
    names = set(model.model_fields)
    for info in model.model_fields.values():
        for arg in (info.annotation, *get_args(info.annotation)):
            for inner in (arg, *get_args(arg)):
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    names |= _field_names(inner)
    return names


def _shape_errors(error: ValidationError, limit: int = 20) -> list[str]:
    """Where the shape is wrong, in the schema's own words only."""
    known = _field_names(Proposal)
    return [
        ".".join(
            str(p) if isinstance(p, int) or p in known or _FIELD.fullmatch(p) else "<unknown field>"
            for p in e["loc"]
        )
        + ": "
        + e["type"]
        for e in error.errors(include_input=False, include_url=False)[:limit]
    ]


def _time(value: str | None, tz: tzinfo | None) -> datetime | None:
    """An ISO time; a date or naive time is read in the batch clock's zone."""
    if value is None:
        return None
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        if tz is None:
            raise ValueError("time requires offset")
        at = at.replace(tzinfo=tz)
    return at


@dataclass
class Scope:
    """What one proposal may cite and name, built from the shown rows only."""

    catalogue: dict[int, Key] = field(default_factory=dict)
    shown_keys: frozenset[Key] = frozenset()
    shown_relations: frozenset[str] = frozenset()
    shown_persons: frozenset[str] = frozenset()
    admitted: frozenset[str] = frozenset()


def proposal_scope(
    inputs: list[Any], fetched: GraphFetched, *,
    shown_keys: Any = frozenset(), shown_relations: Any = frozenset(),
    shown_persons: Any = frozenset(), admitted: Any = frozenset(),
) -> Scope:
    """The citable catalogue of one proposal call: the union of the named
    items' rendered rows, plus the tool-fetched rows the session was shown.
    Handles are citable only when the session showed them: node keys in
    `shown_keys`, correction targets in `shown_relations` or `admitted`, and
    person handles authored by a shown row — the `person=` the agenda emitted
    or a row a read tool handed out; a source's `persons` is its scope, not
    its authorship."""
    scope = Scope(
        shown_keys=frozenset(shown_keys) | frozenset(fetched.sources),
        shown_relations=frozenset(shown_relations),
        shown_persons=frozenset(shown_persons) | frozenset(
            str(r["person_id"]) for item in inputs
            for r in item.snapshot.get("messages", ())
            if r.get("person_id")),
        admitted=frozenset(admitted),
    )
    for item in inputs:
        for n, key in item.snapshot.get("source_keys", {}).items():
            try:
                key = tuple(key)
                scope.catalogue[int(n)] = key
            except (TypeError, ValueError):
                continue
    return scope


class MemoryProposalAdmission:
    """Admission only; the wake owns the model session and its clock."""

    def __init__(self, *, graph: MemoryGraph, records: Any, metrics: Any) -> None:
        self.graph, self.records, self.metrics = graph, records, metrics

    async def _refuse(self, session: int, part: str, reason: str) -> None:
        if part not in PARTS or reason not in REFUSAL_REASONS:
            raise ValueError("unknown comprehend refusal")
        await self.records.record_refusal(session, part, reason)
        if self.metrics is not None:
            self.metrics.inc(
                "nora_comprehend_refused_parts_total", part=part, reason=reason)

    def _endpoint(
        self, value: Endpoint, work: WorkItem, scope: Scope,
    ) -> tuple[Key | None, Entity | None, tuple[str, str] | None]:
        """Resolve one endpoint: a shown handle or an inline new entity.

        Returns (key, entity_or_none, failure_or_none) where failure is
        (part, reason); a `{new: …}` endpoint's refusal is the entity's part,
        a bad handle's the relation's."""
        if isinstance(value, NewEndpoint):
            e = value.new
            if e.kind in POINTER_KINDS:
                return None, None, ("entity", "identity_pointer")
            if e.kind not in PROPOSED_KINDS or not any(
                    row.kind == e.kind for row in MEMORY_KINDS):
                return None, None, ("entity", "vocabulary")
            entity = Entity(
                _stable(work, "entity:" + json.dumps(
                    e.model_dump(mode="json"), sort_keys=True)),
                e.kind, work.unit, e.name, tuple(e.aliases),
                {name: cited.value for name, cited in e.attributes.items()},
            )
            return entity.key, entity, None
        if not isinstance(value, str) or not value.startswith("@"):
            return None, None, ("relation", "endpoint_not_a_handle")
        kind, _, ref = value[1:].partition(":")
        if not kind or not ref:
            return None, None, ("relation", "endpoint_not_a_handle")
        if kind in POINTER_KINDS:
            if not ref.startswith(f"{work.unit}:{kind}:"):
                ref = f"{work.unit}:{kind}:{ref}"
            key = (kind, ref)
            # A person handle is shown when a rendered row authored them or
            # the pointer was already shown/committed this session.
            if (kind == "person" and ref.rpartition(":")[2] in scope.shown_persons) \
                    or key in scope.shown_keys:
                return key, None, None
            return None, None, ("relation", "unshown")
        key = (kind, ref)
        # Entity and record kinds name graph nodes the session must have
        # shown; existing-in-store is not shown-to-this-session.
        if key in scope.shown_keys:
            return key, None, None
        return None, None, ("relation", "unshown")

    def _cited(
        self, cites: list[Citation], scope: Scope, fetched: GraphFetched,
    ) -> tuple[dict[Key, Source], str | None]:
        """Resolve a statement's citations to shown source snapshots."""
        keys: list[Key] = []
        for cite in cites:
            key = scope.catalogue.get(cite.source)
            if key is None:
                key = ("message", str(cite.source))
            if key not in fetched.sources:
                return {}, "unseen_source"
            keys.append(key)
        return {key: fetched.sources[key] for key in keys}, None

    async def _possible_existing(
        self, new: NewEntity, work: WorkItem, fetched: GraphFetched,
        proposed: Entity, confirmed: Any,
    ) -> tuple[str, list[dict[str, Any]], set[Key], set[str]] | None:
        """The resolution candidates (27 §7a) for a `{new: …}` endpoint's
        name/kind in this unit. Returns `(reason, cards, keys, relation_ids)`
        — the refusal reason, the candidate cards that go back to the model,
        and exactly the handles and relation ids THOSE cards emit (shown
        custody is registered from what is returned, never from what the
        walk touched) — or None when the endpoint may proceed.

        The lookup always runs: `new.distinct` never skips it, and a lookup
        that fails is `unavailable` (R-27), never an admission. `distinct`
        is honoured only to re-propose an endpoint this session already
        refused `possible_existing` for the same name and kind (`confirmed`).
        Two hard-refusal paths stay exactly as they were: the candidate that
        IS `proposed` (same deterministic id — a retry of a statement this
        session already admitted) is the duplicate-recovery path's business,
        and an exact name/alias match is the store's own `duplicate_entity`."""
        try:
            candidates = await named_candidates(
                self.graph, unit=work.unit, kind=new.kind, name=new.name,
                persons=frozenset(
                    p for s in fetched.sources.values() for p in s.persons),
                near=work.source, as_of=work.as_of, historical=work.historical,
                drop=lambda e: e.key == proposed.key
                               or new.name in (e.name, *e.aliases))
        except Exception as exc:  # noqa: BLE001
            log.warning("resolution candidates failed (%s)", type(exc).__name__)
            return "unavailable", [], set(), set()
        if not candidates:
            return None
        if (new.distinct or "").strip() and (
                new.kind, new.name.casefold()) in confirmed:
            return None
        cards: list[dict[str, Any]] = []
        keys: set[Key] = set()
        relation_ids: set[str] = set()
        for candidate in candidates:
            node = next((n for n in candidate.nodes if n.entity is not None), None)
            entity = node.entity if node is not None else None
            cards.append({
                "handle": (f"@{entity.key[0]}:{entity.key[1]}" if entity else None),
                "name": entity.name if entity else None,
                "aliases": list(entity.aliases) if entity else [],
                "relations": [
                    {"id": e.relation.id, "rel": e.relation.rel,
                     "src": f"@{e.relation.src[0]}:{e.relation.src[1]}",
                     "dst": f"@{e.relation.dst[0]}:{e.relation.dst[1]}",
                     "evidence": e.evidence_count,
                     "asserted_at": e.relation.asserted_at.isoformat()}
                    for e in candidate.relations],
            })
            if entity is not None:
                keys.add(entity.key)
            for e in candidate.relations:
                keys.update((e.relation.src, e.relation.dst))
                relation_ids.add(e.relation.id)
        return "possible_existing", cards, keys, relation_ids

    async def _admit(
        self, work: WorkItem, output: Proposal, fetched: GraphFetched,
        session: int, scope: Scope, indices: list[int] | None = None,
        confirmed: Any = frozenset(),
    ) -> tuple[Admission, list[dict[str, str]], dict[str, Any]]:
        """Flatten the statements into the existing domain objects, admit them,
        and report one outcome per statement. `indices` maps each valid
        statement's position back to its index in the original call, so a
        schema-refused sibling neither shifts results nor withholds admission.
        `confirmed` is the session's `(kind, name)` pairs a `possible_existing`
        refusal was already returned for — the only endpoints `new.distinct`
        may confirm."""
        local_refusals: list[dict[str, str]] = []
        report: dict[int, dict[str, Any]] = {}
        candidate_keys: set[Key] = set()
        candidate_relations: set[str] = set()
        possible_existing: set[tuple[str, str]] = set()

        async def refuse(part: str, reason: str) -> None:
            local_refusals.append({"kind": part, "reason": reason})
            await self._refuse(session, part, reason)

        def stable(label: str) -> str:
            return _stable(work, label)

        entities: list[Entity] = []
        entity_stmt: dict[str, int] = {}
        relations: list[Relation] = []
        relation_stmt: dict[str, int] = {}
        evidence: list[Evidence] = []
        attr_cites: dict[tuple[str, str], list[int]] = {}
        corrections: list[Correction] = []
        correction_stmt: dict[Correction, int] = {}
        claims: dict[int, dict[str, Any]] = {}

        async def flatten_relation(
            index: int, st: RelationStatement,
        ) -> str | None:
            """Returns the staged relation id, or None with the refusal recorded."""
            if not any(st.rel in row.relates_by for row in MEMORY_KINDS):
                report[index] = {"status": "refused", "reason": "vocabulary",
                                 "kind": "relation"}
                await refuse("relation", "vocabulary")
                return None
            try:
                valid_from = _time(st.valid_from, work.as_of.tzinfo)
            except ValueError:
                report[index] = {"status": "refused", "reason": "unparseable_time",
                                 "kind": "relation", "detail": "valid_from"}
                await refuse("relation", "unparseable_time")
                return None
            try:
                valid_to = _time(st.valid_to, work.as_of.tzinfo)
            except ValueError:
                report[index] = {"status": "refused", "reason": "unparseable_time",
                                 "kind": "relation", "detail": "valid_to"}
                await refuse("relation", "unparseable_time")
                return None
            keys: dict[str, Key] = {}
            for endpoint, slot in ((st.subject, "src"), (st.object, "dst")):
                key, entity, failure = self._endpoint(endpoint, work, scope)
                if failure is not None:
                    part, reason = failure
                    report[index] = {"status": "refused", "reason": reason,
                                     "kind": "relation"}
                    await refuse(part, reason)
                    return None
                if key is None:
                    return None
                keys[slot] = key
                if entity is not None and isinstance(endpoint, NewEndpoint):
                    outcome = await self._possible_existing(
                        endpoint.new, work, fetched, entity, confirmed)
                    if outcome is not None:
                        reason, cards, keys_found, rel_ids = outcome
                        report[index] = {
                            "status": "refused", "reason": reason,
                            "kind": "relation",
                            **({"candidates": cards} if cards else {})}
                        await refuse("entity", reason)
                        if reason == "possible_existing":
                            candidate_keys.update(keys_found)
                            candidate_relations.update(rel_ids)
                            possible_existing.add(
                                (endpoint.new.kind,
                                 endpoint.new.name.casefold()))
                        return None
                    entities.append(entity)
                    entity_stmt[entity.id] = index
                    for name, cited in endpoint.new.attributes.items():
                        attr_cites[(entity.id, name)] = [
                            c.source for c in cited.cite]
                        attr_sources, _ = self._cited(cited.cite, scope, fetched)
                        for cite in cited.cite:
                            ekey = scope.catalogue.get(cite.source) or (
                                "message", str(cite.source))
                            if ekey in attr_sources:
                                evidence.append(Evidence(
                                    ekey, entity_id=entity.id,
                                    attribute=name, span=cite.quote))
            sources, cite_reason = self._cited(st.cite, scope, fetched)
            if cite_reason is not None:
                report[index] = {"status": "refused", "reason": cite_reason,
                                 "kind": "relation"}
                await refuse("relation", cite_reason)
                return None
            src, dst = keys["src"], keys["dst"]
            rel_id = stable("relation:" + json.dumps(
                st.model_dump(mode="json"), sort_keys=True))
            persons = sorted({p for s in sources.values() for p in s.persons})
            if any(s.key[0] == "web_page" for s in sources.values()) and (
                "person" in (src[0], dst[0]) or persons
            ):
                report[index] = {"status": "refused",
                                 "reason": "private_web_evidence", "kind": "relation"}
                await refuse("relation", "private_web_evidence")
                return None
            try:
                relation = Relation(
                    rel_id, src, st.rel, dst, "notebook", work.unit,
                    tuple(persons), valid_from, valid_to,
                    asserted_at=work.as_of,
                    written_by=f"cognition:comprehend:{session}",
                )
            except ValueError:
                report[index] = {"status": "refused", "reason": "invalid_relation",
                                 "kind": "relation"}
                await refuse("relation", "invalid_relation")
                return None
            relations.append(relation)
            relation_stmt[rel_id] = index
            for cite in st.cite:
                key = scope.catalogue.get(cite.source) or ("message", str(cite.source))
                evidence.append(Evidence(key, relation_id=rel_id, span=cite.quote))
            return rel_id

        for pos, st in enumerate(output.statements):
            index = indices[pos] if indices is not None else pos
            if isinstance(st, ReadingStatement):
                claims[index] = {
                    "about": [s.model_dump() for s in st.about],
                    "what": st.what,
                    "happened_at": {
                        "source_id": st.happened_at.source,
                        "expression": st.happened_at.expression,
                    },
                    "where": st.where,
                    "how": st.how,
                    "source_ids": [c.source for c in st.cite],
                    "evidence": [
                        {"source_id": c.source, "quote": c.quote}
                        for c in st.cite if c.quote is not None
                    ],
                }
                report[index] = {"status": "pending", "kind": "reading"}
            elif isinstance(st, CorrectionStatement):
                if st.relation not in scope.shown_relations | scope.admitted:
                    report[index] = {"status": "refused", "reason": "unshown",
                                     "kind": "correction"}
                    await refuse("correction", "unshown")
                    continue
                sources, cite_reason = self._cited(st.cite, scope, fetched)
                if cite_reason is not None:
                    report[index] = {"status": "refused", "reason": cite_reason,
                                     "kind": "correction"}
                    await refuse("correction", cite_reason)
                    continue
                bad_quote = next(
                    (c for c in st.cite
                     if c.quote is not None and not contains_source_span(c.quote, sources[
                         scope.catalogue.get(c.source)
                         or ("message", str(c.source))].text)),
                    None,
                )
                if bad_quote is not None:
                    report[index] = {"status": "refused",
                                     "reason": "span_not_in_source",
                                     "kind": "correction"}
                    await refuse("correction", "span_not_in_source")
                    continue
                by = None
                if st.change == "supersede":
                    if st.replacement is None:
                        report[index] = {"status": "refused",
                                         "reason": "missing_replacement",
                                         "kind": "correction"}
                        await refuse("correction", "missing_replacement")
                        continue
                    by = await flatten_relation(index, st.replacement)
                    if by is None:
                        continue
                elif st.replacement is not None:
                    report[index] = {"status": "refused",
                                     "reason": "unexpected_replacement",
                                     "kind": "correction"}
                    await refuse("correction", "unexpected_replacement")
                    continue
                try:
                    correction = Correction(
                        st.relation, st.change,
                        _time(st.at, work.as_of.tzinfo) or work.as_of,
                        by=by,
                        evidence=tuple(sources.keys()),
                    )
                except ValueError:
                    report[index] = {"status": "refused",
                                     "reason": "unparseable_time",
                                     "kind": "correction", "detail": "at"}
                    await refuse("correction", "unparseable_time")
                    continue
                corrections.append(correction)
                correction_stmt[correction] = index
            else:
                await flatten_relation(index, st)

        admission = await self.graph.stage_and_admit(
            entities=tuple(entities),
            relations=tuple(relations),
            evidence=tuple(evidence),
            sources=fetched.sources,
            corrections=tuple(corrections),
            shown_persons=scope.shown_persons,
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
            recovered_ends = {k for r in recovered for k in (r.src, r.dst)}
            connected_ids = {e.id for e in entities if e.key in recovered_ends}
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
                    ) and not (r.kind == "entity" and r.reason == "orphan_entity"
                               and r.id in connected_ids)
                ),
            )
        admitted_relation_ids = {r.id for r in admission.relations}
        admitted_correction_targets = {c.relation_id for c in admission.corrections}
        refused_by_stmt: dict[int, list[Any]] = {}
        for refusal in admission.refused:
            owner: int | None = None
            if refusal.kind == "relation" and refusal.id in relation_stmt:
                owner = relation_stmt[refusal.id]
            elif refusal.kind == "correction" and refusal.id in {
                    c.relation_id for c in correction_stmt}:
                owner = next(i for c, i in correction_stmt.items()
                             if c.relation_id == refusal.id)
            elif refusal.kind == "entity" and refusal.id in entity_stmt:
                owner = entity_stmt[refusal.id]
            elif refusal.kind == "evidence":
                owner = relation_stmt.get(
                    refusal.id, entity_stmt.get(refusal.id))
            if owner is not None:
                refused_by_stmt.setdefault(owner, []).append(refusal)
        for refusal in admission.refused:
            await self._refuse(session, refusal.kind, refusal.reason)
        await self.records.graph_write(work, session, admission)

        for pos, st in enumerate(output.statements):
            index = indices[pos] if indices is not None else pos
            if index in report and report[index]["status"] != "pending":
                continue
            if isinstance(st, ReadingStatement):
                continue
            saved = False
            if isinstance(st, CorrectionStatement):
                saved = st.relation in admitted_correction_targets
            else:
                rel_id = stable("relation:" + json.dumps(
                    st.model_dump(mode="json"), sort_keys=True))
                saved = rel_id in admitted_relation_ids
            refused_here = refused_by_stmt.get(index, [])
            if saved:
                entry: dict[str, Any] = {"status": "saved", "kind": st.type}
                parts = [
                    {"kind": r.kind, "reason": r.reason,
                     **({"attribute": r.attribute} if r.attribute else {}),
                     **({"cite": attr_cites[(r.id, r.attribute)]}
                        if r.attribute and (r.id, r.attribute) in attr_cites
                        else {})}
                    for r in refused_here
                ]
                if parts:
                    entry["refused_parts"] = parts
                report[index] = entry
            else:
                principal = None
                if isinstance(st, CorrectionStatement):
                    corr = next((r for r in refused_here if r.kind == "correction"), None)
                    rel = next((r for r in refused_here if r.kind == "relation"), None)
                    # missing_replacement is the cascade of the replacement's own
                    # refusal; the relation's reason is the primary one.
                    principal = (
                        rel if corr is not None and corr.reason == "missing_replacement"
                               and rel is not None else corr or rel)
                else:
                    principal = next(
                        (r for r in refused_here if r.kind == "relation"), None)
                if principal is None and refused_here:
                    principal = refused_here[0]
                report[index] = {
                    "status": "refused",
                    "kind": st.type,
                    "reason": principal.reason if principal else "ungrounded",
                    "part": principal.kind if principal else "ungrounded",
                    **({"attribute": principal.attribute}
                       if principal and principal.attribute else {}),
                    **({"cite": attr_cites[(principal.id, principal.attribute)]}
                       if principal and principal.attribute
                       and (principal.id, principal.attribute) in attr_cites
                       else {}),
                }
        return admission, local_refusals, {
            "report": report, "claims": claims,
            "refused_by_stmt": refused_by_stmt,
            "candidate_keys": candidate_keys,
            "candidate_relations": candidate_relations,
            "possible_existing": possible_existing,
        }


def _stable(work: WorkItem, label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{work.id}:{work.input_hash}:{label}"))
