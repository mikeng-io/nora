"""The edit path's write — one `message_edit` row beside the message it edits,
unless the text did not change.

**Unchanged is decided on the text, after custody, and that is what makes it
total.** Discord fires `MESSAGE_UPDATE` when a link preview resolves or a
message is pinned, with the words untouched; a bridge can redeliver an edit
after a reconnect. Filtering on an event TYPE would need a rule per platform
and per quirk; comparing the body custody just produced against the version it
would replace needs none. After custody, not before, because the stored body
carries attachment handles and the parser's does not — compared earlier, every
update to a message with a photo in it would look like an edit.

**Unchanged is not the same as unrecorded.** An edit whose text AND mentions
match the version it replaces (a style-only Signal edit, an update that
changed nothing a reader sees) is still WRITTEN when it is newer: a Signal
client addresses its next edit by this edit's timestamp, and an edit left
unrecorded made every later one an orphan. It opens no turn and feeds Honcho
nothing. Only a true duplicate — the same time and the same text as the
version on record — writes nothing at all.

Mentions count as a change on their own: in a Signal body every mention is
one placeholder character, so «@Alice hi» → «@Nora hi» compares equal as
text. Staleness is decided one stage earlier (`edit_target._is_stale`), before
custody.

Like the audit stage, a failed write drops the edit and never re-raises: this
runs on the listener's await path.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from nora.domain.models import EditedMessage, NormalizedMessage
from nora.domain.ports import RecordStore

log = logging.getLogger("nora.ingest.edit_record")

#: `recorded_unchanged` is written but changes nothing a reader sees; the
#: pipeline counts it `edit_unchanged` and stops there.
EditRecordOutcome = Literal[
    "recorded", "recorded_unchanged", "edit_unchanged", "audit_refused",
]


async def run(
    *, message: NormalizedMessage, original: EditedMessage, audit: RecordStore,
    metrics: Any,
) -> EditRecordOutcome:
    same_text = (message.body or "") == (original.body or "")
    same_mentions = (
        original.mentions is None
        or (message.edit_of is not None and not message.edit_of.mentions_known)
        or message.mentions == original.mentions
    )
    unchanged = same_text and same_mentions
    if unchanged and message.timestamp == (original.edited_ts or original.ts):
        return "edit_unchanged"
    try:
        await audit.append_edit(message_id=original.id, message=message)
    except Exception:
        log.exception("edit append failed; dropping the edit")
        metrics.inc("nora_errors_total", stage="edit_record", platform=message.platform)
        return "audit_refused"
    return "recorded_unchanged" if unchanged else "recorded"
