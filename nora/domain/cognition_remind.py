"""`remind`/`remind_move`/`remind_cancel`, mirrored and held (pure checks;
design/cognition-loop/30-cognition.md §2-§3; migration step 3a-ii).

**Shadow before cut-over** (70-migration.md): every reminder Cognition would
write is recorded here instead of reaching the fire path, and never fires.
This module checks §3's "enforced by code on every act" rules that are code
checks on state the CALLER reads and hands in (the "prior is a parameter"
choice every dry-run module in this tree makes; this module reads nothing):
non-empty citations, the dedupe rule ("one pending act per (subject rows,
addressee)"), the room's `proactive` flag, "a value that is only abduced goes
out as a question", the daily act cap per room and the pending ceiling
(`RemindContext`), and "cognition never cancels a reminder a person set" /
"own misreads only" for `remind_move`/`remind_cancel`. Each is a REFUSAL with a
named reason, so a would-commit is an act the live path would not have stopped.
Not checked here, because it needs a read this module is never handed:
disclosure at assembly (re-run when the reminder fires) and `dream_screen`; and
the design's per-PERSON cap has no addressee to count against, since a
`remind` names a room.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

REMIND_VERBS: tuple[str, ...] = ("remind", "remind_move", "remind_cancel")
INTENTS: tuple[str, ...] = ("tell", "ask")


@dataclass(frozen=True, slots=True)
class PendingMirroredRemind:
    """What the caller already knows about one pending mirrored reminder."""

    reminder_ref: str
    platform: str
    conversation_id: str
    subject_rows: tuple[str, ...]
    created_by_session_id: int | None


@dataclass(frozen=True, slots=True)
class MirrorRow:
    """One recorded `cognition_remind_mirror` row, as `fold_pending_reminds`
    reads it (a refused row is passed too and ignored by the fold)."""

    reminder_ref: str | None
    verb: str
    outcome: str
    platform: str
    conversation_id: str
    subject_rows: tuple[str, ...]
    at_ms: int | None
    session_id: int | None


def fold_pending_reminds(
    rows: Sequence[MirrorRow], *, now_ms: int,
) -> tuple[PendingMirroredRemind, ...]:
    """The mirrored reminds still PENDING, from a unit's rows in recorded
    order. A mirrored remind is a chain (`remind`, then `remind_move` and
    `remind_cancel` on its ref); the latest recorded link decides. Cancelled
    is not pending. **Due is not pending either**: nothing fires in shadow, so
    a remind whose time has come is what would have been sent and is no
    longer a pending act; without this the dedupe rule (one pending act per
    subject rows and room) would refuse a second reminder on the same rows
    forever. A refused row changes nothing; a move or cancel whose `remind` is
    not among `rows` is ignored."""
    live: dict[str, tuple[PendingMirroredRemind, int]] = {}
    for row in rows:
        if row.outcome != "recorded" or row.reminder_ref is None:
            continue
        ref = row.reminder_ref
        if row.verb == "remind" and row.at_ms is not None:
            live[ref] = (
                PendingMirroredRemind(
                    reminder_ref=ref, platform=row.platform,
                    conversation_id=row.conversation_id, subject_rows=row.subject_rows,
                    created_by_session_id=row.session_id,
                ),
                row.at_ms,
            )
        elif row.verb == "remind_move" and ref in live and row.at_ms is not None:
            live[ref] = (live[ref][0], row.at_ms)
        elif row.verb == "remind_cancel":
            live.pop(ref, None)
    return tuple(p for p, at in live.values() if at > now_ms)


@dataclass(frozen=True, slots=True)
class RemindRefusal:
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RemindAnswer:
    """What a mirrored remind call answers: the ledger row, and the refusal
    when it was refused."""

    row_id: int
    refusal_reason: str | None = None
    refusal_detail: str | None = None

    @property
    def refused(self) -> bool:
        return self.refusal_reason is not None


@dataclass(frozen=True, slots=True)
class RemindContext:
    """What the caller read for one `remind`, beyond the pending rows (30 §3,
    "enforced by code on every act"). Every field's default is the permissive
    reading, so a caller that reads none of it is unchanged; the composition
    reads all of it."""

    #: The room's `proactive` flag (`Policy.proactive_allowed`): may Nora reach
    #: this room unasked at all.
    room_proactive: bool = True
    #: `remind` rows recorded for this room in the last 24 hours, and the cap
    #: they are held to (`None`: no cap).
    acts_in_room_today: int = 0
    max_acts_per_room_per_day: int | None = None
    #: The unit's pending ceiling (`None`: none). The count is `len(pending)`.
    max_pending: int | None = None
    #: A cited row is a value that is only abduced: the act must be an `ask`.
    abduced_subject: bool = False


def check_remind(
    *, subject_rows: Sequence[str], intent: str, platform: str, conversation_id: str,
    pending: Sequence[PendingMirroredRemind], context: RemindContext | None = None,
) -> RemindRefusal | None:
    """`remind(at, room, brief)`. `brief` is `(intent, subject_rows)` — never
    prose (30-cognition.md §2). The refusal reasons are `no_citations`,
    `bad_intent`, `room_not_proactive`, `abduced_must_ask`,
    `duplicate_pending`, `room_daily_cap` and `pending_ceiling`; none of the
    details states a number (a model told of a cap declines the work)."""
    if not subject_rows:
        return RemindRefusal("no_citations", "a reminder must cite at least one row")
    if intent not in INTENTS:
        return RemindRefusal("bad_intent", f"intent must be one of {INTENTS}")
    ctx = context or RemindContext()
    if not ctx.room_proactive:
        return RemindRefusal(
            "room_not_proactive", "you may not reach that room unasked",
        )
    if ctx.abduced_subject and intent != "ask":
        return RemindRefusal(
            "abduced_must_ask",
            "a row you cite is only your best explanation, not something anyone said: "
            "ask the room about it, do not tell it",
        )
    duplicate = any(
        p.platform == platform and p.conversation_id == conversation_id
        and set(p.subject_rows) == set(subject_rows)
        for p in pending
    )
    if duplicate:
        return RemindRefusal(
            "duplicate_pending", "an identical reminder is already pending for this room",
        )
    if (
        ctx.max_acts_per_room_per_day is not None
        and ctx.acts_in_room_today >= ctx.max_acts_per_room_per_day
    ):
        return RemindRefusal(
            "room_daily_cap", "that room has already been reached enough today",
        )
    if ctx.max_pending is not None and len(pending) >= ctx.max_pending:
        return RemindRefusal(
            "pending_ceiling", "you already have enough reminders waiting to be sent",
        )
    return None


def check_remind_move(
    *, reminder_ref: str, pending: Sequence[PendingMirroredRemind],
    created_by_session_id: int | None,
) -> RemindRefusal | None:
    row = next((p for p in pending if p.reminder_ref == reminder_ref), None)
    if row is None:
        return RemindRefusal("not_found", f"no pending reminder {reminder_ref!r}")
    if (
        created_by_session_id is not None
        and row.created_by_session_id is not None
        and row.created_by_session_id != created_by_session_id
    ):
        return RemindRefusal(
            "not_own",
            "you may only move or cancel a reminder you set yourself; a person's own "
            "reminder is theirs to change",
        )
    return None


def check_remind_cancel(
    *, reminder_ref: str, pending: Sequence[PendingMirroredRemind],
    created_by_session_id: int | None,
) -> RemindRefusal | None:
    return check_remind_move(
        reminder_ref=reminder_ref, pending=pending,
        created_by_session_id=created_by_session_id,
    )
