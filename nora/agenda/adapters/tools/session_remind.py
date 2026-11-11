"""The `remind` verb's internals: the checks and the arbitrated write it
runs under the session's unit lock. `session.py` keeps the verb itself.
"""

from __future__ import annotations

import uuid
from typing import Any

from nora.app.session_verbs import SessionDeps, _refused
from nora.domain.cognition_remind import check_remind
from nora.domain.reminders import (
    ConflictingProactiveActError,
    DuplicateProactiveNoteError,
    DuplicateProactiveSubjectError,
    DuplicateReminderError,
)


async def _remind_subject_check(
    d: SessionDeps, *, subject: str, intent: str, subject_rows: list[str],
    at_ms: int, action_key: str, new_action: str,
    platform: str, conversation_id: str, destinations: list[tuple[str, str]],
) -> dict[str, object] | tuple[Any, Any]:
    """The named entry resolved and `check_remind`'s verdict, under the unit lock."""
    self, state, unit = d.runner, d.state, d.unit
    resolved_subject = await self._resolve_named_subject(
        unit, state, subject,
    )
    if resolved_subject is None:
        # The `unresolved` toolResult in this session's trace is
        # the durable evidence — the shared view reads it back
        # through the turn Record, across restarts and processes.
        return {
            "status": "unresolved",
            "reason": "subject_unresolved",
            "detail": (
                f"{subject!r} does not resolve to an open note, "
                "decision, commitment, thread or event this act is "
                "about: name the open entry's `kind:id`, or do not "
                "schedule it"
            ),
        }
    pending = await self._pending_reminds()
    context = await self._remind_context(
        unit, platform, conversation_id, subject_rows, at_ms,
    )
    refusal = check_remind(
        subject_rows=subject_rows, intent=intent,
        platform=platform, conversation_id=conversation_id, pending=pending,
        context=context, subject=resolved_subject, action=action_key,
        extra_destinations=destinations[1:],
    )
    if refusal is not None:
        return _refused(refusal.reason, refusal.detail)
    same_intent = {
        p.action for p in pending
        if p.subject == resolved_subject
        and p.intent == intent
    } - {action_key}
    if same_intent and not new_action.strip():
        return _refused(
            "conflict_same_subject",
            f"an {intent!r} act is already pending on that subject "
            f"as {sorted(same_intent)[0]!r}: schedule under the same "
            "action if it is the same act, or pass new_action with "
            "why this is a different question",
        )
    return resolved_subject, pending


async def _remind_destinations_check(
    d: SessionDeps, *, resolved_subject: Any, intent: str,
    action_key: str, new_action: str,
    destinations: list[tuple[str, str]], pending: Any,
) -> dict[str, object] | tuple[dict[str, str], Any, Any]:
    """Whether every named room may be written."""
    self, state, unit = d.runner, d.state, d.unit
    subject_id, subject_kind = resolved_subject[1], resolved_subject[0]
    # Every destination validated BEFORE a row exists: proactive
    # policy and the requester cap apply per room, so a batch
    # refused late is refused whole.
    for dest_platform, dest_cid in destinations[1:]:
        if self._room_proactive is not None and not self._room_proactive(
            dest_platform, dest_cid,
        ):
            return _refused(
                "room_not_proactive",
                f"you may not reach {dest_platform}:{dest_cid} "
                "unasked",
            )
    link: dict[str, str] = {"plan_id": str(uuid.uuid4())}
    if new_action.strip():
        link["new_action_reason"] = new_action.strip()[:200]
    if subject_kind == "note":
        shown_note = await self._notes.read(
            unit=unit, note_id=subject_id)
        if shown_note is None:
            return _refused(
                "not_found", f"no open note {subject_id!r}")
        # The note link machinery (merge repoint, retire-cancel, the
        # moved-note screen) keys on these payload fields — the deliberate
        # stronger invariant: a note-subject act is single-destination.
        link["note_id"] = shown_note[0].id
        link["revision_id"] = shown_note[1].id
        link["note_title"] = shown_note[0].title
    pending_dests = {
        (p.platform, p.conversation_id) for p in pending
        if p.subject == resolved_subject and p.action == action_key
    }
    unit_rooms = (
        list(self._rooms_of_unit(unit))
        if self._rooms_of_unit is not None
        # Unwired harnesses have no canonical scope: the agenda's
        # own rooms are all the scope there is (the same parity
        # the unwired debrief keeps).
        else [tuple(k.split(":", 1)) for k in state.rooms]
    )
    if not unit_rooms:
        # The conflict arbiter needs the CANONICAL unit, not this agenda's
        # room list: a scope we cannot establish refuses the write rather
        # than narrowing it.
        return {"status": "unavailable",
                "reason": "unit_scope_unavailable",
                "detail": "the unit's room set could not be read"}
    delivered = await self._reminders.delivered_subject(
        unit_rooms, requester_person_id=self._ai_peer,
        subject_kind=subject_kind, subject_id=subject_id,
        action=action_key, limit=5,
    )
    if len(delivered) >= 5:
        # The keyed read cut off: an uncounted delivered act
        # could sit past the cap, so «not delivered» is not
        # established.
        return {"status": "unavailable",
                "reason": "delivered_check_incomplete",
                "detail": "the delivered check could not be completed"}
    if delivered:
        return _refused(
            "duplicate_pending",
            "that act was already delivered for the room(s) named",
        )
    return link, pending_dests, unit_rooms


async def _remind_create(
    d: SessionDeps, *, text: str, intent: str, subject_rows: list[str],
    at_ms: int, link: dict[str, str], subject_kind: str, subject_id: str,
    action_key: str, destinations: list[tuple[str, str]],
    pending_dests: Any, unit_rooms: list[tuple[str, str]],
) -> dict[str, object] | list[str]:
    """The rows written under the unit's advisory arbitration."""
    self, unit = d.runner, d.unit
    rows = [
        self._reminder_row(
            platform=dest_platform, conversation_id=dest_cid,
            text=text, intent=intent, subject_rows=subject_rows,
            at_ms=at_ms, unit=unit,
            link={
                **link,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "action": action_key,
            },
        )
        for dest_platform, dest_cid in destinations
        if (dest_platform, dest_cid) not in pending_dests
    ]
    if not rows:
        return _refused(
            "duplicate_pending",
            "the same act is already pending for the room(s) named",
        )
    try:
        # ONE transaction, advisory-locked on the unit with the
        # cross-room conflict check INSIDE it: every destination
        # lands or none does, against any writer in any process.
        await self._reminders.create_many(
            rows, arbitration_key=f"proactive:{unit}",
            arbitration_rooms=[tuple(r) for r in unit_rooms],
        )
    except (ConflictingProactiveActError,
            DuplicateProactiveNoteError,
            DuplicateProactiveSubjectError,
            DuplicateReminderError):
        return _refused(
            "duplicate_pending",
            "the same act is already pending for the room(s) named",
        )
    return [str(r.id) for r in rows]
