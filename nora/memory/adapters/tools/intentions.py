"""Model surface for committing intentions through admission."""

from collections.abc import Awaitable, Callable, Collection
from typing import Annotated, Any

from pydantic import PlainSerializer, WrapValidator

from nora.agent.toolkit.results import _record
from nora.memory.app.act_scope import active_act_authority, pass_created_intentions, pass_shown
from nora.memory.app.admission import AdmissionService, Unavailable
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_ports import Stored
from nora.memory.domain.admission_proposals import (
    CloseIntention,
    CreateIntention,
    PassContext,
    Proposal,
    Retire,
    Revise,
)
from nora.memory.domain.intention_operations import (
    IntentionOperation,
    IntentionProposal,
    limit_to_delivery,
    parse_intention_operation,
)
from nora.obs.metrics import Metrics

_ENTRY_NOTES = {
    "admitted": "The operation was admitted.",
    "inactive": "The operation could not be submitted.",
    "unavailable": "The operation could not be recorded.",
    "commit_uncertain": "Read back the intention before retrying.",
}

_REFUSALS = {
    "room_busy": "This room is in an engagement pass. Leave the change for a later wake.",
    "not_this_delivery": "Only change the intention this pass delivers.",
    "passive_commit_only": "This pass can open an intention without scheduling a delivery, "
        "or revise one it opened in this pass.",
    "invalid_field": "Fix the named field and submit that operation again.",
    "invalid_trigger": "Use a time or an act_event, person_event or predicate condition.",
    "citation_not_shown": "Use a citation from this pass's context or graph reads.",
    "unrooted_citation": "Cite a graph row grounded in observation or computation.",
    "illegal_endpoints": "Check the referenced IDs and their row kinds.",
    "illegal_basis": "Use a row that supports the requested relation.",
    "unresolved_person": "Use a known person ID for the named field.",
    "illegal_transition": "Read the intention and check which operations it permits.",
    "pointer_entity": "Use an intention ID for the base.",
    "duplicate_delivery": "Read the conflicting intention before scheduling another delivery.",
    "stale_base": "Read the current intention and use its current ID.",
    "trigger_without_evidence": "Cite what warrants changing the trigger.",
    "scope_widening": "Use evidence and links that share the intended scope.",
    "witness_not_met": "Use the observed witness that settles this condition.",
    "untyped_condition": "An untyped condition needs a typed revision before a mechanical close.",
    "not_open": "This intention has already settled.",
    "credential": "Remove credentials from the named field.",
    "doctrine": "Use only information safe to record here.",
    "content": "Remove protected content from the named field.",
}

# Strands validates the list. Admission parses each entry and preserves malformed siblings.
_OperationInput = Annotated[
    IntentionOperation,
    WrapValidator(lambda raw, _validate: raw),
    PlainSerializer(lambda operation: operation, return_type=Any),
]


def _make_intention_commit(deps):
    admission, metrics = deps.intention_admission, deps.metrics

    async def intention_commit(operations: list[_OperationInput]) -> dict:
        """Commit intentions and return a result for each operation. Call this
        when a commitment needs to be opened, revised, dropped or closed.

        On a call or an offered floor, commit only what your reply promises.
        A proactive pass without a delivered intention can open passive
        intentions and revise them during the same pass.
        For choose_moment, use a time trigger or a condition trigger with the
        act_event, person_event or predicate form.
        Returns `status`: ok, refused or unavailable, with a `note`.

        Args:
            operations: Intention operations in order. Give person IDs or nora for owner
                and owed_to. Use graph IDs shown in this pass for references.
        """
        return await submit_operations(
            operations, admission, metrics, "intention_commit", _parse_commit,
        )

    return intention_commit


def _parse_commit(operations: list, context: PassContext) -> list[Proposal | Refusal]:
    authority = active_act_authority()
    delivery = authority.intention if authority is not None else None
    parsed = [parse_intention_operation(raw, context) for raw in operations]
    if delivery is not None:
        return [
            entry if isinstance(entry, Refusal) else limit_to_delivery(entry, delivery) or entry
            for entry in parsed
        ]
    if context.occasion == "proactive":
        created = pass_created_intentions() or ()
        return [
            entry if isinstance(entry, Refusal) else _limit_passive_commit(entry, created) or entry
            for entry in parsed
        ]
    return list(parsed)


