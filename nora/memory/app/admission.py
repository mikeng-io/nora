"""Protect model output, decide its form, and commit only admitted proposals."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import fields as data_fields
from dataclasses import is_dataclass, replace
from urllib.parse import unquote

from nora.agent.egress import LeakGuard, scan_identifiers, scan_reply
from nora.agent.security import looks_like_coordinate
from nora.memory.app.admission_identity import AdmissionIdentity
from nora.memory.domain.admission import (
    Refusal,
    decide_admission,
    fields,
    pending_topics,
    row_references,
    topic_targets,
)
from nora.memory.domain.admission_ports import (
    AdmissionResult as AdmissionResult,
)
from nora.memory.domain.admission_ports import (
    AdmissionStore,
    AdmissionUnavailableError,
    Stored,
)
from nora.memory.domain.admission_ports import (
    Unavailable as Unavailable,
)
from nora.memory.domain.admission_proposals import (
    CloseIntention,
    CreateIntention,
    MergeEntities,
    PassContext,
    Proposal,
    ProposalFields,
    ResolvedReferences,
    Retire,
    Revise,
    SourceReference,
)
from nora.memory.domain.graph import StaleBaseError


class AdmissionService:
    def __init__(
        self,
        store: AdmissionStore,
        identity: AdmissionIdentity,
        leak_guard: LeakGuard,
        *,
        content_checks: bool,
        watch_kinds: Collection[str] = (),
    ) -> None:
        self._store = store
        self._identity = identity
        self._leak_guard = leak_guard
        self._content_checks = content_checks
        self._watch_kinds = watch_kinds

    async def admit(
        self,
        proposals: Iterable[Proposal],
        context: PassContext,
        shown: Iterable[str],
    ) -> AdmissionResult:
        """Process each proposal once, with only confirmed writes visible to its siblings."""
        proposals = tuple(proposals)
        screened = [self._protect(proposal) for proposal in proposals]
        try:
            context_or_refusal = await self._context(context)
        except AdmissionUnavailableError as exc:
            unavailable = tuple(
                refusal or Unavailable(fields(proposal).id, exc.commit_uncertain)
                for proposal, refusal in zip(proposals, screened, strict=True)
            )
            return AdmissionResult(unavailable, (), ())
        if isinstance(context_or_refusal, Refusal):
            refused = tuple(refusal or context_or_refusal for refusal in screened)
            return AdmissionResult(refused, (), ())
        context = context_or_refusal
        shown_ids = set(shown)
        unknown: set[str] = set()
        outcomes: dict[int, Stored | Refusal | Unavailable] = {
            index: refusal for index, refusal in enumerate(screened) if refusal is not None
        }
        pending = pending_topics({
            index: proposal for index, proposal in enumerate(proposals) if index not in outcomes
        })
        deferred = set(pending.values())
        for index, proposal in enumerate(proposals):
            if index in outcomes or index in deferred:
                continue
            topics = tuple(
                proposals[pending[key]] for key in topic_targets(proposal) if key in pending
            )
            outcome, related = await self._try(proposal, context, shown_ids, unknown, topics)
            outcomes[index] = outcome
            if isinstance(outcome, Unavailable) and outcome.commit_uncertain:
                unknown.add(outcome.id)
                if isinstance(proposal, (Revise, Retire)):
                    unknown.add(proposal.base)
                elif isinstance(proposal, MergeEntities):
                    unknown.add(proposal.source)
                for topic in related:
                    unknown.add(topic.id)
                    if topic.base is not None:
                        unknown.add(topic.base)
                    outcomes[pending.pop(topic.id)] = Unavailable(topic.id, commit_uncertain=True)
            if isinstance(outcome, Stored):
                shown_ids.add(outcome.id)
                for topic in related:
                    shown_ids.add(topic.id)
                    outcomes[pending.pop(topic.id)] = topic
        for index in pending.values():
            outcomes[index], _ = await self._try(proposals[index], context, shown_ids, unknown)
        ordered = tuple(outcomes[index] for index in range(len(proposals)))
        revisions = tuple(outcome for proposal, outcome in zip(proposals, ordered, strict=True)
                          if isinstance(proposal, Revise) and isinstance(outcome, Stored))
        retirements = tuple(outcome for proposal, outcome in zip(proposals, ordered, strict=True)
                            if isinstance(proposal, Retire) and isinstance(outcome, Stored))
        return AdmissionResult(ordered, revisions, retirements)

    async def _context(self, context: PassContext) -> PassContext | Refusal:
        if self._identity.resolve_admission_unit(context.unit) is None:
            return Refusal("wrong_unit", "unit")
        if context.room is None:
            return context
        room = await self._identity.room(context.room, context.unit)
        if room is None:
            return Refusal("unresolved_room", "origin_room")
        if room.unit != context.unit:
            return Refusal("wrong_unit", "origin_room")
        return replace(context, room=room.id, room_is_dm=context.room_is_dm or room.is_dm)

    async def _try(
        self,
        proposal: Proposal,
        context: PassContext,
        shown: set[str],
        unknown: set[str],
        topics: tuple[Proposal, ...] = (),
    ) -> tuple[Stored | Refusal | Unavailable, tuple[Stored, ...]]:
        data = fields(proposal)
        if not isinstance(data, ProposalFields):
            return Refusal("unknown_kind", "kind"), ()
        ids = frozenset().union(*(row_references(part) for part in (proposal, *topics)))
        if ids & unknown:
            return Unavailable(data.id, commit_uncertain=False), ()
        if isinstance(proposal, CloseIntention):
            return await self._close(proposal, context, shown), ()
        # IDs supplied by the model grant no additional reads.
        readable = ids & shown
        related: tuple[Stored, ...] = ()
        inputs = (data, *(fields(topic) for topic in topics))
        try:
            rooms, persons = await self._identity.references(
                context.unit,
                rooms=tuple(part.scope.origin_room for part in inputs
                            if part.scope.origin_room is not None),
                persons=tuple(ref for part in inputs
                              for ref in (*part.scope.persons, *part.scope.excluded_persons)),
                principals=(data.owner, data.owed_to) if isinstance(data, CreateIntention) else (),
            )
            rows = await self._store.read(context.unit, frozenset(readable))
            choice = decide_admission(
                proposal,
                context,
                shown,
                rows,
                ResolvedReferences(rooms, persons),
                watch_kinds=self._watch_kinds,
                topics=topics,
            )
            if isinstance(choice, Refusal):
                return choice, ()
            references = [part.row.source_ref for part in (choice, *choice.topics)]
            references.extend(
                rows[link.target].source_ref
                for link in choice.links if link.type == "watches" and link.target != choice.row.id
            )
            if any(_unsafe_page(ref) for ref in references):
                return Refusal("unsafe_page_reference", "source_ref"), ()
            stored = Stored(choice.row.id, choice.base.id if choice.base is not None else None)
            related = tuple(Stored(topic.row.id, topic.base.id if topic.base else None)
                            for topic in choice.topics)
            await self._store.persist_admitted_proposal(choice, context, stored)
        except StaleBaseError:
            return Refusal("stale_base", "base"), ()
        except AdmissionUnavailableError as exc:
            return Unavailable(data.id, exc.commit_uncertain), related
        return stored, related

    async def _close(
        self, proposal: CloseIntention, context: PassContext, shown: set[str],
    ) -> Stored | Refusal | Unavailable:
        if proposal.witness not in shown:
            return Refusal("citation_not_shown", "witness")
        if proposal.id not in shown:
            return Refusal("stale_base", "base")
        try:
            return await self._store.close(proposal.id, proposal.witness, context)
        except AdmissionUnavailableError as exc:
            return Unavailable(proposal.id, exc.commit_uncertain)

    def _protect(self, proposal: Proposal) -> Refusal | None:
        for field, text in _text(proposal):
            if scan_identifiers(text, content_checks=False):
                return Refusal("credential", field)
            if self._leak_guard.check(text):
                return Refusal("doctrine", field)
            if self._content_checks and scan_reply(text, content_checks=True):
                return Refusal("content", field)
        return None


def _unsafe_page(ref: SourceReference | None) -> bool:
    if ref is None or ref.kind != "page":
        return False
    decoded = unquote(ref.value)
    return scan_identifiers(decoded) is not None or looks_like_coordinate(decoded)


def _text(value: object, field: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield field, value
    elif is_dataclass(value) and not isinstance(value, type):
        for entry in data_fields(value):
            if entry.name != "provenance":
                name = f"{field}.{entry.name}" if field else entry.name
                yield from _text(getattr(value, entry.name), name)
    elif isinstance(value, Mapping):
        for key, inner in value.items():
            yield from _text(key, field)
            if isinstance(key, str) and type(inner) in (int, float):
                yield field, f"{key}: {inner}"
            yield from _text(inner, field)
    elif isinstance(value, (tuple, list)):
        if len(value) == 2 and all(type(x) in (int, float) for x in value):
            yield field, f"{value[0]}, {value[1]}"
        for inner in value:
            yield from _text(inner, field)
