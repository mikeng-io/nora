from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Literal

from nora.agenda.adapters.tools.shared import (
    _PERIOD_DAYS,
    _owner_name,
    _parse_repeat,
    _reminders_ok_note,
    _RepeatRefusal,
)
from nora.agent.toolkit.clock import _HKT, _parse_when
from nora.agent.toolkit.results import _record, _record_created
from nora.domain.notes import (
    Note,
)
from nora.domain.reminders import (
    Recurrence,
    Reminder,
    ReminderState,
    Series,
    remaining_occurrences,
)

log = logging.getLogger("nora.tools")


def _make_remind(deps):
    async def remind(
        when: str,
        text: str,
        repeat: Literal[
            "none",
            "minutely",
            "hourly",
            "daily",
            "weekly",
            "monthly",
            "yearly",
        ] = "none",
        every_n: int | None = None,
        times: int | None = None,
        until: str | None = None,
        note: str | None = None,
    ) -> dict:
        """Hold something for later and say it back into this conversation when the
        time comes, once or on a repeat.
        Call this when somebody asks you to remember a thing for them.

        `text` is their own words, not a script: when it comes due you get the
        turn back and answer it as a fresh request.

        `when` is one exact moment, ISO-8601 with an offset; for a repeat it is
        the first time it goes off. Work that moment out yourself from the
        current time rather than asking them to restate it. Lead time is
        arithmetic on `when`: N daily reminders before a date start N days
        before it, with `times=N`.

        One call sets a whole series: `repeat` is the unit and `every_n`
        multiplies it. Never set several reminders to fake a repeat. Every series
        ends: at `times`, at `until`, or at a cap the result reports in
        `repeat_left`. Nothing repeats faster than every few minutes.

        Returns `status`: ok, refused or unavailable, with a `note` on how to
        report it.

        Args:
            when: the FIRST moment it should go off, ISO-8601 with an offset.
            text: what they asked for, in their words.
            repeat: the unit of a repeat, or "none" for a one-off (the default).
            every_n: repeat every N of those units: 10 with `minutely` is every
                ten minutes. Default 1. Repeats only.
            times: how many times in total, including the first. Repeats only.
            until: the last date/moment it may go off, ISO-8601. Repeats only.
            note (str, optional): the TITLE of the note this is about, exactly
                as listed in <notes>, when the reminder is about one of them. It
                records the link; a reminder you set for yourself on your own
                pass is dropped if the note is settled before it fires.
        """
        import nora.memory as _w_memory

        c = SimpleNamespace(
            when=when,
            text=text,
            repeat=repeat,
            every_n=every_n,
            times=times,
            until=until,
            note=note,
            conversation_type=deps.conversation_type,
            fire_turn=deps.fire_turn,
            metrics=deps.metrics,
            reminders=deps.reminders,
            room=deps.room,
            _notebook=deps._notebook,
            _resolve=_w_memory.wire.make_resolve(deps),
            bound_conversation_id=deps.bound_conversation_id,
            caps=deps.caps,
            notes_off=deps.notes_off,
            now_fn=deps.now_fn,
        )
        k_, _, v_ = await _remind__validate(c)
        if k_:
            return v_
        k_, _, v_ = await _remind__resolve_notebook(c)
        if k_:
            return v_
        k_, _, v_ = await _remind__result(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return remind


def _make_list_reminders(deps):
    ai_peer = deps.ai_peer
    metrics = deps.metrics
    persons = deps.persons
    reminders = deps.reminders
    bound_conversation_id = deps.bound_conversation_id

    async def list_reminders() -> dict:
        """List the reminders waiting in THIS conversation, soonest first. Call
        this when somebody asks what's set, or before cancelling one.

        Each row has its `id` (for cancel_reminder), `text`, `due_at`, `set_by`
        and `repeat`.

        Returns `status`: ok or unavailable, with a `note` on how to report it.
        """
        if reminders is None:
            _record(metrics, "list_reminders", "unavailable")
            return {
                "status": "unavailable",
                "note": "Reminders aren't set up here, so there is nothing to list.",
            }
        try:
            rows = await reminders.pending_for(bound_conversation_id)
        except Exception:
            log.exception("list_reminders failed")
            _record(metrics, "list_reminders", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't check what's waiting. Say so: an empty list and "
                "a failed look are different answers.",
            }
        _record(metrics, "list_reminders", "ok", count=len(rows))
        listed = [
            {
                "id": str(r.id),
                "text": r.text,
                # A REPEATING reminder shows as ONE row, because that is what
                # it is here: a series keeps exactly one waiting occurrence
                # and the rest do not exist yet (DEC-85). So `repeat` is the
                "repeat": (
                    Recurrence.NONE.value if r.series is None else r.series.rule.every.value
                ),
                **(
                    {}
                    if r.series is None
                    else {
                        "every_n": r.series.rule.interval,
                        "repeat_left": remaining_occurrences(r.series),
                        "repeat_until": (
                            None
                            if r.series.rule.until is None
                            else r.series.rule.until.astimezone(_HKT).isoformat()
                        ),
                    }
                ),
                # WHOSE. A reminder is group-owned in a group, so this listing
                # shows everybody's and anybody here may cancel any of them —
                # which is precisely why the name has to be on the row.
                "set_by": _owner_name(persons, r.requester_person_id, ai_peer=ai_peer),
                # Rendered in Nora's own zone rather than UTC: the model
                # reasons about 「聽日七點」 against the local clock the prefill
                # stated, and a UTC stamp is one silent subtraction away from
                "due_at": r.due_at.astimezone(_HKT).isoformat(),
            }
            for r in rows
        ]
        return {"status": "ok", "reminders": listed, "note": _reminders_ok_note(listed)}

    return list_reminders


def _make_cancel_reminder(deps):
    metrics = deps.metrics
    reminders = deps.reminders
    bound_conversation_id = deps.bound_conversation_id

    async def cancel_reminder(reminder_id: str) -> dict:
        """Cancel a waiting reminder by the id list_reminders gave you. Call this
        when somebody wants to call off a reminder.

        Anyone here may cancel any reminder set in this conversation. On a
        repeating reminder it cancels the whole series: there is no skipping a
        single occurrence, so if that is what they want, say so and offer to
        cancel the series instead.

        Returns `status`: ok, refused or unavailable, with a `note` on how to
        report it.

        Args:
            reminder_id: The id from list_reminders, never one you made up.
        """
        if reminders is None:
            _record(metrics, "cancel_reminder", "unavailable")
            return {
                "status": "unavailable",
                "note": "Reminders aren't set up here, so there is nothing to cancel.",
            }
        try:
            parsed_id = uuid.UUID(str(reminder_id).strip())
        except (AttributeError, TypeError, ValueError):
            # The MODEL writes this argument, so a malformed one is ordinary.
            # `UUID("洗衫")` raises `ValueError`, and an exception escaping a tool
            # is a silent turn (R-5).
            _record(metrics, "cancel_reminder", "refused", reason="not_an_id")
            return {
                "status": "refused",
                "note": "That isn't an id I gave you. Call list_reminders and use one of those.",
            }
        try:
            cancelled = await reminders.cancel(
                parsed_id,
                conversation_id=bound_conversation_id,
            )
        except Exception:
            log.exception("cancel_reminder failed")
            _record(metrics, "cancel_reminder", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't cancel it. Say you're not sure it's off, rather "
                "than saying it is.",
            }
        if not cancelled:
            # The store answers ONE bool for three situations — another
            # conversation's row (a uuid carries no scope, so the STORE re-checks
            # it), a row that already resolved, and an id that never existed. The
            _record(metrics, "cancel_reminder", "refused", reason="no_pending_row")
            return {
                "status": "refused",
                "note": "I have nothing waiting under that id here. It may already "
                "have gone out, or it belongs to another conversation. Check "
                "list_reminders before saying it's off.",
            }
        _record(metrics, "cancel_reminder", "ok")
        # How to report it (ADR 0015 §3). The store answers one bool, so the
        # note cannot say whether it repeated or whose it was; it says what to
        # tell them either way.
        return {
            "status": "ok",
            "note": "It is off from now on, the whole series if it repeated. If "
            "list_reminders showed someone else as set_by, say whose "
            "it was.",
        }

    return cancel_reminder


async def _remind__validate(c):
    if c.fire_turn:
        _record_created(c.metrics, "fire_turn")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "This turn is a reminder going off, so "
                    "I can't set another one from inside it. Just answer what was asked; if a NEW "
                    "reminder is genuinely wanted, someone has to ask for it."
                ),
            },
        )
    if c.reminders is None:
        _record_created(c.metrics, "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't hold anything for later "
                    "because reminders aren't set up here. Say so plainly rather than promising."
                ),
            },
        )
    c.tagger = c.room.tagger
    if c.tagger is None:
        _record_created(c.metrics, "unmapped")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "I don't know who you are well enough "
                    "to hold something for you. Say you can't set it, and do not guess."
                ),
            },
        )
    c.owner = c.tagger.person_id
    c.due_at = _parse_when(c.when)
    if c.due_at is None:
        _record_created(c.metrics, "unparseable")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "I couldn't read that as a time. Give "
                    "me one exact moment as ISO-8601 with an offset, like "
                    "2026-08-08T21:00:00+08:00."
                ),
            },
        )
    c.now = c.now_fn()
    if c.due_at <= c.now:
        _record_created(c.metrics, "past")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "That moment has already gone. If they "
                    "meant tomorrow, work out that date and try again; otherwise ask which one."
                ),
            },
        )
    if c.due_at - c.now < timedelta(seconds=c.caps.min_lead_seconds):
        _record_created(c.metrics, "too_soon")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    f"That is less than "
                    f"{c.caps.min_lead_seconds // 60} minutes away, near enough that I'd be "
                    f"saying it "
                    f"now. Answer them now instead, or set it further out."
                ),
            },
        )
    c.rule = _parse_repeat(
        repeat=c.repeat,
        every_n=c.every_n,
        times=c.times,
        until=c.until,
        due_at=c.due_at,
        caps=c.caps,
    )
    if isinstance(c.rule, _RepeatRefusal):
        _record_created(c.metrics, c.rule.status)
        return (True, None, {"status": c.rule.outcome, "note": c.rule.note})
    c.horizon = timedelta(days=c.caps.max_horizon_days)
    if c.rule is not None:
        c.horizon = max(c.horizon, timedelta(days=_PERIOD_DAYS[c.rule.every]))
    if c.due_at - c.now > c.horizon:
        _record_created(c.metrics, "too_far")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    f"I only hold things for "
                    f"{c.horizon.days} days. Say that, and let them ask again nearer the time."
                ),
            },
        )
    c.payload: dict[str, str] | None = None
    c.linked: Note | None = None
    return (False, None, None)


