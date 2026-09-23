"""An edit's attachments — given the handles Nora already holds, not fetched
again (design/message-edits/02 §4).

No network lets an edit ADD media: Signal, WhatsApp and Instagram edit text or
a caption, and Discord can only remove attachments. So an edit that carries as
many attachments as the original holds blobs for gets the original's handles
back on its placeholders, and nothing is downloaded. Running custody instead
(the first draft) re-fetched every attachment on every update — a Discord pin
of an edited message included — and a re-fetch that failed left `[image]`
where the record said `[image a3f2c1]`, which then compared as a CHANGE: the
edit was recorded, fed to Honcho as a correction, and the handle was gone
from the transcript.

When the counts differ (an attachment removed, or one the original never
held), custody runs as for any message, because there is no positional match
to trust.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable

from nora.domain.media import blobs_from_media_json, body_with_handles, label_for
from nora.domain.models import EditedMessage, NormalizedMessage


async def run(
    *, message: NormalizedMessage, original: EditedMessage,
    custody: Callable[[NormalizedMessage], Awaitable[NormalizedMessage]],
) -> NormalizedMessage:
    if not message.media:
        return message
    held = blobs_from_media_json(original.media_json)
    if len(held) != len(message.media):
        return await custody(message)
    labels = tuple(label_for(ref) for ref in message.media)
    return dataclasses.replace(
        message,
        body=body_with_handles(message.body, labels, tuple(b.handle for b in held)),
        media_blobs=held,
    )
