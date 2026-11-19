"""Form and scope decisions for a batch of model proposals."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, cast, get_args

from nora.domain.graph_registration import Exposure, InputScope, SourceKind, inherited_scope
from nora.domain.lineage import ROW, Lineage, unresolved
from nora.memory.domain import graph
from nora.memory.domain.admission_proposals import (
    AddEdge,
    CreateClaim,
    CreateEntity,
    CreateIntention,
    ExposureOrder,
    Link,
    MergeEntities,
    PassContext,
    PersonAuthor,
    Proposal,
    ProposalFields,
    ResolvedReferences,
    Retire,
    Revise,
    RowFacts,
    SourceReference,
)

FormRefusal = Literal[
    "unknown_kind",
    "unknown_edge_type",
    "illegal_endpoints",
    "illegal_basis",
    "illegal_transition",
    "citation_not_shown",
    "unrooted_citation",
    "missing_evidence",
    "invalid_reasoning",
    "invalid_confidence",
    "told_without_person",
    "wrong_unit",
    "scope_widening",
    "unresolved_room",
    "unresolved_person",
    "pointer_entity",
    "orphan_entity",
    "invalid_merge",
    "trigger_without_evidence",
    "occasion_basis",
    "invalid_field",
    "unbacked_page_reference",
    "unpollable_watch_target",
]
WriteRefusal = Literal["stale_base", "credential", "doctrine", "content", "unsafe_page_reference"]
RefusalReason = Literal[FormRefusal, WriteRefusal]


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: RefusalReason
    field: str


@dataclass(frozen=True, slots=True)
class Admitted:
    proposal: Proposal
    row: RowFacts
    links: tuple[Link, ...]
    base: RowFacts | None = None
    survivor: RowFacts | None = None
    owner: str | None = None
    owed_to: str | None = None
    topics: tuple[Admitted, ...] = ()


class _RefusalError(Exception):
    def __init__(self, reason: FormRefusal, field: str) -> None:
        self.refusal = Refusal(reason, field)


_CREATE_BASIS = {"claim": "inferred", "entity": "inferred", "intention": "committed"}
_OCCASIONS = {
    "called": ("committed",),
    "ambient": ("committed",),
    "proactive": ("committed",),
    "debrief": ("committed",),
    "wake": ("committed", "inferred"),
}
_REVISION_STATES = {
    "claim": ("live", "superseded", "awaiting_weighing"),
    "entity": ("admitted", "merged"),
    "intention": ("open", "superseded"),
    "edge": ("live", "superseded"),
}
_RETIRE_STATES = {
    "claim": ("live", "awaiting_weighing", "superseded"),
    "entity": ("admitted", "merged"),
    "intention": ("open", "superseded"),
    "edge": ("live", "superseded"),
}
_ENDINGS = {
    "achieve": get_args(graph.AchieveEnding),
    "maintain": get_args(graph.MaintainEnding),
}
_SPEECH_SOURCES = frozenset({"messages", "message_edit", "message_reaction"})


def fields(proposal: Proposal) -> ProposalFields:
    return proposal.replacement if isinstance(proposal, Revise) else proposal


def _links(proposal: ProposalFields) -> tuple[Link, ...]:
    if isinstance(proposal, AddEdge):
        return (
            Link(
                type=proposal.type,
                source=proposal.source,
                target=proposal.target,
                basis=proposal.basis,
                reasoning=proposal.reasoning,
                confidence=proposal.confidence,
            ),
        )
    if isinstance(proposal, (CreateClaim, CreateEntity, CreateIntention)):
        return proposal.links
    return ()


def row_references(proposal: Proposal) -> frozenset[str]:
    data = fields(proposal)
    ids = {data.id, *data.citations}
    for link in _links(data):
        ids.update((link.source, link.target))
    if isinstance(proposal, (Revise, Retire)):
        ids.add(proposal.base)
    if isinstance(proposal, MergeEntities):
        ids.update((proposal.source, proposal.survivor))
    return frozenset(ids)


def topic_targets(proposal: Proposal) -> tuple[str, ...]:
    data = fields(proposal)
    if not isinstance(data, (CreateClaim, CreateIntention)) or not data.citations:
        return ()
    return tuple(dict.fromkeys(
        link.target for link in data.links if link.type == "about" and link.source == data.id
    ))


def pending_topics(proposals: Mapping[int, Proposal]) -> dict[str, int]:
    targets = {target for proposal in proposals.values() for target in topic_targets(proposal)}
    return {
        data.id: index
        for index, proposal in proposals.items()
        if isinstance(data := fields(proposal), CreateEntity)
        and not data.links and data.id in targets
    }


def decide_admission(
    proposal: Proposal,
    context: PassContext,
    shown: Collection[str],
    rows: Mapping[str, RowFacts],
    refs: ResolvedReferences,
    *,
    watch_kinds: Collection[str] = (),
    topics: Iterable[Proposal] = (),
) -> Admitted | Refusal:
    """Decide a proposal with the evidenced topics it can bring into existence."""
    facts = dict(rows)
    available = set(shown)
    admission = _Admission(context, available, facts, refs, watch_kinds)
    try:
        data = fields(proposal)
        targets = frozenset(topic_targets(proposal))
        choices = []
        for topic in topics:
            topic_data = fields(topic)
            if not isinstance(data, (CreateClaim, CreateIntention)) or (
                not isinstance(topic_data, CreateEntity) or topic_data.id not in targets
                or topic_data.id in available
            ):
                continue
            evidence = admission._citations(data, data.kind)
            choice = admission.decide(topic, topic_evidence=evidence)
            choices.append(choice)
            available.add(choice.row.id)
            facts[choice.row.id] = choice.row
        return replace(admission.decide(proposal), topics=tuple(choices))
    except _RefusalError as exc:
        return exc.refusal


def admit_batch(
    proposals: Iterable[Proposal],
    context: PassContext,
    shown: Iterable[str],
    rows: Mapping[str, RowFacts],
    refs: ResolvedReferences,
    *,
    watch_kinds: Collection[str] = (),
) -> tuple[Admitted | Refusal, ...]:
    """Decide in order, including the new topics linked by an evidenced sibling."""
    indexed = dict(enumerate(proposals))
    pending = pending_topics(indexed)
    deferred = set(pending.values())
    facts = dict(rows)
    available = set(shown)
    decisions: dict[int, Admitted | Refusal] = {}
    for index, proposal in indexed.items():
        if index in deferred:
            continue
        choice = decide_admission(
            proposal, context, available, facts, refs, watch_kinds=watch_kinds,
            topics=(
                indexed[pending[target]] for target in topic_targets(proposal) if target in pending
            ),
        )
        decisions[index] = choice
        if isinstance(choice, Refusal):
            continue
        for topic in choice.topics:
            decisions[pending.pop(topic.row.id)] = topic
            facts[topic.row.id] = topic.row
            available.add(topic.row.id)
        facts[choice.row.id] = choice.row
        if choice.base is not None:
            if choice.survivor is not None:
                facts[choice.base.id] = replace(
                    choice.base,
                    state="merged",
                    origin_room=choice.row.origin_room,
                    room_is_dm=choice.row.room_is_dm,
                    exposure=choice.row.exposure,
                    excluded_persons=choice.row.excluded_persons,
                    lineage=choice.row.lineage,
                )
            elif choice.base.kind != "entity":
                facts[choice.base.id] = replace(choice.base, state="superseded")
        available.add(choice.row.id)
    for index in pending.values():
        decisions[index] = decide_admission(
            indexed[index], context, available, facts, refs, watch_kinds=watch_kinds,
        )
    return tuple(decisions[index] for index in indexed)


class _Admission:
    def __init__(
        self,
        context: PassContext,
        shown: set[str],
        rows: Mapping[str, RowFacts],
        refs: ResolvedReferences,
        watch_kinds: Collection[str],
    ) -> None:
        self.context = context
        self.shown = shown
        self.rows = rows
        self.refs = refs
        self.watch_kinds = watch_kinds

    def decide(self, proposal: Proposal, *, topic_evidence: tuple[RowFacts, ...] = ()) -> Admitted:
        data = fields(proposal)
        if not isinstance(
            data, (CreateClaim, CreateEntity, CreateIntention, AddEdge, Retire, MergeEntities)
        ):
            raise _RefusalError("unknown_kind", "kind")
        if not data.id or data.id in self.rows:
            raise _RefusalError("invalid_field", "id")
        if data.scope.unit != self.context.unit:
            raise _RefusalError("wrong_unit", "unit")
        base, survivor = self._transition(proposal)
        wanted: str | None
        if isinstance(data, Retire) and base is not None:
            kind, wanted = base.kind, base.basis
        elif isinstance(data, MergeEntities):
            kind, wanted = "edge", "inferred"
        elif isinstance(data, (CreateClaim, CreateEntity, CreateIntention, AddEdge)):
            kind, wanted = data.kind, _CREATE_BASIS.get(data.kind)
        else:
            raise _RefusalError("illegal_transition", "base")
        if wanted is not None and data.basis != wanted:
            raise _RefusalError("illegal_basis", "basis")
        if data.basis not in _OCCASIONS.get(self.context.occasion, ()):
            if isinstance(data, AddEdge) and data.basis not in get_args(graph.GraphBasis):
                raise _RefusalError("illegal_basis", "basis")
            raise _RefusalError("occasion_basis", "basis")
        evidence = self._citations(data, base.kind if survivor and base else kind)
        if isinstance(data, CreateClaim) and not evidence:
            raise _RefusalError("missing_evidence", "citations")
        if isinstance(data, MergeEntities) and not evidence:
            raise _RefusalError("invalid_merge", "citations")
        if (
            isinstance(data, CreateIntention)
            and base
            and base.trigger != data.trigger
            and not evidence
        ):
            raise _RefusalError("trigger_without_evidence", "citations")
        grounding = (*evidence, *topic_evidence)
        if not isinstance(data, AddEdge):
            self._reasoning(data.basis, data.reasoning, data.confidence, grounding)
        owner, owed_to = self._shape(data)
        state = self._state(proposal, kind)
        source_ref = _source_reference(data, grounding)
        candidate = RowFacts(
            data.id, kind, data.basis, state, self.context.unit, source_ref=source_ref,
            form=data.form if isinstance(data, CreateIntention) else None,
        )
        links, endpoints = self._relations(data, candidate, evidence, base, survivor)
        if isinstance(data, CreateEntity) and not topic_evidence and (
            not evidence or not any(link.type != "cites" for link in links)
        ):
            raise _RefusalError("orphan_entity", "links")
        row = self._scope(data, kind, state, (*grounding, *endpoints), base)
        row = replace(
            row,
            source_ref=source_ref,
            dependencies=tuple(
                link.target
                for link in links
                if link.source == row.id and link.type in ("draws_on", "rests_on")
            ),
        )
        return Admitted(proposal, row, links, base, survivor, owner, owed_to)

    def _get(self, ref: str, field: str, *, reason: FormRefusal) -> RowFacts:
        row = self.rows.get(ref)
        if ref not in self.shown or row is None:
            raise _RefusalError(reason, field)
        if row.unit != self.context.unit:
            raise _RefusalError("wrong_unit", field)
        return row

    def _transition(self, proposal: Proposal) -> tuple[RowFacts | None, RowFacts | None]:
        if isinstance(proposal, MergeEntities):
            source = self._get(proposal.source, "source", reason="invalid_merge")
            target = self._get(proposal.survivor, "survivor", reason="invalid_merge")
            for field, row in (("source", source), ("survivor", target)):
                if row.pointer_kind is not None:
                    raise _RefusalError("pointer_entity", field)
                if row.kind != "entity" or row.basis != "inferred" or row.state != "admitted":
                    raise _RefusalError("invalid_merge", field)
            if source.id == target.id:
                raise _RefusalError("invalid_merge", "survivor")
            return source, target
        if not isinstance(proposal, (Revise, Retire)):
            return None, None
        base = self._get(proposal.base, "base", reason="illegal_transition")
        if base.pointer_kind is not None:
            raise _RefusalError("pointer_entity", "base")
        states = _REVISION_STATES if isinstance(proposal, Revise) else _RETIRE_STATES
        if base.state not in states.get(base.kind, ()):
            raise _RefusalError("illegal_transition", "base")
        if isinstance(proposal, Revise):
            if getattr(proposal.replacement, "kind", None) != base.kind:
                raise _RefusalError("illegal_transition", "kind")
            if isinstance(proposal.replacement, CreateIntention) and (
                proposal.replacement.form != base.form
            ):
                raise _RefusalError("illegal_transition", "form")
        elif base.kind == "intention":
            if proposal.ending not in _ENDINGS.get(base.form or "", ()):
                raise _RefusalError("illegal_transition", "ending")
        elif proposal.ending is not None:
            raise _RefusalError("illegal_transition", "ending")
        return base, None

    @staticmethod
    def _state(proposal: Proposal, kind: graph.Kind) -> str:
        if isinstance(proposal, Retire):
            return "ended" if kind == "intention" else "retired"
        return {"entity": "admitted", "intention": "open"}.get(kind, "live")

    def _citations(self, data: ProposalFields, kind: graph.Kind) -> tuple[RowFacts, ...]:
        evidence = tuple(
            self._get(ref, "citations", reason="citation_not_shown")
            for ref in dict.fromkeys(data.citations)
        )
        for row in evidence:
            self._check_citation(kind, data.basis, row, "citations")
        return evidence

    def _check_citation(self, kind: str, basis: str, row: RowFacts, field: str) -> None:
        if not graph.endpoints_legal("cites", kind, row.kind) or not graph.cites_legal(
            basis, graph.Cited(row.kind, row.basis)
        ):
            raise _RefusalError("unrooted_citation", field)
        pending = list(row.dependencies)
        seen = {row.id}
        while pending:
            ref = pending.pop()
            if ref in seen:
                continue
            seen.add(ref)
            reached = self.rows.get(ref)
            if (
                reached is None
                or reached.unit != self.context.unit
                or (reached.basis not in ("observed", "computed"))
            ):
                raise _RefusalError("unrooted_citation", field)
            pending.extend(reached.dependencies)

    @staticmethod
    def _reasoning(
        basis: str,
        reasoning: str | None,
        confidence: str | None,
        evidence: tuple[RowFacts, ...],
    ) -> None:
        if (basis == "inferred" and reasoning not in get_args(graph.Reasoning)) or (
            basis != "inferred" and reasoning is not None
        ):
            raise _RefusalError("invalid_reasoning", "reasoning")
        if (basis == "inferred" and confidence not in get_args(graph.Confidence)) or (
            basis != "inferred" and confidence is not None
        ):
            raise _RefusalError("invalid_confidence", "confidence")
        if reasoning == "told" and not any(
            row.kind == "item"
            and row.source in _SPEECH_SOURCES
            and isinstance(row.author, PersonAuthor)
            for row in evidence
        ):
            raise _RefusalError("told_without_person", "citations")

    def _person(self, ref: str, field: str) -> str:
        value = self.refs.persons.get(ref)
        if value is None:
            raise _RefusalError("unresolved_person", field)
        return value

    def _shape(self, data: ProposalFields) -> tuple[str | None, str | None]:
        if isinstance(data, CreateEntity):
            if data.pointer_kind is not None or data.pointer_ref is not None:
                raise _RefusalError("pointer_entity", "pointer_kind")
            if not data.name.strip():
                raise _RefusalError("invalid_field", "name")
        if isinstance(data, CreateClaim):
            prediction = data.prediction is not None
            window = data.window_end is not None
            if (
                data.content is None
                or data.part not in get_args(graph.ClaimPart)
                or ((prediction, window) != ((data.part == "guess"), (data.part == "guess")))
            ):
                raise _RefusalError("invalid_field", "claim")
        if isinstance(data, CreateIntention):
            self._intention_shape(data)
            return self._person(data.owner, "owner"), self._person(data.owed_to, "owed_to")
        return None, None

    @staticmethod
    def _intention_shape(data: CreateIntention) -> None:
        if data.form not in get_args(graph.IntentionForm):
            raise _RefusalError("invalid_field", "form")
        forms = graph.DoneWhenForm if data.form == "achieve" else graph.ConditionForm
        if data.condition.form not in get_args(forms) or data.condition.value is None:
            raise _RefusalError("invalid_field", "condition")
        if data.drop_when and (
            data.drop_when.form not in get_args(graph.ConditionForm) or data.drop_when.value is None
        ):
            raise _RefusalError("invalid_field", "drop_when")
        if data.condition.form == "untyped":
            if data.form == "achieve" and data.deadline is None:
                raise _RefusalError("invalid_field", "deadline")
            if data.form == "maintain" and (
                not data.drop_when or data.drop_when.form != "deadline"
            ):
                raise _RefusalError("invalid_field", "drop_when")
        if data.delivery_mode not in get_args(graph.DeliveryMode) or (
            (data.delivery_mode == "choose_moment") != (data.trigger is not None)
        ):
            raise _RefusalError("invalid_field", "trigger")

    def _scope(
        self,
        data: ProposalFields,
        kind: graph.Kind,
        state: str,
        inputs: tuple[RowFacts, ...],
        base: RowFacts | None,
    ) -> RowFacts:
        scope = data.scope
        room, dm = self.context.room, self.context.room_is_dm
        if base is not None:
            room, dm = base.origin_room, base.room_is_dm
        if scope.origin_room is not None:
            answer = self.refs.rooms.get(scope.origin_room)
            if answer is None:
                raise _RefusalError("unresolved_room", "origin_room")
            if answer.unit != self.context.unit:
                raise _RefusalError("wrong_unit", "origin_room")
            room, dm = answer.id, answer.is_dm
        ranked = ExposureOrder
        if scope.exposure not in ranked:
            raise _RefusalError("invalid_field", "exposure")
        excluded = tuple(self._person(ref, "excluded_persons") for ref in scope.excluded_persons)
        inherited = (*inputs, base) if base is not None else inputs
        private_rooms = {r.origin_room for r in inherited if r.room_is_dm}
        if len(private_rooms) > 1:
            raise _RefusalError("scope_widening", "origin_room")
        if private_rooms:
            room, dm = next(iter(private_rooms)), True
            if room is None:
                raise _RefusalError("unresolved_room", "origin_room")
        restrictions = inherited_scope(
            (
                InputScope(r.excluded_persons, cast(Exposure, r.exposure), _lineage(r))
                for r in inherited
            ),
            build_lineage=Lineage(),
            minimum="external",
        )
        if ranked.index(scope.exposure) < ranked.index(restrictions.exposure):
            raise _RefusalError("scope_widening", "exposure")
        if not set(restrictions.excluded_persons).issubset(excluded):
            raise _RefusalError("scope_widening", "excluded_persons")
        persons = tuple(self.refs.persons.get(ref) for ref in scope.persons)
        return RowFacts(
            data.id,
            kind,
            data.basis,
            state,
            self.context.unit,
            room,
            dm,
            persons,
            excluded,
            scope.exposure,
            restrictions.lineage,
            form=data.form if isinstance(data, CreateIntention) else None,
            trigger=data.trigger if isinstance(data, CreateIntention) else None,
            delivery_mode=data.delivery_mode if isinstance(data, CreateIntention) else None,
        )

    def _relations(
        self,
        data: ProposalFields,
        row: RowFacts,
        evidence: tuple[RowFacts, ...],
        base: RowFacts | None,
        survivor: RowFacts | None,
    ) -> tuple[tuple[Link, ...], tuple[RowFacts, ...]]:
        links: tuple[Link, ...]
        endpoints: tuple[RowFacts, ...]
        if isinstance(data, MergeEntities) and base is not None and survivor is not None:
            links = (
                Link(
                    type="merged_into",
                    source=base.id,
                    target=survivor.id,
                    basis="inferred",
                    reasoning=data.reasoning,
                    confidence=data.confidence,
                ),
            )
            endpoints = (survivor,)
        else:
            links = _links(data)
            endpoints = tuple(
                endpoint
                for link in links
                for endpoint in self._relation(data, row, link, evidence)
                if endpoint.id != row.id
            )
        if evidence:
            src = base.id if survivor is not None and base else row.id
            links += tuple(
                Link(
                    type="cites",
                    source=src,
                    target=target.id,
                    basis=data.basis,
                    reasoning=data.reasoning,
                    confidence=data.confidence,
                )
                for target in evidence
            )
        return links, endpoints

    def _relation(
        self,
        data: ProposalFields,
        row: RowFacts,
        link: Link,
        evidence: tuple[RowFacts, ...],
    ) -> tuple[RowFacts, RowFacts]:
        if link.type not in graph.EDGES:
            raise _RefusalError("unknown_edge_type", "type")
        if link.type == "merged_into" or (
            not isinstance(data, AddEdge)
            and (row.id not in (link.source, link.target) or link.type == "cites")
        ):
            raise _RefusalError("illegal_endpoints", "links")
        source = (
            row
            if link.source == row.id
            else self._get(
                link.source,
                "source",
                reason="illegal_endpoints",
            )
        )
        target = (
            row
            if link.target == row.id
            else self._get(
                link.target,
                "target",
                reason="illegal_endpoints",
            )
        )
        if not graph.endpoints_legal(link.type, source.kind, target.kind):
            raise _RefusalError("illegal_endpoints", "target")
        if link.type == "about":
            if isinstance(data, AddEdge):
                allowed = source.kind == "item"
            else:
                allowed = source.id == row.id and row.kind in ("claim", "intention")
            if not allowed:
                raise _RefusalError("illegal_endpoints", "source")
        if link.type == "watches":
            if source.form != "maintain" or source.state != "open":
                raise _RefusalError("illegal_endpoints", "source")
            if (target.state != "admitted" or target.source_ref is None
                    or target.source_ref.kind not in self.watch_kinds):
                raise _RefusalError("unpollable_watch_target", "target")
        if not graph.basis_legal(link.type, link.basis):
            raise _RefusalError("illegal_basis", "basis")
        if link.basis not in _OCCASIONS.get(self.context.occasion, ()):
            raise _RefusalError("occasion_basis", "basis")
        if link.type == "cites":
            self._check_citation(source.kind, source.basis, target, "target")
            exposure = ExposureOrder
            if exposure.index(source.exposure) < exposure.index(target.exposure) or (
                not set(target.excluded_persons).issubset(source.excluded_persons)
                or _lineage(source).union(_lineage(target)) != _lineage(source)
                or (target.room_is_dm and source.origin_room != target.origin_room)
            ):
                raise _RefusalError("scope_widening", "source")
            evidence = (target,)
        if link.type == "rests_on" and not graph.rests_on_legal(
            source.basis, graph.Cited(target.kind, target.basis)
        ):
            raise _RefusalError("illegal_basis", "source")
        self._reasoning(link.basis, link.reasoning, link.confidence, evidence)
        return source, target


def _source_reference(
    data: ProposalFields, evidence: tuple[RowFacts, ...],
) -> SourceReference | None:
    if not isinstance(data, CreateEntity) or data.source_ref is None:
        return None
    reference = data.source_ref
    if reference.kind not in get_args(SourceKind) or not reference.value.strip():
        raise _RefusalError("invalid_field", "source_ref")
    if reference.kind != "page":
        return reference
    # ponytail: a previously fetched URL can contain private text. The watch
    # only repeats the URL that web_fetch already sent.
    for item in evidence:
        if item.state == "live" and item.fetched_url == reference.value:
            return SourceReference("page", item.fetched_url)
    raise _RefusalError("unbacked_page_reference", "source_ref")


def _lineage(row: RowFacts) -> Lineage:
    if row.origin_room is not None and not row.lineage.sections:
        return Lineage.of({ROW: (unresolved(row.origin_room),)})
    return row.lineage
