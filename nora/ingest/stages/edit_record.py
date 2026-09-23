"""The edit path's write — one `message_edit` row beside the message it edits,
unless the text did not change (design/message-edits/02 §4).

**Unchanged is decided on the text, after custody, and that is what makes it
total.** Discord fires `MESSAGE_UPDATE` when a link preview resolves or a
message is pinned, with the words untouched; a bridge can redeliver an edit
after a reconnect. Filtering on an event TYPE would need a rule per platform
and per quirk; comparing the body custody just produced against the version it
would replace needs none. After custody, not before, because the stored body
carries attachment handles and the parser's does not — compared earlier, every
update to a message with a photo in it would look like an edit.

**Stale is decided on the clock.** An edit no newer than the newest one on
record is a redelivery or an out-of-order arrival: the transcript already
shows the later text, and recording it would feed Honcho an older version as
the latest correction.

Like the audit stage, a failed write drops the edit and never re-raises: this
runs on the listener's await path.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from nora.domain.models import EditedMessage, NormalizedMessage
from nora.domain.ports import RecordStore

log = logging.getLogger("nora.ingest.edit_record")

EditRecordOutcome = Literal["recorded", "edit_unchanged", "edit_stale", "audit_refused"]


async def run(
    *, message: NormalizedMessage, original: EditedMessage, audit: RecordStore,
    metrics: Any,
) -> EditRecordOutcome:
    if original.edited_ts is not None and message.timestamp <= original.edited_ts:
        return "edit_stale"
    if (message.body or "") == (original.body or ""):
        return "edit_unchanged"
    try:
        await audit.append_edit(message_id=original.id, message=message)
    except Exception:
        log.exception("edit append failed; dropping the edit")
        metrics.inc("nora_errors_total", stage="ambient", platform=message.platform)
        return "audit_refused"
    return "recorded"
