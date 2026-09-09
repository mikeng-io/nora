"""The fire-time screen for a `kind="proactive"` reminder row
(design/dreamer-agent-loop/39 D5 and D11, DEC-162).

WHAT IT ANSWERS
---------------
Whether a due proactive row may reach a fire turn at all, and — when it may
not — what the row should say about why. Two questions, both asked at FIRE
time and neither at create time, for `nora.app.heartbeat`'s own reason: the
row was written hours ago by a decide turn reading the world as it was then.

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
   `CANCELLED` / `cancelled_note_retired` or `cancelled_note_moved`. Not
   «any head move»: the debrief's note sink appends revisions with a run-id
   `established_by` on ordinary tag turns, so a row cancelled by a
   paraphrase would cancel on every tag. `tag_path_progressed_since_wake`
   is the domain's own predicate for exactly this distinction, walked over
   `revision_chain`.

No model is called for a refused row; the heartbeat resolves it and moves
on. A row with no note in its payload is screened on the flag alone.

FAIL-CLOSED, and the three states are kept apart. A note store that is not
wired, a note that cannot be read, a note the row's workspace cannot reach,
or a policy that cannot name the room's workspace is «I could not check»,
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
        self, *, workspace: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...

    async def revision_chain(
        self, *, workspace: str, note_id: str,
    ) -> tuple[NoteRevision, ...]: ...


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
        note_id = payload.get("note_id")
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
            current = await self._notes.read(workspace=convo.workspace, note_id=note_id)
            chain = () if current is None else await self._notes.revision_chain(
                workspace=convo.workspace, note_id=note_id,
            )
        except Exception:
            log.exception(
                "proactive reminder %s: note %s could not be read; not firing "
                "on an unverified premise", row.id, note_id,
            )
            return ReminderState.FAILED, FAILED_ERROR
        if current is None:
            # `read` answers None for absent, for another workspace, and for
            # an id that is not UUID-shaped — «not reachable from here», which
            # is not «retired». A workspace renamed in policy.toml between
            # decide and fire lands here; claiming the note was retired would
            # be a fact about the note nobody checked. Not fired, not
            # cancelled: `failed_error`, the wiring-gap outcome.
            log.error(
                "proactive reminder %s names note %s, which workspace %r cannot "
                "reach; not firing on an unverified premise",
                row.id, note_id, convo.workspace,
            )
            return ReminderState.FAILED, FAILED_ERROR
        if current[0].retired_at is not None:
            return ReminderState.CANCELLED, CANCELLED_NOTE_RETIRED
        moved = tag_path_progressed_since_wake(
            head_revision_id=current[0].head_revision_id,
            last_wake_revision_id=linked_at,
            chain={r.id: (r.parent_revision_id, r.established_by) for r in chain},
        )
        if moved:
            return ReminderState.CANCELLED, CANCELLED_NOTE_MOVED
        return None
