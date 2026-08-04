"""Stage 2 — the audit write. Failure drops the message; it never re-raises.

`on_event` is awaited by the SSE reader (`platform/signal/listener.py`), so a
raise here would stop ingest for every conversation on this adapter. Dropping
one message is the smaller loss, and it is counted so the loss is visible.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import AuditStore

log = logging.getLogger("nora.ingest.audit")


async def run(*, message: NormalizedMessage, audit: AuditStore, metrics: Any) -> bool:
    """Return True if the message was recorded and ingest should continue."""
    try:
        await audit.append(message=message)
    except Exception:
        log.exception("audit append failed; dropping message")
        metrics.inc("nora_errors_total", stage="ambient")
        return False
    return True
