"""The edit path's first stage — which recorded message does this edit change,
and may it change it? (design/message-edits/02 §4)

Runs after the room gate and before anything is written. It answers one of
three things, and the pipeline acts on each:

* **found** — the original is on record and the editor is its author. The
  returned message carries what the platform left out from the original:
  the sender when it named none (Instagram's edit delta carries no sender at
  all), and the mentions when it could not see them (the same delta carries
  none), so identity and the gate compute on the same facts the original had.
* **`edit_orphaned`** — nothing on record. Dropped and counted, whoever the
  platform says sent it. An orphan is an edit to something Nora never
  recorded — pruned past the retention window, from before she joined, from
  before the room was allowlisted — or, on Discord, an update racing the
  message it updates. Ingesting it as a new message (the first draft of this
  stage) put an OLD message at the bottom of the transcript as if said just
  now, where a tag in it opened a turn on something long settled, and a race
  wrote a duplicate row. Losing the rare genuine edit of an unrecorded message
  is the smaller cost.
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

A lookup failure drops the edit too, and counts an error besides: «could not
look» is not «not there», and the error counter is what tells them apart.
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
    """`ending` None: fold onto `original`, which is then always set.
    `ending` set: stop there."""

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
        return EditTargetResult(message=message, original=None, ending="edit_orphaned")

    if original.is_nora or not _same_author(message, original, persons):
        return EditTargetResult(message=message, original=original, ending="edit_refused")

    if not message.sender_id:
        message = dataclasses.replace(message, sender_id=original.sender_id)
    if not target.mentions_known and original.mentions is not None:
        # The platform could not see mentions on the edit; carry the
        # original's, and say they are now known so the edit row records them.
        message = dataclasses.replace(
            message, mentions=original.mentions,
            edit_of=dataclasses.replace(target, mentions_known=True),
        )
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
