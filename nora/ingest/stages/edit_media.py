"""An edit's attachments — given the handles Nora already holds, not fetched
again.

No network lets an edit ADD media: Signal, WhatsApp and Instagram edit text or
a caption, and Discord can only remove attachments. So an edit that carries as
many attachments as the original holds blobs for gets the original's handles
back on its placeholders, and nothing is downloaded. Running custody instead would re-fetch every
attachment on every update — a
Discord pin of an edited message included — and a re-fetch that failed would
leave `[image]` where the record said `[image a3f2c1]`, which then compares as
a CHANGE: the edit would be recorded, fed to Honcho as a correction, and the
handle lost from the transcript.

When the counts differ (an attachment removed, or one the original never
held), custody runs as for any message, because there is no positional match
to trust.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable

from nora.domain.media import (
    blobs_from_media_json,
    body_with_handles,
    label_for,
    placeholders_in,
    render_placeholder,
)
from nora.domain.models import EditedMessage, NormalizedMessage


async def run(
    *, message: NormalizedMessage, original: EditedMessage,
    custody: Callable[[NormalizedMessage], Awaitable[NormalizedMessage]],
) -> NormalizedMessage:
    if not message.media:
        return message
    held = blobs_from_media_json(original.media_json)
    # `media_json` lists only the blobs Nora HOLDS — a failed download is a
    # gap, dropped — so it is positional only when the original held EVERY
    # attachment: all its placeholders carry a handle, and there are as many
    # as there are blobs. A bare placeholder (`[image]`, no handle) in the
    # original IS such a gap — `placeholders_in` only sees handled ones, so
    # it is looked for directly. Otherwise a removed attachment
    # could hand its neighbour's handle to the wrong placeholder.
    labels = tuple(label_for(ref) for ref in message.media)
    first = original.first_body or ""
    placed = placeholders_in(first)          # the placeholders that carry a handle
    gap = any(render_placeholder(label) in first for label in set(labels))
    if len(held) != len(message.media) or len(placed) != len(held) or gap:
        return await custody(message)
    return dataclasses.replace(
        message,
        body=body_with_handles(message.body, labels, tuple(b.handle for b in held)),
        media_blobs=held,
    )