def _limit_passive_commit(entry: IntentionProposal, created: Collection[str]) -> Refusal | None:
    if isinstance(entry, (Retire, CloseIntention)):
        return Refusal("passive_commit_only", "op")
    if isinstance(entry, Revise) and entry.base not in created:
        return Refusal("passive_commit_only", "base")
    intention = entry.replacement if isinstance(entry, Revise) else entry
    if not isinstance(intention, CreateIntention):
        return Refusal("invalid_field", "op")
    if intention.rooms:
        return Refusal("passive_commit_only", "rooms")
    if intention.delivery_mode != "when_it_comes_up":
        return Refusal("passive_commit_only", "delivery")
    return None


async def submit_operations(
    operations: list, admission: AdmissionService | None, metrics: Metrics, name: str,
    parse: Callable[[list, PassContext], list[Proposal | Refusal]], *,
    rediscover: Callable[[str], Awaitable[dict]] | None = None,
    room_busy: Callable[[], None] | None = None,
) -> dict:
    authority = active_act_authority()
    context = authority.pass_context if authority is not None else None
    shown = pass_shown()
    if admission is None or authority is None or context is None or shown is None:
        _record(metrics, name, "unavailable")
        return {
            "status": "unavailable",
            "operations": [
                {
                    "status": "unavailable", "commit_uncertain": False,
                    "note": _ENTRY_NOTES["inactive"],
                }
                for _ in operations
            ],
            "note": ("The intention store is unavailable." if admission is None
                     else "No active pass can submit operations." if name != "intention_commit"
                         else "No active pass can commit intentions."),
        }

    parsed = parse(operations, context)
    proposals = tuple(entry for entry in parsed if not isinstance(entry, Refusal))
    admitted = await admission.admit(proposals, context, shown) if proposals else None
    admission_outcomes = admitted.outcomes if admitted is not None else ()
    if rediscover is None:
        shown.update(
            key for outcome in admission_outcomes if isinstance(outcome, Stored)
            for key in (outcome.id, *outcome.other_deliveries)
        )
    stored = iter(admission_outcomes)
    created = (
        pass_created_intentions()
        if name == "intention_commit"
        and context.occasion == "proactive" and authority.intention is None
        else None
    )
    outcomes = []
    for entry in parsed:
        outcome = entry if isinstance(entry, Refusal) else next(stored)
        if (created is not None and isinstance(outcome, Stored)
                and isinstance(entry, (CreateIntention, Revise))):
            created.add(outcome.id)
        result = _entry(outcome)
        if isinstance(outcome, Stored) and rediscover is not None:
            result["neighbourhood"] = await rediscover(outcome.id)
            if outcome.other_deliveries:
                result["other_neighbourhoods"] = {
                    id_: await rediscover(id_) for id_ in outcome.other_deliveries
                }
        if isinstance(outcome, Refusal) and outcome.reason == "room_busy" and room_busy:
            room_busy()
        outcomes.append(result)
    statuses = {entry["status"] for entry in outcomes}
    if "unavailable" in statuses:
        _record(metrics, name, "unavailable")
        return {
            "status": "unavailable", "operations": outcomes,
            "note": "Some operations could not be confirmed. Read back uncertain writes.",
        }
    if "refused" in statuses:
        _record(metrics, name, "refused")
        return {
            "status": "refused", "operations": outcomes,
            "note": "Use the operation results to repair individual refusals.",
        }
    _record(metrics, name, "ok")
    return {"status": "ok", "operations": outcomes, "note": "All operations were admitted."}


def _entry(outcome: Stored | Refusal | Unavailable) -> dict:
    if isinstance(outcome, Refusal):
        note = "invalid_trigger" if (outcome.reason, outcome.field) == (
            "invalid_field", "trigger",
        ) else outcome.reason
        return {
            "status": "refused", "reason": outcome.reason, "field": outcome.field,
            "note": _REFUSALS.get(note, "This operation was refused."),
        }
    if isinstance(outcome, Unavailable):
        reason = "commit_uncertain" if outcome.commit_uncertain else "unavailable"
        return {
            "status": "unavailable", "commit_uncertain": outcome.commit_uncertain,
            "note": _ENTRY_NOTES[reason],
        }
    admitted = {"status": "admitted", "id": outcome.id, "note": _ENTRY_NOTES["admitted"]}
    if outcome.other_deliveries:
        return {**admitted, "ids": [outcome.id, *outcome.other_deliveries]}
    return admitted
