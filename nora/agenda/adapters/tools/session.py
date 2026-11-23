"""Cognition-session verbs for self-wakes and reminders."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any, Literal

from nora.agenda.adapters.tools.session_remind import (
    _remind_create,
    _remind_destinations_check,
    _remind_subject_check,
)
from nora.agent import cognition_pace as pace
from nora.app.session_verbs import (
    _DAY_MS,
    SessionDeps,
    _answer,
    _bad_time,
    _outside_unit,
    _refused,
    _room_key,
    _room_of,
    _when_ms,
    now_utc,
)
from nora.domain.cognition_remind import (
    RemindContext,
    check_remind_time,
)
from nora.domain.cognition_session import Room
from nora.domain.reminders import (
    Reminder,
)


def session_verbs(d: SessionDeps) -> tuple[tuple[Any, ...], ...]:
    """The session's write verbs, built against the runner's
    collaborators in `d` (`d.runner` is `CognitionSessionRunner`)."""
    return (
        (),  # The session runner expects the event, wake and reminder slots.
        (_make_wake_at(d), _make_wake_move(d), _make_wake_cancel(d),),
        (_make_remind(d), _make_remind_move(d), _make_remind_cancel(d),),
    )


async def _own_reminder(d: SessionDeps, reminder_id: str) -> Reminder | dict[str, object]:
    self, state = d.runner, d.state
    row: Reminder | None = next(
        (r for r in await self._reminders.pending_of(self._ai_peer)
         if str(r.id) == reminder_id), None,
    )
    if row is None:
        return _refused("not_found", f"no pending reminder of yours {reminder_id!r}")
    if _room_key(Room(row.platform, row.conversation_id)) not in state.allowed_rooms:
        return _outside_unit(state)
    return row


def _make_wake_at(d: SessionDeps) -> Any:
    self, unit, state = d.runner, d.unit, d.state
    async def wake_at(
        subject_rows: list[str], at: str, why: str,
    ) -> dict[str, object]:
        """Ask for your own next look at some rows, at a time you name. Call
        this at the end of a pass when something will be worth checking
        again later ("the event is at 20:00, look at 19:40"). Nothing is
        sent: it only wakes you. A new one that shares a subject row with a
        pending one replaces it.

        Returns `status`: ok (with the `wake_id` to move or cancel it), refused
        or unavailable, with a `reason` and `detail` when it is not ok.

        Args:
            subject_rows: The rows to look at again, at least one.
            at: When, ISO 8601 with an offset.
            why: The reason you ask, in a sentence.
        """
        at_ms = _when_ms(at)
        if at_ms is None:
            return _bad_time("at")
        async with self._lock(unit):
            # The REAL clock, not the session's start: a session can run
            # for minutes, and the per-day window and the horizon are
            # about when this call is made.
            call_ms = self._now_ms()
            pending = await self._pending(unit, call_ms)
            created_today = await self._store.count_self_wakes_created(
                unit=unit, since_ms=call_ms - _DAY_MS,
            )
            wake_ref = self._mint_ref()
            answer = await pace.wake_at(
                ledger=self._store, stimuli=self._stimuli, session_id=state.session_id,
                unit=unit, subject_rows=subject_rows, at_ms=at_ms, why=why,
                now_ms=call_ms, wake_ref=wake_ref,
                cooldown_seconds=self._cooldown_seconds, pending=pending,
                max_self_wakes=self._max_self_wakes, created_today=created_today,
                max_self_wakes_per_day=self._max_self_wakes_per_day,
            )
        if not answer.refused:
            self._inc("nora_self_wake_total", outcome="created")
        return _answer(answer, wake_id=wake_ref)
    return wake_at


def _make_wake_move(d: SessionDeps) -> Any:
    self, unit, state = d.runner, d.unit, d.state
    async def wake_move(wake_id: str, at: str, why: str) -> dict[str, object]:
        """Move a self-wake you set to a new time. Call this when the time
        you chose no longer fits. The old time no longer wakes you.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            wake_id: The id `wake_at` returned.
            at: The new time, ISO 8601 with an offset.
            why: The reason you move it.
        """
        at_ms = _when_ms(at)
        if at_ms is None:
            return _bad_time("at")
        async with self._lock(unit):
            call_ms = self._now_ms()
            pending = await self._pending(unit, call_ms)
            answer = await pace.wake_move(
                ledger=self._store, stimuli=self._stimuli,
                session_id=state.session_id, unit=unit, wake_ref=wake_id, at_ms=at_ms,
                why=why, now_ms=call_ms, cooldown_seconds=self._cooldown_seconds,
                pending=pending, created_by_session_id=None,
            )
        if not answer.refused:
            self._inc("nora_self_wake_total", outcome="moved")
        return _answer(answer)
    return wake_move


def _make_wake_cancel(d: SessionDeps) -> Any:
    self, unit, state = d.runner, d.unit, d.state
    async def wake_cancel(wake_id: str, why: str) -> dict[str, object]:
        """Cancel a self-wake you set. Call this when there is no longer
        anything to look at. It will not wake you.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            wake_id: The id `wake_at` returned.
            why: The reason you cancel it.
        """
        async with self._lock(unit):
            call_ms = self._now_ms()
            pending = await self._pending(unit, call_ms)
            answer = await pace.wake_cancel(
                ledger=self._store, stimuli=self._stimuli,
                session_id=state.session_id, unit=unit, wake_ref=wake_id, why=why,
                now_ms=call_ms, pending=pending, created_by_session_id=None,
            )
        if not answer.refused:
            self._inc("nora_self_wake_total", outcome="cancelled")
        return _answer(answer)
    return wake_cancel


def _make_remind(d: SessionDeps) -> Any:
    self, unit, state = d.runner, d.unit, d.state
    async def remind(
        intent: Literal["tell", "ask"], text: str, subject: str, action: str,
        subject_rows: list[str], at: str,
        room: str = "", also_rooms: list[str] | None = None,
        new_action: str = "",
    ) -> dict[str, object]:
        """Reach a room at a time: when the moment comes you get a turn in
        that room to say something to it or ask it something, and you may
        still stay silent then. Call this when a room should hear from you
        and not before.

        `subject` names the open entry the act is about, as `kind:id`
        (`note:`, `decision:`, `commitment:`, `thread:` or `event:` plus the
        id the note index and ledger read show). It must resolve to an
        open entry or the call is `unresolved`, never scheduled on a
        citation alone. `action` is YOUR short key for the act
        (`confirm-dinner-date`, `ask-vote-deadline`): two questions about
        the same subject are different actions, and the same
        subject+action already pending somewhere you did not name is
        refused `conflict_other_room`. `also_rooms` is how you mean the
        same act in several rooms at once.

        Returns `status`: ok (with `reminder_ids`), refused or
        unavailable, plus `unresolved` when `subject` does not resolve to
        the open entry the act is about, each with a `reason` and `detail`
        when it is not ok.

        Args:
            intent: `tell` to say something, `ask` to ask something.
            text: What to raise, in a sentence, as you will read it when it
                comes due.
            subject: The open entry the act is about, as `kind:id`.
            action: Your key for this act on this subject: same act again
                is the same key, a different question is a different one.
            subject_rows: The evidence rows it rests on, at least one.
            at: When to reach the room, ISO 8601 with an offset.
            room: Optional when the agenda is one room, required when it
                spans several. The room to reach, as
                `platform:conversation_id`, which the reads block
                shows beside each room's label.
            also_rooms: Optional. Every other room this same act should
                reach, each as `platform:conversation_id`. An act the unit
                already has pending somewhere else is refused unless you
                name that room here or in `room`.
            new_action: Optional. Required, a short why, only when this is
                a DIFFERENT act on a subject an `intent` act already
                waits on: a pending act's action key is the stable name
                of what is pending, so a second act needs a recorded
                reason, not a fresh spelling. The why is kept on the row.
        """
        resolved = _room_of(room, state)
        if isinstance(resolved, dict):
            return resolved
        platform, conversation_id = resolved
        at_ms = _when_ms(at)
        if at_ms is None:
            return _bad_time("at")
        if not text.strip():
            return _refused("no_text", "say what to raise when it comes due")
        action_key = action.strip().lower()
        if not action_key:
            return _refused(
                "no_action", "name this act: same act again is the same key",
            )
        destinations = [(platform, conversation_id)]
        for extra in also_rooms or []:
            r = _room_of(extra, state)
            if isinstance(r, dict):
                return r
            if r not in destinations:
                destinations.append(r)
        async with self._lock(unit):
            checked = await _remind_subject_check(
                d, subject=subject, intent=intent, subject_rows=subject_rows,
                at_ms=at_ms, action_key=action_key, new_action=new_action,
                platform=platform, conversation_id=conversation_id,
                destinations=destinations,
            )
            if isinstance(checked, dict):
                return checked
            resolved_subject, pending = checked
            subject_id, subject_kind = resolved_subject[1], resolved_subject[0]
            rooms = await _remind_destinations_check(
                d, resolved_subject=resolved_subject, intent=intent,
                action_key=action_key, new_action=new_action,
                destinations=destinations, pending=pending,
            )
            if isinstance(rooms, dict):
                return rooms
            link, pending_dests, unit_rooms = rooms
            reminder_ids = await _remind_create(
                d, text=text, intent=intent, subject_rows=subject_rows,
                at_ms=at_ms, link=link, subject_kind=subject_kind,
                subject_id=subject_id, action_key=action_key,
                destinations=destinations, pending_dests=pending_dests,
                unit_rooms=unit_rooms,
            )
            if isinstance(reminder_ids, dict):
                return reminder_ids
        return {"status": "ok", "reminder_id": reminder_ids[0],
                "reminder_ids": reminder_ids}
    return remind


def _make_remind_move(d: SessionDeps) -> Any:
    self, unit = d.runner, d.unit
    async def remind_move(
        reminder_id: str, at: str, why: str,
    ) -> dict[str, object]:
        """Move a reminder you set to a new time. Call this when the time
        you chose no longer fits. Only your own.

        Returns `status`: ok (with the new `reminder_id`), refused or
        unavailable, with a `reason` and `detail` when it is not ok.

        Args:
            reminder_id: The id `remind` returned.
            at: The new time, ISO 8601 with an offset.
            why: The reason you move it.
        """
        at_ms = _when_ms(at)
        if at_ms is None:
            return _bad_time("at")
        async with self._lock(unit):
            row = await _own_reminder(d, reminder_id)
            if isinstance(row, dict):
                return row
            refusal = check_remind_time(RemindContext(
                now_ms=self._now_ms(), at_ms=at_ms,
                min_lead_seconds=self._remind_min_lead_seconds,
                max_horizon_days=self._remind_max_horizon_days,
            ))
            if refusal is not None:
                return _refused(refusal.reason, refusal.detail)
            moved = replace(
                row, id=uuid.uuid4(), due_at=now_utc(at_ms),
                created_at=now_utc(self._now_ms()),
            )
            if not await self._reminders.cancel(row.id, conversation_id=row.conversation_id):
                # The heartbeat claimed it between the read and the cancel:
                # it is firing now, so a moved copy would say it twice.
                return _refused("already_due", "that reminder is coming due now")
            await self._reminders.create(moved)
        return {"status": "ok", "reminder_id": str(moved.id)}
    return remind_move


def _make_remind_cancel(d: SessionDeps) -> Any:
    self, unit = d.runner, d.unit
    async def remind_cancel(reminder_id: str, why: str) -> dict[str, object]:
        """Cancel a reminder you set. Call this when the room no longer
        needs to hear it. Only your own.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            reminder_id: The id `remind` returned.
            why: The reason you cancel it.
        """
        async with self._lock(unit):
            row = await _own_reminder(d, reminder_id)
            if isinstance(row, dict):
                return row
            await self._reminders.cancel(row.id, conversation_id=row.conversation_id)
        return {"status": "ok"}
    return remind_cancel