async def _remind__resolve_notebook(c):
    if c.note is not None and c.note.strip():
        c.book = c._notebook()
        if c.book is None:
            _record_created(c.metrics, "unavailable")
            return (True, None, dict(c.notes_off))
        try:
            c.matches = await c._resolve(c.book, c.note)
        except Exception:
            log.exception("remind: note lookup failed")
            _record_created(c.metrics, "unavailable")
            return (True, None, dict(c.notes_off))
        if len(c.matches) != 1:
            _record_created(c.metrics, "unparseable")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": ("More than one note answers to that title. Say which.")
                    if c.matches
                    else "No note in <notes> has that title. Give "
                    "it exactly as listed, or leave `note` out.",
                },
            )
        c.linked = c.matches[0]
        c.payload = {
            "note_id": c.linked.id,
            "revision_id": c.linked.head_revision_id,
            "note_title": c.linked.title,
        }
    try:
        c.pending = await c.reminders.count_pending(c.owner)
        if c.pending >= c.caps.max_pending_per_requester:
            _record_created(c.metrics, "too_many")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": (
                        f"They already have {c.pending} "
                        f"waiting, which is my limit. Say so: list_reminders shows what is "
                        f"there, and "
                        f"one can be cancelled to make room."
                    ),
                },
            )
        c.series_id = uuid.uuid4()
        c.series = (
            None
            if c.rule is None
            else Series(id=c.series_id, anchor_at=c.due_at, occurrence=0, rule=c.rule)
        )
        c.row = Reminder(
            id=uuid.uuid4() if c.series is None else uuid.uuid5(c.series_id, "0"),
            requester_person_id=c.owner,
            platform=c.room.platform,
            conversation_id=c.bound_conversation_id,
            conversation_type=c.conversation_type,
            text=c.text,
            created_at=c.now,
            due_at=c.due_at,
            state=ReminderState.PENDING,
            fired_at=None,
            outcome=None,
            series=c.series,
            kind=("reminder"),
            payload=c.payload,
        )
        await c.reminders.create(c.row)
    except Exception:
        log.exception("remind failed")
        _record_created(c.metrics, "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I couldn't write that down. Say "
                    "you didn't manage to set it, never that you did."
                ),
            },
        )
    _record_created(
        c.metrics,
        "created",
        ahead_seconds=int((c.due_at - c.now).total_seconds()),
        repeat=Recurrence.NONE.value if c.rule is None else c.rule.every.value,
        kind=c.row.kind,
    )
    return (False, None, None)


async def _remind__result(c):
    c.result: dict[str, object] = {
        "status": "ok",
        "id": str(c.row.id),
        "due_at": c.due_at.astimezone(_HKT).isoformat(),
    }
    if c.payload is not None and "note_title" in c.payload:
        c.result["note_title"] = c.payload["note_title"]
    if c.rule is not None:
        c.result["repeat"] = c.rule.every.value
        c.result["every_n"] = c.rule.interval
        c.result["repeat_left"] = c.rule.count - 1
        c.result["repeat_until"] = (
            None if c.rule.until is None else c.rule.until.astimezone(_HKT).isoformat()
        )
    c.result["note"] = (
        "Say back the moment you took them to mean, so a wrong guess is caught now."
        + (
            (
                " Say the limit too (repeat_left, repeat_until): a limit nobody was told about is "
                "what "
                "surprises people later."
            )
            if c.rule is not None
            else ""
        )
    )
    return (True, None, c.result)
    return (False, None, None)
