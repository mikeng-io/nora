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

from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime

from nora.domain.cognition_write import WouldWrite, stale_event_base
from nora.domain.gatherings import (
    TERMINAL_STATUSES,
    FieldMeta,
    Gathering,
    GatheringStatus,
    InvalidEventFieldModeError,
    merge_field_meta,
    still_open_gatherings,
)
from nora.domain.note_body import NoteBodyError, Shown, check_citations, parse_note_body
from nora.domain.notes import (
    NoteCreationProposal,
    NoteRevisionProposal,
    has_duplicate_open_title,
)
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
    open_titles: tuple[str, ...] = (),
) -> WouldWrite:
    """`note_create` in shadow. Args mirror
    `nora.domain.notes.NoteCreationProposal` plus `established_by`/`now_ms`
    (provenance the live verb stamps, not part of the proposal's own
    invariants) and `shown` (the body's schema check, `parse_note_body`).

    `open_titles` (review follow-up on #625's step 3a-i): the unit's
    currently-open note titles, READ-ONLY input the caller already fetched
    the way the live verb does (`book.standing(unit=...)`,
    `nora.agent.tools.note_create`) — this function makes no store call
    itself. Checked with the exact function the live verb calls
    (`nora.domain.notes.has_duplicate_open_title`), so a duplicate title is
    refused here exactly as it would be on a tag turn (design 41 §7.2,
    `30-cognition.md` §2's "same validator" rule)."""
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
    if has_duplicate_open_title(title, open_titles):
        write = WouldWrite(
            tier="note", verb="note_create", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason="duplicate_title",
            refusal_detail="a note with that title is already open",
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

#: The event field each `Gathering` column a write can change states (12 §3's
#: "what, start, end, place, people, status"; `nora.domain.gatherings.EVENT_PARTS`):
#: the part a citation, a lineage section and a stated reasoning attach to.
_PART_OF: dict[str, str] = dict(zip(
    ("title", "when", "end", "venue_text", "people", "status"),
    ("what", "start", "end", "place", "people", "status"),
    strict=True,
))


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


def _stated(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(
        value, GatheringStatus,
    ) else value


def _checked_evidence(
    *, verb: str, reasoning: str, confidence: str, cited_ids: Sequence[str], shown: Shown,
    parts: Sequence[str], may_be_uncited: bool,
) -> tuple[tuple[FieldMeta, ...], tuple[int, ...], tuple[str, ...]]:
    """What an event write states and rests on, held to what the session saw
    (30 §3 checks 1 and 5). Raises `InvalidEventFieldModeError` for a mode or a
    confidence the tier does not allow and `NoteBodyError` for a citation it was
    not shown, exactly the two the note verbs raise. `may_be_uncited` is the
    one write whose evidence is the clock: `lapsed`, the date passing with
    nothing agreed (40 §3), which no row carries."""
    meta = tuple(FieldMeta(field=p, reasoning=reasoning, confidence=confidence) for p in parts)
    if not cited_ids and may_be_uncited:
        return meta, (), ()
    ids, keys = check_citations(cited_ids, shown, what=verb)
    return meta, ids, keys


def _questions(changes: Mapping[str, object], cited: Sequence[str]) -> dict[str, object]:
    """An abduced write's whole result (40 §4): not a stated field but a
    question to ask, the value as the guess and the rows it came from. Nothing
    of the event changes, and a "yeah" to the question later does not make it
    anything but abduced."""
    return {
        "kind": "question_to_ask", "reasoning": "abduced", "cited_ids": list(cited),
        "questions_to_ask": [
            {"field": _PART_OF[name], "value": _stated(v)} for name, v in changes.items()
        ],
    }


async def dry_run_event_propose(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, now_ms: int, shown: Shown,
    reasoning: str, confidence: str, cited_ids: Sequence[str],
    build: type[Gathering] = Gathering, open_proposed_events: Sequence[Gathering] = (),
    now: datetime | None = None, **event_kwargs: object,
) -> WouldWrite:
    """`event_propose` in shadow. `event_kwargs` are `Gathering`'s own
    constructor arguments (`id`, `title`, `created_by`, `when`, `status`,
    `created_at`, `unit_id`, ...) — this function does not narrow the
    signature further than `Gathering.__post_init__` already does; the
    caller (a session wiring, or a test) builds the row the way
    `GatheringService.create_event` builds one today, then hands it here
    instead of `self._store.create(event)`.

    `status` is `staged` or `proposed` (30 §2: "`event_propose` (status
    `staged` or `proposed`)"); any other is refused `bad_status`, an event
    is born early in its lifecycle.

    `reasoning` (`told`, `deduced` or `abduced`), `confidence` and `cited_ids`
    (message ids and `rollup(...)` keys) are what 40 §4 says every field
    carries; `cited_ids` are held to `shown` as a note's are. **An abduced write
    creates nothing**: it records the question to ask (`_questions`), never a
    stated event.

    `open_proposed_events` (review follow-up on #625's step 3a-i): the
    conversation's already-`PROPOSED` events, READ-ONLY input the caller
    fetched the way the live verb does (`list_events(status=PROPOSED)`) —
    this function makes no store call. Checked with the exact function the
    live verb calls (`nora.domain.gatherings.still_open_gatherings`,
    `GatheringService.create_event`'s I1 backstop), refusing exactly as it
    would when two or more are still open. `now` defaults to `now_ms`
    converted, matching the live verb's own clock argument."""
    args = {
        k: (str(v) if hasattr(v, "isoformat") or k in ("id",) else v)
        for k, v in event_kwargs.items()
    }
    args |= {"reasoning": reasoning, "confidence": confidence, "cited_ids": list(cited_ids)}

    async def refuse(reason: str, detail: str | None = None) -> WouldWrite:
        write = WouldWrite(
            tier="event", verb="event_propose", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=reason, refusal_detail=detail,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)

    clock = now if now is not None else datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    still_open = still_open_gatherings(open_proposed_events, now=clock)
    if len(still_open) >= 2:
        return await refuse(
            "ambiguous_open_gatherings",
            f"{len(still_open)} open proposed events for {conversation_id}",
        )
    if event_kwargs.get("status") not in (GatheringStatus.STAGED, GatheringStatus.PROPOSED):
        return await refuse("bad_status", "an event is proposed or staged, nothing later")
    stated = {
        "title": event_kwargs.get("title"), "when": event_kwargs.get("when"),
        "venue_text": event_kwargs.get("venue_text"), "status": event_kwargs["status"],
    }
    stated = {k: v for k, v in stated.items() if v is not None}
    try:
        meta, _ids, _keys = _checked_evidence(
            verb="event_propose", reasoning=reasoning, confidence=confidence,
            cited_ids=cited_ids, shown=shown,
            parts=[_PART_OF[k] for k in stated], may_be_uncited=False,
        )
    except InvalidEventFieldModeError as exc:
        return await refuse(f"bad_{exc.field}", str(exc))
    except NoteBodyError as exc:
        return await refuse(exc.rule, str(exc))
    if reasoning == "abduced":
        write = WouldWrite(
            tier="event", verb="event_propose", platform=platform,
            conversation_id=conversation_id, args=args, outcome="would_commit",
            candidate=_questions(stated, cited_ids),
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    try:
        event = build(
            conversation_id=conversation_id, unit_id=unit, reasoning=reasoning,
            confidence=confidence, field_meta=meta,
            **event_kwargs,  # type: ignore[arg-type]
        )
    except InvalidEventFieldModeError as exc:
        return await refuse(f"bad_{exc.field}", str(exc))
    candidate = _event_candidate(event) | {
        "cited_ids": list(cited_ids), "evidence_parts": [m.field for m in meta],
    }
    write = WouldWrite(
        tier="event", verb="event_propose", platform=platform,
        conversation_id=conversation_id, args=args, outcome="would_commit",
        candidate=candidate,
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)


async def dry_run_event_revise(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, current: Gathering | None,
    base_revision_id: str | None, now_ms: int, shown: Shown, reasoning: str,
    confidence: str, cited_ids: Sequence[str], verb: str = "event_revise",
    **field_changes: object,
) -> WouldWrite:
    """`event_revise` in shadow. `current` is the row the caller already
    read (the same "prior is a parameter" choice `standing_revise` makes,
    §3b): this function never reads a store.

    The fields `field_changes` names are the fields this write states, each
    with `reasoning` and `confidence`, resting on `cited_ids` (held to `shown`
    as a note's are; a move to `lapsed` alone may cite nothing, the date
    passing is no row). Fields it leaves alone keep what they carried. **An
    abduced write changes nothing**: it records the question to ask."""
    args = {
        "base_revision_id": base_revision_id, **field_changes, "reasoning": reasoning,
        "confidence": confidence, "cited_ids": list(cited_ids),
    }
    current_head = None if current is None else (
        None if current.head_revision_id is None else str(current.head_revision_id)
    )

    async def refuse(reason: str, detail: str | None = None) -> WouldWrite:
        write = WouldWrite(
            tier="event", verb=verb, platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=reason, refusal_detail=detail,
            base_revision_id=base_revision_id,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)

    if current is None:
        return await refuse("not_found")
    if stale_event_base(base_revision_id=base_revision_id, current_head_revision_id=current_head):
        return await refuse("stale_base", f"current head is {current_head!r}")
    if current.status in TERMINAL_STATUSES:
        # 40 §3: only a status BEFORE `happened` may be revised.
        return await refuse(
            "terminal_status", f"the event is {current.status}; nothing moves it on",
        )
    try:
        meta, _ids, _keys = _checked_evidence(
            verb=verb, reasoning=reasoning, confidence=confidence, cited_ids=cited_ids,
            shown=shown, parts=[_PART_OF[k] for k in field_changes],
            may_be_uncited=str(field_changes.get("status")) == GatheringStatus.LAPSED,
        )
    except InvalidEventFieldModeError as exc:
        return await refuse(f"bad_{exc.field}", str(exc))
    except NoteBodyError as exc:
        return await refuse(exc.rule, str(exc))
    if reasoning == "abduced":
        write = WouldWrite(
            tier="event", verb=verb, platform=platform,
            conversation_id=conversation_id, args=args, outcome="would_commit",
            candidate=_questions(field_changes, cited_ids) | {"event_id": str(current.id)},
            base_revision_id=base_revision_id,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    try:
        updated = replace(
            current, **field_changes,  # type: ignore[arg-type]
            reasoning=reasoning, confidence=confidence,
            field_meta=merge_field_meta(current.field_meta, meta),
        )
    except InvalidEventFieldModeError as exc:
        return await refuse(f"bad_{exc.field}", str(exc))
    candidate = _event_candidate(updated) | {
        "cited_ids": list(cited_ids), "evidence_parts": [m.field for m in meta],
    }
    write = WouldWrite(
        tier="event", verb=verb, platform=platform,
        conversation_id=conversation_id, args=args, outcome="would_commit",
        candidate=candidate, base_revision_id=base_revision_id,
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)


async def dry_run_event_set_status(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, current: Gathering | None, status: str,
    base_revision_id: str | None, now_ms: int, shown: Shown, reasoning: str,
    confidence: str, cited_ids: Sequence[str],
) -> WouldWrite:
    """`event_set_status` in shadow: `event_revise` over the `status` field
    alone, recorded under its own verb name."""
    return await dry_run_event_revise(
        ledger=ledger, session_id=session_id, unit=unit, platform=platform,
        conversation_id=conversation_id, current=current,
        base_revision_id=base_revision_id, now_ms=now_ms, shown=shown,
        reasoning=reasoning, confidence=confidence, cited_ids=cited_ids,
        verb="event_set_status", status=status,
    )


# --------------------------------------------------------------------------
# standing
# --------------------------------------------------------------------------

async def dry_run_standing_revise(
    *, ledger: CognitionDryRunStore, session_id: int | None, unit: str,
    platform: str, conversation_id: str, prior: Standing | None,
    where_it_stands: str, working_set: Sequence[WorkingSetItem] = (),
    hold_open: str | None = None, i_owe: str | None = None, mode: str = "deduced",
    base_revision_id: str | None = None, prior_revision_id: str | None = None,
    now_ms: int,
) -> WouldWrite:
    """`standing_revise` in shadow. `base_revision_id` is the
    `standing_revision.id` the session's `standing_read` showed and
    `prior_revision_id` the id of the row that is the head now (M6, DEC-250):
    the caller read both with `prior`, this function reads nothing. Calls the
    exact three pure checks `nora.infra.sql.standing_revise.standing_revise`
    (the LIVE verb) calls,
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
        current_revision_id=prior_revision_id,
    )
    if stale_refusal is not None:
        write = WouldWrite(
            tier="standing", verb="standing_revise", platform=platform,
            conversation_id=conversation_id, args=args, outcome="refused",
            refusal_reason=stale_refusal.reason, refusal_detail=stale_refusal.detail,
            base_revision_id=base_revision_id,
        )
        return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
    candidate: dict[str, object] = {
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
        candidate=candidate, base_revision_id=base_revision_id,
    )
    return await _record(ledger, session_id=session_id, unit=unit, write=write, now_ms=now_ms)
