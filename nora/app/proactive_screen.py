"""The fire-time screen for a `kind="proactive"` reminder row
(DEC-162).

WHAT IT ANSWERS
---------------
Whether a due proactive row may reach a fire turn at all, and — when it may
not — what the row should say about why. Two questions, both asked at FIRE
time and neither at create time, for `nora.app.heartbeat`'s own reason: the
row was written hours earlier (by the Cognition session's `remind`) reading
the world as it was then.

1. **The room's grant.** `is_allowed` is (room, person), and a proactive
   row's owner is Nora's reserved `ai_peer` id — nobody in `identity.toml`,
   by construction (`composition.py` reserves the id at startup). So the
   grant is the room's `proactive = true` flag (`policy.toml`), read through
   the same `Policy` the heartbeat re-checks ordinary rows against. Refused:
   `FAILED` / `failed_policy`, the ordinary revoked-trust outcome.

2. **The note the row is about.** `remind` under Dreamer provenance stamps
   the note's id and the HEAD revision it was linked at into the payload.
   If the note has since been RETIRED, or the TAG PATH has revised it past
   that revision, the premise of the row is gone — a human settled the
   thing at 11:00, so Nora does not raise it at 14:00. Refused:
   `CANCELLED` / `cancelled_note_retired` or `cancelled_note_moved`. A
   note retired `retired-merged` is not gone: its question was folded into
   another open note, so the row FOLLOWS that survivor —
   it fires unless the survivor was revised on the tag path after the row
   was written, or the chain ends on no open note. Not
   «any head move»: the debrief's note sink appends revisions with a run-id
   `established_by` on ordinary tag turns, so a row cancelled by a
   paraphrase would cancel on every tag. `tag_path_progressed_since_wake`
   is the domain's own predicate for exactly this distinction, walked over
   `revision_chain`.

No model is called for a refused row; the heartbeat resolves it and moves
on. A row with no note in its payload is screened on the flag alone.

FAIL-CLOSED, and the three states are kept apart. A note store that is not
wired, a note that cannot be read, a note the row's unit cannot reach,
or a policy that cannot name the room's unit is «I could not check»,
and a row that cannot be screened does not fire: `FAILED` / `failed_error`,
logged, never `cancelled` (which would claim the note moved or was retired)
and never admitted (which would speak on a premise nobody verified).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

from nora.domain.notes import (
    Note,
    NoteRevision,
    NoteSupersedeReason,
    proactive_row_forwarded_is_moved,
    tag_path_progressed_since_wake,
)
from nora.domain.reminders import Reminder, ReminderState
from nora.policy import Conversation

log = logging.getLogger("nora.app.proactive_screen")

#: The reminder `kind` the proactive adaptor's `remind` stamps and this
#: screen answers for. One spelling, imported by the heartbeat and the tool
#: layer's tests, so the two cannot drift.
PROACTIVE_KIND = "proactive"

#: `nora_reminders_fired_total{outcome}` values this screen can write.
CANCELLED_NOTE_RETIRED = "cancelled_note_retired"
CANCELLED_NOTE_MOVED = "cancelled_note_moved"
FAILED_POLICY = "failed_policy"
FAILED_ERROR = "failed_error"


class _Policy(Protocol):
    """The two `Policy` reads this screen makes, named so a wiring mistake is
    loud: the ENGAGE policy has neither."""

    def conversation(self, platform: str, conversation_id: str) -> Conversation | None: ...

    def proactive_allowed(self, *, platform: str, conversation_id: str) -> bool: ...


class _Notes(Protocol):
    """The two note-store reads this screen makes."""

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...

    async def revision_chain(
        self, *, unit: str, note_id: str,
    ) -> tuple[NoteRevision, ...]: ...

    async def successor(self, *, unit: str, note_id: str) -> Note | None: ...


class ProactiveScreen:
    """`refusal(row)` answers `None` (fire it) or `(state, outcome)` (resolve
    it so). Collaborators through `__init__` only."""

    def __init__(self, *, policy: _Policy, notes: _Notes | None) -> None:
        self._policy = policy
        # `None` is the note tier switched off. A proactive row linked to a
        # note then cannot be screened and does not fire (module docstring);
        # a row with no note is unaffected.
        self._notes = notes

    async def refusal(self, row: Reminder) -> tuple[ReminderState, str] | None:
        if not self._policy.proactive_allowed(
            platform=row.platform, conversation_id=row.conversation_id,
        ):
            return ReminderState.FAILED, FAILED_POLICY
        payload: Mapping[str, str] = row.payload if isinstance(row.payload, Mapping) else {}
        # New-shape rows name their subject, not a note link: `note:`-subject
        # rows carry the note's id as `subject_id`, which is what the moved-
        # note screen below keys on.
        note_id = payload.get("note_id") or (
            payload.get("subject_id")
            if payload.get("subject_kind") == "note" else None
        )
        if not note_id:
            return None
        linked_at = payload.get("revision_id") or None
        if self._notes is None:
            log.error(
                "proactive reminder %s names note %s but no note store is "
                "wired; it cannot be screened and will not fire", row.id, note_id,
            )
            return ReminderState.FAILED, FAILED_ERROR
        convo = self._policy.conversation(row.platform, row.conversation_id)
        if convo is None:
            # `proactive_allowed` just said the room is listed, so this is a
            # policy that changed under us mid-screen — treated as the
            # wiring gap it is, not as a moved note.
            return ReminderState.FAILED, FAILED_ERROR
        try:
            current = await self._notes.read(unit=convo.unit, note_id=note_id)
            chain = () if current is None else await self._notes.revision_chain(
                unit=convo.unit, note_id=note_id,
            )
        except Exception:
            log.exception(
                "proactive reminder %s: note %s could not be read; not firing "
                "on an unverified premise", row.id, note_id,
            )
            return ReminderState.FAILED, FAILED_ERROR
        if current is None:
            # `read` answers None for absent, for another unit, and for
            # an id that is not UUID-shaped — «not reachable from here», which
            # is not «retired». A unit renamed in policy.toml between
            # decide and fire lands here; claiming the note was retired would
            # be a fact about the note nobody checked. Not fired, not
            # cancelled: `failed_error`, the wiring-gap outcome.
            log.error(
                "proactive reminder %s names note %s, which unit %r cannot "
                "reach; not firing on an unverified premise",
                row.id, note_id, convo.unit,
            )
            return ReminderState.FAILED, FAILED_ERROR
        merged = current[0].retire_reason == NoteSupersedeReason.RETIRED_MERGED
        if current[0].retired_at is not None and not merged:
            return ReminderState.CANCELLED, CANCELLED_NOTE_RETIRED
        duplicate_chain = {r.id: (r.parent_revision_id, r.established_by) for r in chain}
        # The row's OWN note first, open or merged, so a row whose own note
        # moved reads `cancelled_note_moved` whatever later happened to its
        # survivor (running this only inside `_follow_survivor` would let a
        # retired or unreadable survivor change the label). For a
        # merged note, `proactive_row_forwarded_is_moved` repeats this step
        # and finds nothing; the shared function stays the one definition.
        if tag_path_progressed_since_wake(
            head_revision_id=current[0].head_revision_id,
            last_wake_revision_id=linked_at, chain=duplicate_chain,
        ):
            return ReminderState.CANCELLED, CANCELLED_NOTE_MOVED
        if current[0].retired_at is None:
            return None
        return await self._follow_survivor(
            row, unit=convo.unit, note_id=note_id,
            duplicate_head=current[0].head_revision_id,
            duplicate_chain=duplicate_chain, linked_at=linked_at,
        )

    async def _follow_survivor(
        self, row: Reminder, *, unit: str, note_id: str,
        duplicate_head: str, duplicate_chain: Mapping[str, tuple[str | None, str]],
        linked_at: str | None,
    ) -> tuple[ReminderState, str] | None:
        """A row on a `retired-merged` note is screened against the open note
        its question was folded into: re-pointed in effect,
        not cancelled. `proactive_row_forwarded_is_moved` is the two-step
        check — the DUPLICATE's own chain (an ancestry walk from
        `duplicate_head`/`duplicate_chain`, the caller's own read, back to
        `linked_at`) first, THEN the survivor's (a scan by time, since
        nothing links this row to any revision of the survivor — it was
        never about the survivor). `SqlNoteStore.merge_into`'s re-point
        calls the SAME function, which is why it is a shared domain
        function rather than inlined twice: inlining only the second step
        lets a stale reminder fire."""
        if self._notes is None:     # pragma: no cover — checked by the caller
            return ReminderState.FAILED, FAILED_ERROR
        try:
            survivor = await self._notes.successor(unit=unit, note_id=note_id)
            chain = () if survivor is None else await self._notes.revision_chain(
                unit=unit, note_id=survivor.id,
            )
        except Exception:
            log.exception(
                "proactive reminder %s: the survivor of merged note %s could "
                "not be read; not firing on an unverified premise",
                row.id, note_id,
            )
            return ReminderState.FAILED, FAILED_ERROR
        if survivor is None:
            return ReminderState.CANCELLED, CANCELLED_NOTE_RETIRED
        linked_ms = int(row.created_at.timestamp() * 1000)
        moved = proactive_row_forwarded_is_moved(
            duplicate_head=duplicate_head, duplicate_chain=duplicate_chain,
            revision_id=linked_at, created_at_ms=linked_ms,
            survivor_chain=((r.written_at, r.established_by) for r in chain),
        )
        if moved:
            return ReminderState.CANCELLED, CANCELLED_NOTE_MOVED
        return None
