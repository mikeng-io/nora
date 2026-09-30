"""`remind`/`remind_move`/`remind_cancel`, mirrored and held (pure checks;
design/cognition-loop/30-cognition.md §2-§3; migration step 3a-ii).

**Shadow before cut-over** (70-migration.md): every reminder Cognition would
write is recorded here instead of reaching the fire path, and never fires.
This module checks the subset of §3's "enforced by code on every act" rules
that do NOT need a live room read (disclosure at assembly, rate caps and
`dream_screen` all need state this module is never handed, by the "prior is
a parameter" choice every dry-run module in this tree makes — those stay the
caller's job at cut-over, and are out of scope for a call that is never
delivered): non-empty citations, and the dedupe rule ("one pending act per
(subject rows, addressee)"), and "cognition never cancels a reminder a
person set" / "own misreads only" for `remind_move`/`remind_cancel`.
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
    return tuple(p for p, at in live.values() if at >= now_ms)


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


def check_remind(
    *, subject_rows: Sequence[str], intent: str, platform: str, conversation_id: str,
    pending: Sequence[PendingMirroredRemind],
) -> RemindRefusal | None:
    """`remind(at, room, brief)`. `brief` is `(intent, subject_rows)` — never
    prose (30-cognition.md §2)."""
    if not subject_rows:
        return RemindRefusal("no_citations", "a reminder must cite at least one row")
    if intent not in INTENTS:
        return RemindRefusal("bad_intent", f"intent must be one of {INTENTS}")
    duplicate = any(
        p.platform == platform and p.conversation_id == conversation_id
        and set(p.subject_rows) == set(subject_rows)
        for p in pending
    )
    if duplicate:
        return RemindRefusal(
            "duplicate_pending", "an identical reminder is already pending for this room",
        )
    return None


def check_remind_move(
    *, reminder_ref: str, pending: Sequence[PendingMirroredRemind],
    created_by_session_id: int | None,
) -> RemindRefusal | None:
    row = next((p for p in pending if p.reminder_ref == reminder_ref), None)
    if row is None:
        return RemindRefusal("not_found", f"no pending mirrored reminder {reminder_ref!r}")
    if (
        created_by_session_id is not None
        and row.created_by_session_id is not None
        and row.created_by_session_id != created_by_session_id
    ):
        return RemindRefusal(
            "not_own",
            "cognition never moves a reminder it did not write itself "
            "(30-cognition.md §3 rule 12; a person's own reminder is the "
            "tag path's to cancel, never cognition's)",
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
