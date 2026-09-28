"""The touch path's stage — a reaction to, or a delete/unsend of, a recorded
message: find it, and record the touch beside it
(design/cognition-loop/20-observe.md §1, migration step 1).

Neither is a message (`nora.domain.models.Touch`), so neither becomes a
`messages` row, a transcript line, a Honcho feed or a turn. Each is a Record
item attached to the message it touches (`message_reaction`,
`message_delete`), because the withdrawal cascade (step 2c), the
cited-message cues and the tie need them.

Three endings, each counted on `nora_ingest_outcomes_total`:

* **`<kind>_recorded`** — the target is on record and the row is written.
* **`<kind>_orphaned`** — nothing on record to attach it to (a message from
  before Nora joined, pruned, or never recorded). Dropped, for
  `edit_target`'s reason: a touch of nothing cannot be attached, and a
  lookup that FAILED counts an error besides — «could not look» is not «not
  there».
* **`audit_refused`** — the write failed; dropped and counted, never raised
  (this runs on the listener's await path, like the audit write).

The target is found by the lookup an edit uses. On Signal the target's
AUTHOR is preferred when two rows share a timestamp — a reaction names whose
message it is — not the reactor. Who acted is the platform's sender; where it
names nobody (an Instagram unsend, a Discord delete) the row keeps `""` and
the person is the target's author: only an author can unsend on Instagram,
and on Discord (a moderator can delete) it is the closest honest answer, so
the row's `sender_id` stays empty rather than claim more.
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
    "delete_recorded", "delete_orphaned",
    "audit_refused",
]


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

    if message.sender_id:
        person = persons.resolve(message.platform, message.sender_id)
        person_id = person.person_id if person is not None else None
    else:
        person_id = target.person_id
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
    return TouchResult(
        ending="reaction_recorded" if touch.kind == "reaction" else "delete_recorded",
        message=message, target=target, row_id=row_id, person_id=person_id,
    )
