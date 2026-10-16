"""The touch path's stage — a reaction to, or a delete/unsend of, a recorded
message: find it, and record the touch beside it.

Neither is a message (`nora.domain.models.Touch`), so neither becomes a
`messages` row, a transcript line, a Honcho feed or a turn. Each is a Record
item attached to the message it touches (`message_reaction`,
`message_delete`), because the cited-message cues and the tie need them.

A delete of a message on record also HIDES it (`RecordStore.hide_message`,
`messages.hidden_at`): every replay and render reads through `transcript_select`,
which excludes a hidden row, so the message is no longer shown or fed to memory.
That is all a delete does. Honcho keeps what it was already fed (it has no
per-message delete), and nothing derived from the message is marked or revisited
(AGENTS.md «A guard needs a consequence»). A delete whose target is not on record
is dropped, as a reaction is. A reaction hides nothing.

Four endings, each counted on `nora_ingest_outcomes_total`:

* **`<kind>_recorded`** — the target is on record and the row is written.
* **`<kind>_orphaned`** — nothing on record to attach it to (a message from
  before Nora joined, pruned, or never recorded). Dropped, for
  `edit_target`'s reason: a touch of nothing cannot be attached, and a
  lookup that FAILED counts an error besides — «could not look» is not «not
  there».
* **`delete_refused`** — a delete on a platform where only a message's author
  can delete it (Signal's remote delete), naming a message whose author is
  not the deleter: a malformed or forged event, refused as
  `edit_target` refuses an edit by someone else. Authorship is the platform
  id first, the resolved person second (one person, two id forms).
* **`audit_refused`** — the write failed; dropped and counted, never raised
  (this runs on the listener's await path, like the audit write).

The target is found by the lookup an edit uses. On Signal the target's
AUTHOR is preferred when two rows share a timestamp — a reaction names whose
message it is — not the reactor. Who acted is the platform's sender; where it
names nobody (an Instagram unsend, a Discord delete, which a moderator may
have made) the row keeps `sender_id = ""` AND `person_id = NULL`: the
platform did not say who, and the Record does not assert it. (The stimulus
stage still treats an unattributed delete of Nora's OWN message as hers, so
it raises no stimulus; that is a judgement about waking, not a Record claim.)
"""

from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from nora.domain.models import EditedMessage, NormalizedMessage, Touch
from nora.domain.ports import RecordStore
from nora.persons import Persons

log = logging.getLogger("nora.ingest.touch")

TouchEnding = Literal[
    "reaction_recorded", "reaction_orphaned",
    "delete_recorded", "delete_orphaned", "delete_refused",
    "audit_refused",
]

#: Platforms where only a message's author can delete it for everyone, so a
#: delete naming someone else's message is not a member's action.
_AUTHOR_ONLY_DELETES: frozenset[str] = frozenset({"signal"})


@dataclass(frozen=True)
class TouchResult:
    """`row_id` set only when the touch was recorded; `target` whenever it
    was found."""

    ending: TouchEnding
    message: NormalizedMessage
    target: EditedMessage | None = None
    row_id: int | None = None
    person_id: str | None = None


async def run(
    *, message: NormalizedMessage, touch: Touch, audit: RecordStore,
    persons: Persons, metrics: Any,
) -> TouchResult:
    """`touch` is `message.touch`, passed on its own so its presence is a
    type rather than a runtime check."""
    orphaned: TouchEnding = (
        "reaction_orphaned" if touch.kind == "reaction" else "delete_orphaned"
    )
    if message.timestamp <= 0:
        # A platform that gives no time for the touch: stamped on ARRIVAL,
        # seconds late, never wrong in kind (the edit path's fallback).
        message = dataclasses.replace(message, timestamp=int(time.time() * 1000))
    try:
        target = await audit.find_edited(
            conversation_id=message.conversation_id, target=touch.target,
            sender_id=touch.target_author or message.sender_id,
        )
    except Exception:
        log.exception("%s target lookup failed; dropping it", touch.kind)
        metrics.inc("nora_errors_total", stage="touch_target", platform=message.platform)
        return TouchResult(ending=orphaned, message=message)
    if target is None:
        return TouchResult(ending=orphaned, message=message)

    person = persons.resolve(message.platform, message.sender_id) if message.sender_id else None
    person_id = person.person_id if person is not None else None
    if (
        touch.kind == "delete" and message.platform in _AUTHOR_ONLY_DELETES
        and not _same_author(message.sender_id, person_id, target)
    ):
        return TouchResult(ending="delete_refused", message=message, target=target)
    try:
        if touch.kind == "reaction":
            row_id = await audit.append_reaction(
                message_id=target.id, message=message, person_id=person_id,
            )
        else:
            row_id = await audit.append_delete(
                message_id=target.id, message=message, person_id=person_id,
            )
    except Exception:
        log.exception("%s append failed; dropping it", touch.kind)
        metrics.inc("nora_errors_total", stage="touch_record", platform=message.platform)
        return TouchResult(ending="audit_refused", message=message, target=target)
    if touch.kind == "delete":
        try:
            await audit.hide_message(message_id=target.id, now_ms=int(time.time() * 1000))
        except Exception:
            # The record above is written; the message stays visible until the next
            # delete event for it. Counted, never raised (the listener's await path).
            log.exception("hiding message %d failed", target.id)
            metrics.inc("nora_errors_total", stage="touch_hide", platform=message.platform)
    return TouchResult(
        ending="reaction_recorded" if touch.kind == "reaction" else "delete_recorded",
        message=message, target=target, row_id=row_id, person_id=person_id,
    )


def _same_author(sender_id: str, person_id: str | None, target: EditedMessage) -> bool:
    """`edit_target._same_author`'s rule: the same platform id, or the same
    resolved person (a Signal sender seen by phone once and by ACI once)."""
    if sender_id and sender_id == target.sender_id:
        return True
    return person_id is not None and person_id == target.person_id
