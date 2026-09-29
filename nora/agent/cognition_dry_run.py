"""Cognition's write verbs, in DRY RUN (design/cognition-loop/70-migration.md
step 3a: "its note, event and Standing verbs in dry run (would-write rows
recorded, nothing committed)"; `30-cognition.md` §2's write-verb table).

**Each function here runs the SAME pure validation the live verb runs, and
then records instead of commits.** That is the whole point (`30-cognition.md`
§2: "a note or event written by cognition and one written on a tag turn pass
the same validator", extended to the shadow path):

- note: `nora.domain.notes.NoteCreationProposal` / `NoteRevisionProposal`
  (the title/description/anchor/body-length invariants `Note.__post_init__`
  and the curator's sink both hold callers to) plus
  `nora.domain.note_body.parse_note_body` (the body's schema, §4 of
  `12-schemas.md`'s note section);
- event: `nora.domain.gatherings.Gathering.__post_init__`
  (`validate_event_field_mode`/`validate_event_field_confidence`) plus
  `nora.domain.cognition_write.stale_event_base` (mirrors
  `StaleEventRevisionError` without a store);
- standing: `nora.domain.standing_revise.check_schema`/`check_mode` plus
  `check_stale_base` — literally the same three functions
  `nora.infra.sql.standing_revise.standing_revise` (the LIVE verb, step 3b)
  calls, imported here rather than re-implemented.

**None of these functions ever calls a live store's mutating method.** Each
takes the store it would otherwise call (`NoteCollaborator`-shaped,
`GatheringStore`, `StandingStore`) only for its READ methods (`prior`/
`current` are passed in explicitly by the caller instead, the same choice
`nora.infra.sql.standing_revise` made for `prior` — see its module
docstring for why a hidden second read is the wrong shape for a bounded
session) — this module makes NO read either. It is pure aside from the one
`CognitionDryRunStore.record_would_write` call each function ends with.
Nothing here can send: see
`tests/architecture/test_cognition_shadow_cannot_write_or_send.py` for what
importing this module is and is not allowed to reach.

**A simplification, stated rather than hidden.** The tag path's own
`note_create`/`note_update` (`nora.agent.tools._held_to_schema`) run
`resolve_positions` before `parse_note_body`, remapping a model's `[#n]`
citation indices to the transcript row ids the turn actually rendered. That
remap is a detail of how a TURN'S window is built, not of what the schema
requires, so it is not duplicated here: `body` is expected already
resolved (`shown.rendered` ids used directly as citations) — a session
builds `Shown` from its own item's window the same way a turn does, and
that wiring is the caller's job (`nora.app.cognition_session`, out of
3a-i's scope per `70-migration.md`'s own step split). A body that would be
refused by `parse_note_body` after remap is refused here too; what this
module cannot yet exercise is a citation that is only valid THROUGH the
remap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, replace

from nora.domain.cognition_write import WouldWrite, stale_event_base
from nora.domain.gatherings import (
    Gathering,
    InvalidEventFieldModeError,
    validate_event_field_confidence,
    validate_event_field_mode,
)
from nora.domain.note_body import NoteBodyError, Shown, parse_note_body
from nora.domain.notes import NoteCreationProposal, NoteRevisionProposal
from nora.domain.ports import CognitionDryRunStore, NewCognitionWouldWrite
from nora.domain.standing import Standing, WorkingSetItem
from nora.domain.standing_revise import check_mode, check_schema, check_stale_base

__all__ = [
    "dry_run_event_propose",
    "dry_run_event_revise",
    "dry_run_event_set_status",
    "dry_run_note_create",
    "dry_run_note_update",
    "dry_run_standing_revise",
]


async def _record(
    ledger: CognitionDryRunStore, *, session_id: int | None, unit: str, write: WouldWrite,
    now_ms: int,
) -> WouldWrite:
    await ledger.record_would_write(
        write=NewCognitionWouldWrite(
            session_id=session_id, unit=unit, tier=write.tier, verb=write.verb,
            platform=write.platform, conversation_id=write.conversation_id,
            args=write.args, outcome=write.outcome,
            refusal_reason=write.refusal_reason, refusal_detail=write.refusal_detail,
            candidate=write.candidate, base_revision_id=write.base_revision_id,
        ),
        recorded_at_ms=now_ms,
    )
    return write


# --------------------------------------------------------------------------
# note
# --------------------------------------------------------------------------

async def dry_run_note_create(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, title: str, description: str,
    uncertainty: str, closing_condition: str, body: str, established_by: str,
    now_ms: int, anchor_date: int | None = None,
    anchor_entity_ids: tuple[str, ...] = (), shown: Shown, revisit_at: int | None = None,
) -> WouldWrite:
    """`note_create` in shadow. Args mirror
    `nora.domain.notes.NoteCreationProposal` plus `established_by`/`now_ms`
    (provenance the live verb stamps, not part of the proposal's own
    invariants) and `shown` (the body's schema check, `parse_note_body`)."""
    args: dict[str, object] = {
        "title": title, "description": description, "uncertainty": uncertainty,
        "closing_condition": closing_condition, "body": body,
        "established_by": established_by, "anchor_date": anchor_date,
        "anchor_entity_ids": list(anchor_entity_ids),
    }
    try:
        parsed = parse_note_body(body, shown=shown, require_open=True)
    except NoteBodyError as exc:
        write = WouldWrite(
            tier="note", verb="note_create", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=exc.rule, refusal_detail=str(exc),
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    try:
        proposal = NoteCreationProposal(
            title=title, description=description, uncertainty=uncertainty,
            closing_condition=closing_condition, established_in=conversation_id,
            body=parsed.text, anchor_date=anchor_date,
            anchor_entity_ids=anchor_entity_ids, revisit_at=revisit_at,
        )
    except ValueError as exc:
        write = WouldWrite(
            tier="note", verb="note_create", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason="invalid_note", refusal_detail=str(exc),
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    candidate = {
        "unit": unit, "title": proposal.title, "description": proposal.description,
        "uncertainty": proposal.uncertainty,
        "closing_condition": proposal.closing_condition,
        "established_in": proposal.established_in, "body": proposal.body,
        "established_by": established_by, "now_ms": now_ms,
        "anchor_date": proposal.anchor_date,
        "anchor_entity_ids": list(proposal.anchor_entity_ids),
    }
    write = WouldWrite(
        tier="note", verb="note_create", platform=platform,
        conversation_id=conversation_id, args=args, outcome="would_commit",
        candidate=candidate,
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)


async def dry_run_note_update(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, note_id: str, body: str,
    parent_revision_id: str, established_by: str, now_ms: int,
    shown: Shown, entity_ids: tuple[str, ...] = (),
) -> WouldWrite:
    """`note_update`/`append_revision` in shadow."""
    args: dict[str, object] = {
        "note_id": note_id, "body": body, "parent_revision_id": parent_revision_id,
        "established_by": established_by, "entity_ids": list(entity_ids),
    }
    try:
        parsed = parse_note_body(body, shown=shown)
    except NoteBodyError as exc:
        write = WouldWrite(
            tier="note", verb="note_update", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=exc.rule, refusal_detail=str(exc),
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    try:
        proposal = NoteRevisionProposal(
            note_id=note_id, body=parsed.text,
            parent_revision_id=parent_revision_id, entity_ids=entity_ids,
        )
    except ValueError as exc:
        write = WouldWrite(
            tier="note", verb="note_update", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason="invalid_revision", refusal_detail=str(exc),
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    candidate = {
        "unit": unit, "note_id": proposal.note_id, "body": proposal.body,
        "parent_revision_id": proposal.parent_revision_id,
        "entity_ids": list(proposal.entity_ids),
        "established_by": established_by, "now_ms": now_ms,
    }
    write = WouldWrite(
        tier="note", verb="note_update", platform=platform,
        conversation_id=conversation_id, args=args, outcome="would_commit",
        candidate=candidate, base_revision_id=parent_revision_id,
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)


# --------------------------------------------------------------------------
# event
# --------------------------------------------------------------------------

def _event_candidate(event: Gathering) -> dict[str, object]:
    row = asdict(event)
    row["id"] = str(event.id)
    row["when"] = None if event.when is None else event.when.isoformat()
    row["end"] = None if event.end is None else event.end.isoformat()
    row["created_at"] = event.created_at.isoformat()
    row["venue_resolved_at"] = (
        None if event.venue_resolved_at is None else event.venue_resolved_at.isoformat()
    )
    row["status"] = str(event.status)
    row["head_revision_id"] = (
        None if event.head_revision_id is None else str(event.head_revision_id)
    )
    return row


async def dry_run_event_propose(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, now_ms: int,
    build: type[Gathering] = Gathering, **event_kwargs: object,
) -> WouldWrite:
    """`event_propose` in shadow. `event_kwargs` are `Gathering`'s own
    constructor arguments (`id`, `title`, `created_by`, `when`, `status`,
    `created_at`, `unit_id`, ...) — this function does not narrow the
    signature further than `Gathering.__post_init__` already does; the
    caller (a session wiring, or a test) builds the row the way
    `GatheringService.create_event` builds one today, then hands it here
    instead of `self._store.create(event)`."""
    args = {
        k: (str(v) if hasattr(v, "isoformat") or k in ("id",) else v)
        for k, v in event_kwargs.items()
    }
    try:
        event = build(conversation_id=conversation_id, unit_id=unit, **event_kwargs)
    except InvalidEventFieldModeError as exc:
        write = WouldWrite(
            tier="event", verb="event_propose", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=f"bad_{exc.field}", refusal_detail=str(exc),
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    write = WouldWrite(
        tier="event", verb="event_propose", platform=platform,
        conversation_id=conversation_id, args=args, outcome="would_commit",
        candidate=_event_candidate(event),
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)


async def dry_run_event_revise(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, current: Gathering | None,
    base_revision_id: str | None, now_ms: int, **field_changes: object,
) -> WouldWrite:
    """`event_revise` in shadow. `current` is the row the caller already
    read (the same "prior is a parameter" choice `standing_revise` makes,
    §3b): this function never reads a store."""
    args = {"base_revision_id": base_revision_id, **field_changes}
    current_head = None if current is None else (
        None if current.head_revision_id is None else str(current.head_revision_id)
    )
    if stale_event_base(base_revision_id=base_revision_id, current_head_revision_id=current_head):
        write = WouldWrite(
            tier="event", verb="event_revise", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason="stale_base",
            refusal_detail=f"current head is {current_head!r}",
            base_revision_id=base_revision_id,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    if current is None:
        write = WouldWrite(
            tier="event", verb="event_revise", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason="not_found", base_revision_id=base_revision_id,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    try:
        updated = replace(current, **field_changes)  # type: ignore[arg-type]
    except InvalidEventFieldModeError as exc:
        write = WouldWrite(
            tier="event", verb="event_revise", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=f"bad_{exc.field}", refusal_detail=str(exc),
            base_revision_id=base_revision_id,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    write = WouldWrite(
        tier="event", verb="event_revise", platform=platform,
        conversation_id=conversation_id, args=args, outcome="would_commit",
        candidate=_event_candidate(updated), base_revision_id=base_revision_id,
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)


async def dry_run_event_set_status(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, current: Gathering | None, status: str,
    base_revision_id: str | None, now_ms: int,
) -> WouldWrite:
    """`event_set_status` in shadow."""
    return await dry_run_event_revise(
        ledger=ledger, session_id=session_id, unit=unit, platform=platform,
        conversation_id=conversation_id, current=current,
        base_revision_id=base_revision_id, now_ms=now_ms, status=status,
    )


# --------------------------------------------------------------------------
# standing
# --------------------------------------------------------------------------

async def dry_run_standing_revise(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, prior: Standing | None,
    where_it_stands: str, working_set: Sequence[WorkingSetItem] = (),
    hold_open: str | None = None, i_owe: str | None = None, mode: str = "deduced",
    base_revision_id: int | None = None, now_ms: int,
) -> WouldWrite:
    """`standing_revise` in shadow. Calls the exact three pure checks
    `nora.infra.sql.standing_revise.standing_revise` (the LIVE verb) calls,
    imported from `nora.domain.standing_revise` — this function is that
    module's async half restated with a ledger write where the live verb
    has `store.write`."""
    args: dict[str, object] = {
        "where_it_stands": where_it_stands,
        "working_set": [w.bears_on for w in working_set],
        "hold_open": hold_open, "i_owe": i_owe, "mode": mode,
        "base_revision_id": base_revision_id,
    }
    schema_refusal = check_schema(
        where_it_stands=where_it_stands, hold_open=hold_open, i_owe=i_owe,
    )
    if schema_refusal is not None:
        write = WouldWrite(
            tier="standing", verb="standing_revise", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=schema_refusal.reason, refusal_detail=schema_refusal.detail,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    mode_refusal = check_mode(mode)
    if mode_refusal is not None:
        write = WouldWrite(
            tier="standing", verb="standing_revise", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=mode_refusal.reason, refusal_detail=mode_refusal.detail,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    stale_refusal = check_stale_base(
        base_revision_id=base_revision_id,
        current_fold_count=prior.fold_count if prior is not None else None,
    )
    if stale_refusal is not None:
        write = WouldWrite(
            tier="standing", verb="standing_revise", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=stale_refusal.reason, refusal_detail=stale_refusal.detail,
            base_revision_id=None if base_revision_id is None else str(base_revision_id),
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    candidate = {
        "platform": platform, "conversation_id": conversation_id,
        "body": where_it_stands, "hold_open": hold_open, "i_owe": i_owe,
        "fold_count": (prior.fold_count + 1) if prior is not None else 1,
        "updated_at": now_ms,
        "working_set": [w.bears_on for w in working_set],
        "mode": mode,
    }
    write = WouldWrite(
        tier="standing", verb="standing_revise", platform=platform,
        conversation_id=conversation_id, args=args, outcome="would_commit",
        candidate=candidate,
        base_revision_id=None if base_revision_id is None else str(base_revision_id),
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
