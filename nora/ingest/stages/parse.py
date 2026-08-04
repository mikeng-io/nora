"""Stage 1 — turn the raw platform event into a `NormalizedMessage`.

Delegates to the adapter. `nora.domain.ports.PlatformAdapter` deliberately has
no `parse()` — signal-cli's SSE envelopes, Discord's gateway events and
Telegram's update batches don't share a dict shape, so parsing stays
per-adapter and is tested directly against its own transport (e.g.
`platform/signal/parser.py`). This stage is a thin call-through, not a parser.

On failure (spec §4.3, row 1): the adapter returns `None` for anything it
declines to normalize — a DM, a sync message, a self-send, an empty body —
and the message is dropped silently. There is nothing to log here: these are
expected outcomes, not exceptional ones.
"""

from __future__ import annotations

from typing import Any, Protocol

from nora.domain.models import NormalizedMessage


class ParsingAdapter(Protocol):
    """The slice of the adapter this stage needs. `PlatformAdapter` (the
    domain port) has no `parse()` — see module docstring — so this stage
    declares its own narrow shape rather than depending on that port.
    Public (not `_`-prefixed) because `nora.ingest.pipeline` reuses it as
    `IngestPipeline`'s `adapter` parameter type."""

    async def parse(self, raw_event: dict) -> NormalizedMessage | None: ...


async def run(
    *, adapter: ParsingAdapter, raw_event: dict, metrics: Any
) -> NormalizedMessage | None:
    """Return the normalized message, or `None` if this event should be
    dropped silently."""
    message = await adapter.parse(raw_event)
    if message is None:
        return None
    metrics.inc("nora_messages_total", platform=message.platform)
    return message
