"""The edit path's first stage — which recorded message does this edit change,
and may it change it?

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
  message it updates. Ingesting it as a new message would put an OLD message at
  the bottom of the transcript as if said just now, where a tag in it would open
  a turn on something long settled, and a race would write a duplicate row.
  Losing the rare genuine edit of an unrecorded message is the smaller cost.
  The same ending when the original IS on record but withdrawn (a delete or
  unsend hid it, `EditedMessage.hidden`): an edit after a delete must not fold a
  new version onto a hidden row, feed Honcho a correction «of» it (which would
  quote what it first said), or open a turn on it. A late edit event for a
  message its author took back is the common way this arrives.
* **`edit_stale`** — older than the newest edit on record (see `_is_stale`).
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
import time
from dataclasses import dataclass
from typing import Any, Literal

from nora.domain.models import EditedMessage, EditTarget, NormalizedMessage
from nora.domain.ports import RecordStore
from nora.persons import Persons

log = logging.getLogger("nora.ingest.edit_target")

#: The two endings this stage can decide by itself; `None` means «go on».
EditTargetEnding = Literal["edit_orphaned", "edit_refused", "edit_stale"]


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
    if message.timestamp <= 0:
        # A parser with no time for the edit (WhatsApp and Signal fall back to
        # 0). Zero would render no `edited` marker and lose to every other
        # edit, so it is stamped on ARRIVAL — seconds late, never wrong in
        # kind, the fallback Instagram's parser already takes.
        message = dataclasses.replace(message, timestamp=int(time.time() * 1000))
    try:
        original = await audit.find_edited(
            conversation_id=message.conversation_id, target=target,
            sender_id=message.sender_id,
        )
    except Exception:
        log.exception("edit target lookup failed; dropping the edit")
        metrics.inc("nora_errors_total", stage="edit_target", platform=message.platform)
        return EditTargetResult(message=message, original=None, ending="edit_orphaned")

    if original is None or original.hidden:
        return EditTargetResult(message=message, original=None, ending="edit_orphaned")

    if original.is_nora or not _same_author(message, original, persons):
        return EditTargetResult(message=message, original=original, ending="edit_refused")

    if _is_stale(message, target, original):
        return EditTargetResult(message=message, original=original, ending="edit_stale")

    if not message.sender_id:
        message = dataclasses.replace(message, sender_id=original.sender_id)
    if not target.mentions_known:
        # The platform cannot see mentions on an edit (Instagram). The gate
        # compares against the original's, so it gets them — but the edit row
        # still records «unknown» (`mentions_known` stays False): nobody
        # observed what this version mentions, and recording the original's
        # as if they had would let the NEXT edit be judged against a guess.
        message = dataclasses.replace(message, mentions=original.mentions or frozenset())
    return EditTargetResult(message=message, original=original, ending=None)


def _is_stale(
    message: NormalizedMessage, target: EditTarget, original: EditedMessage,
) -> bool:
    """Older than the newest edit on record: a redelivery, or out of order.
    Decided HERE, before custody, so a stale update fetches nothing.

    Older than the NEWEST CLOCK on record — the maximum, not the version shown,
    which a skewed edit can make lower (judging against it would let a
    redelivered older edit bring old text back). An edit whose timestamp is
    already on record is a redelivery. And a Signal edit that names the latest
    revision's own timestamp is newer BY DEFINITION, whatever its clock says:
    each device stamps its own sends, so a linked desktop running behind the
    phone would otherwise have every edit it makes thrown away."""
    if message.timestamp in original.edit_timestamps:
        # Already on record: a redelivery. Checked FIRST, because the waiver
        # below must never re-admit an edit Nora already holds.
        return True
    if not original.edit_timestamps or message.timestamp >= max(original.edit_timestamps):
        return False
    return not (target.sent_ts is not None and target.sent_ts == original.edited_ts)


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
