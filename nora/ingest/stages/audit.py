"""The audit write. Failure drops the message; it never re-raises.

`on_event` is awaited by the SSE reader (`platform/signal/listener.py`), so a
raise here would stop ingest for every conversation on this adapter. Dropping
one message is the smaller loss, and it is counted so the loss is visible.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.ports import RecordStore

log = logging.getLogger("nora.ingest.audit")


async def run(
    *,
    message: NormalizedMessage,
    audit: RecordStore,
    metrics: Any,
    person: PersonRef | None = None,
    tx_hook: Any = None,
    absorbed: bool = True,
    decision: Any = None,
    unit: str | None = None,
) -> int | None:
    """Return the recorded row's id, or None when the message was dropped and
    ingest should stop. The id is what the stimulus stage names the Record
    item by (`messages:<id>`).

    `person` is already resolved (identity runs first) so it lands in this one
    INSERT rather than a follow-up UPDATE — WAL allows a single writer and this
    path drops the message on contention.

    `tx_hook` is the durable caller's same-commit write; it is invoked as
    `tx_hook(conn, row_id, context)` where `context` carries the canonical
    handoff facts the caller cannot otherwise see inside the append's
    transaction: the custody-enriched `message`, the resolved `person`, the
    admission-time `absorbed` flag, the room's `unit` at admission (deferred
    writers pin their scope to it — a moved unit binding can never route old
    content into a new unit) and — when the caller asked for the gate
    first (durable) — the `decision`."""
    try:
        inner = (
            None if tx_hook is None else
            lambda conn, row_id: tx_hook(
                conn, row_id, {"message": message, "person": person,
                               "absorbed": absorbed, "decision": decision,
                               "unit": unit})
        )
        return await audit.append(
            message=message, person_id=person.person_id if person else None,
            tx_hook=inner,
        )
    except Exception:
        log.exception("audit append failed; dropping message")
        metrics.inc("nora_errors_total", stage="ambient", platform=message.platform)
        return None
