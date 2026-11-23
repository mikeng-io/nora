"""Lazy adoption — attribute the audited messages of a person who was unmapped
when they arrived.

A sender absent from `identity.toml` is audited with `person_id` NULL. Mapping
hot-reloads for new messages, but every earlier row stays unattributed unless
the startup sweep resolves it, and the memory graph reads `messages.person_id`
live, so an unattributed row is a message no person's memory can claim.

This module reconciles that at startup: find audited rows with no `person_id`
whose `(platform, sender_id)` NOW resolves against the freshly-loaded
`Persons` index, and record the `person_id` on the row.

Three properties are load-bearing:

1. **Off the ingest hot path, and never blocking startup.** `nora.infra.sql.
   audit`'s module docstring: WAL permits exactly one writer, an extra
   per-message write can raise SQLITE_BUSY under a burst, and the ingest path
   drops the message on failure. So this chore is scheduled like
   `prune_loop`: `App.run` spawns it through `BackgroundTasks` after the
   health server is up, and it starts after a delay so it does not compete
   with the first burst of ingest writes. `SqlRecordStore.set_person_id`
   writes one row per statement for the same reason.

2. **Best-effort.** A failing sweep is logged and counted and cannot take
   startup or the process down (the discipline `nora.app.runtime.prune_loop`
   already follows). Whatever was already marked stays adopted; the rest is
   retried on the next start.

3. **Idempotent, with `person_id` itself as the marker.** A marked row no
   longer matches "person_id IS NULL", so a second run finds nothing and marks
   nothing. There is no separate ledger to keep in sync.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from nora.domain.ports import RecordStore
from nora.obs.metrics import Metrics
from nora.persons import Persons

log = logging.getLogger("nora.app.adoption")

# The audit table is already bounded by retention (`store.max_per_conversation`
# per conversation), so this cap is belt-and-braces rather than the real bound:
# it stops one pathological start from holding an unbounded row list in memory.
# A backlog past it is not lost — the next restart picks up where this one
# stopped, because marking is what removes a row from the scan.
DEFAULT_SCAN_LIMIT = 1000

# Let the listener settle before the backfill contends for the store (a habit
# from SQLite's single writer; still free under Postgres). Nothing depends on
# this having finished, so a late start costs nothing.
DEFAULT_START_DELAY_SECONDS = 5.0


@dataclass(frozen=True)
class AdoptionCounts:
    """What one sweep did."""

    scanned: int = 0
    resolved: int = 0
    marked: int = 0


async def backfill(
    *,
    audit: RecordStore,
    persons: Persons,
    metrics: Metrics,
    access: Any = None,
    limit: int = DEFAULT_SCAN_LIMIT,
    start_delay_seconds: float = DEFAULT_START_DELAY_SECONDS,
) -> AdoptionCounts:
    """Adopt every unattributed audit row whose sender now maps to a person."""
    if start_delay_seconds > 0:
        await asyncio.sleep(start_delay_seconds)

    scanned = resolved = marked = 0
    try:
        rows = await audit.unattributed(limit=limit)
        scanned = len(rows)
        for row in rows:
            person = (
                await access.resolve_ref(row.platform, row.sender_id)
                if access is not None
                else persons.resolve(row.platform, row.sender_id)
            )
            if person is None:
                continue        # still unmapped — a later adoption's problem
            resolved += 1
            await audit.set_person_id(row_id=row.id, person_id=person.person_id)
            marked += 1
            metrics.inc("nora_lazy_adoptions_total", platform=row.platform)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Same discipline as `prune_loop`: a failing chore is logged and counted,
        # never fatal to the process it was spawned from.
        log.exception("lazy-adoption backfill failed after %d adopted row(s)", marked)
        metrics.inc("nora_errors_total", stage="adoption")

    counts = AdoptionCounts(scanned=scanned, resolved=resolved, marked=marked)
    # Unconditional, including the all-zero case. "The backfill found nothing"
    # and "the backfill never ran" must not look the same from the log (R-5).
    log.info(
        "lazy adoption: scanned %d unattributed row(s), %d now resolve to a person, "
        "marked %d",
        counts.scanned, counts.resolved, counts.marked,
    )
    return counts
