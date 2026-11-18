"""Protect model output, decide its form, and commit only admitted proposals."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, is_dataclass, replace
from dataclasses import fields as data_fields

from nora.agent.egress import LeakGuard, scan_identifiers, scan_reply
from nora.memory.app.admission_identity import AdmissionIdentity
from nora.memory.domain.admission import Refusal, admit_batch, fields, row_references
from nora.memory.domain.admission_ports import AdmissionStore, AdmissionUnavailableError
from nora.memory.domain.admission_proposals import (
    CreateIntention,
    MergeEntities,
    PassContext,
    Proposal,
    ProposalFields,
    ResolvedReferences,
    Retire,
    Revise,
)
from nora.memory.domain.graph import StaleBaseError


@dataclass(frozen=True, slots=True)
class Stored:
    id: str
    base: str | None = None


@dataclass(frozen=True, slots=True)
class Unavailable:
    id: str
    commit_uncertain: bool


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    outcomes: tuple[Stored | Refusal | Unavailable, ...]
    revisions: tuple[Stored, ...]
    retirements: tuple[Stored, ...]


class AdmissionService:
    def __init__(
        self,
        store: AdmissionStore,
        identity: AdmissionIdentity,
        leak_guard: LeakGuard,
        *,
        content_checks: bool,
    ) -> None:
        self._store = store
        self._identity = identity
        self._leak_guard = leak_guard
        self._content_checks = content_checks

    async def admit(
        self,
        proposals: Iterable[Proposal],
        context: PassContext,
        shown: Iterable[str],
    ) -> AdmissionResult:
        """Process each proposal once, with only confirmed writes visible to its siblings."""
        proposals = tuple(proposals)
        screened = [self._protect(proposal) for proposal in proposals]
        context_or_refusal = self._context(context)
        if isinstance(context_or_refusal, Refusal):
            outcomes = tuple(refusal or context_or_refusal for refusal in screened)
            return AdmissionResult(outcomes, (), ())
        context = context_or_refusal
        shown_ids = set(shown)
        unknown: set[str] = set()
        outcomes_list: list[Stored | Refusal | Unavailable] = []
        revisions: list[Stored] = []
        retirements: list[Stored] = []
        for proposal, refusal in zip(proposals, screened, strict=True):
            outcome = refusal or await self._try(proposal, context, shown_ids, unknown)
            outcomes_list.append(outcome)
            if isinstance(outcome, Unavailable) and outcome.commit_uncertain:
                unknown.add(outcome.id)
                if isinstance(proposal, (Revise, Retire)):
                    unknown.add(proposal.base)
                elif isinstance(proposal, MergeEntities):
                    unknown.add(proposal.source)
            if isinstance(outcome, Stored):
                shown_ids.add(outcome.id)
                if isinstance(proposal, Revise):
                    revisions.append(outcome)
                elif isinstance(proposal, Retire):
                    retirements.append(outcome)
        return AdmissionResult(tuple(outcomes_list), tuple(revisions), tuple(retirements))

    def _context(self, context: PassContext) -> PassContext | Refusal:
        if self._identity.unit(context.unit) is None:
            return Refusal("wrong_unit", "unit")
        if context.room is None:
            return context
        room = self._identity.room(context.room, context.unit)
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
    ) -> Stored | Refusal | Unavailable:
        data = fields(proposal)
        if not isinstance(data, ProposalFields):
            return Refusal("unknown_kind", "kind")
        ids = row_references(proposal)
        if ids & unknown:
            return Unavailable(data.id, commit_uncertain=False)
        # IDs supplied by the model grant no additional reads.
        readable = ids & shown
        rooms, persons = self._identity.references(
            context.unit,
            rooms=(data.scope.origin_room,) if data.scope.origin_room is not None else (),
            persons=(*data.scope.persons, *data.scope.excluded_persons),
            principals=(data.owner, data.owed_to) if isinstance(data, CreateIntention) else (),
        )
        try:
            rows = await self._store.read(context.unit, frozenset(readable))
            (choice,) = admit_batch(
                (proposal,),
                context,
                shown,
                rows,
                ResolvedReferences(rooms, persons),
            )
            if isinstance(choice, Refusal):
                return choice
            await self._store.write(choice, context)
        except StaleBaseError:
            return Refusal("stale_base", "base")
        except AdmissionUnavailableError as exc:
            return Unavailable(data.id, exc.commit_uncertain)
        return Stored(choice.row.id, choice.base.id if choice.base is not None else None)

    def _protect(self, proposal: Proposal) -> Refusal | None:
        for field, text in _text(proposal):
            if scan_identifiers(text, content_checks=False):
                return Refusal("credential", field)
            if self._leak_guard.check(text):
                return Refusal("doctrine", field)
            if self._content_checks and scan_reply(text, content_checks=True):
                return Refusal("content", field)
        return None


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
