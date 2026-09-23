"""The edit path's first stage — which recorded message does this edit change,
and may it change it? (design/message-edits/02 §4)

Runs after the room gate and before anything is written. It answers one of
four things, and the pipeline acts on each:

* **found** — the original is on record and the editor is its author. The
  returned message carries the original's sender when the platform named none
  (Instagram's edit delta carries no sender at all), so identity resolves
  exactly as it did for the original.
* **orphan, ingestable** — nothing on record, but the platform named the
  sender. The edit is the only version Nora will ever see, so the pipeline
  ingests it as an ordinary message — what Signal and WhatsApp did with every
  edit before this path existed.
* **`edit_orphaned`** — nothing on record and no sender to attribute it to.
  Dropped and counted.
* **`edit_refused`** — the original is Nora's own (R-12: she never ingests her
  own words, edited or not), or somebody other than its author is editing it.
  An edit is not a way to put words under someone else's row, and platforms
  enforce authorship themselves, so a mismatch here is a malformed or forged
  event rather than a member's action.

Authorship is compared on the platform id first and on the resolved person
second. The second is what lets a WhatsApp sender whose original arrived under
their phone form and whose edit arrived under their LID still edit their own
message: both forms resolve to one person (`identity.toml` maps each account).
An UNMAPPED sender whose two forms differ is refused — the one case where
fail-closed costs a legitimate edit, and the cost is the edit, not the message.

A lookup failure is an ERROR, not an orphan: an orphan is ingested as a new
message, and treating «could not look» as «not there» would write a duplicate
row every time the store hiccuped. It drops the edit and counts it.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Any, Literal

from nora.domain.models import EditedMessage, EditTarget, NormalizedMessage
from nora.domain.ports import RecordStore
from nora.persons import Persons

log = logging.getLogger("nora.ingest.edit_target")

#: The two endings this stage can decide by itself; `None` means «go on».
EditTargetEnding = Literal["edit_orphaned", "edit_refused"]


@dataclass(frozen=True)
class EditTargetResult:
    """`original` set and `ending` None: fold onto it. Both None: an orphan
    the pipeline ingests as an ordinary message. `ending` set: stop there."""

    message: NormalizedMessage
    original: EditedMessage | None
    ending: EditTargetEnding | None


async def run(
    *, message: NormalizedMessage, target: EditTarget, audit: RecordStore,
    persons: Persons, metrics: Any,
) -> EditTargetResult:
    """`target` is `message.edit_of`, passed on its own so its presence is a
    type rather than a runtime check."""
    try:
        original = await audit.find_edited(
            conversation_id=message.conversation_id, target=target,
            sender_id=message.sender_id,
        )
    except Exception:
        log.exception("edit target lookup failed; dropping the edit")
        metrics.inc("nora_errors_total", stage="edit_target", platform=message.platform)
        return EditTargetResult(message=message, original=None, ending="edit_orphaned")

    if original is None:
        if message.sender_id:
            return EditTargetResult(message=message, original=None, ending=None)
        return EditTargetResult(message=message, original=None, ending="edit_orphaned")

    if original.is_nora or not _same_author(message, original, persons):
        return EditTargetResult(message=message, original=original, ending="edit_refused")

    if not message.sender_id:
        message = dataclasses.replace(message, sender_id=original.sender_id)
    return EditTargetResult(message=message, original=original, ending=None)


def _same_author(
    message: NormalizedMessage, original: EditedMessage, persons: Persons,
) -> bool:
    """An edit that names no sender cannot contradict the original's; one
    that does must be the same account, or the same person."""
    if not message.sender_id or message.sender_id == original.sender_id:
        return True
    editor = persons.resolve(message.platform, message.sender_id)
    return (
        editor is not None
        and original.person_id is not None
        and editor.person_id == original.person_id
    )
