"""Lazy adoption — backfill the Honcho memory of a person who was unmapped
when their messages arrived.

A sender absent from `identity.toml` is audited with `person_id` NULL and
NOTHING is fed to Honcho (`nora.ingest.stages.memory_feed` bare-returns on
`person is None`). Mapping is startup-only, so when the operator adds that
account and restarts, every earlier message stays unfed unless it is
backfilled; the peer card would silently miss the person's history up to
their adoption.

This module reconciles that at startup: find audited rows with no `person_id`
whose `(platform, sender_id)` NOW resolves against the freshly-loaded
`Persons` index, replay each into that person's peer, and record the
`person_id` on the row.

Four properties are load-bearing:

1. **FEED, THEN MARK. Never the reverse.** Setting `person_id` first and
   feeding second means a Honcho failure — or a crash — between the two leaves
   the row marked done and never retried: the hole becomes permanent and
   silent, which is exactly the failure this module exists to remove. Feeding
   first makes a crash between the two re-feed on the next restart. That is
   at-least-once rather than at-most-once, and duplicating one message into a
   peer's memory is the tolerable direction; losing it forever is not.

2. **Off the ingest hot path, and never blocking startup.** `nora.infra.sql.
   audit`'s module docstring: WAL permits exactly one writer, an extra
   per-message write can raise SQLITE_BUSY under a burst, and the ingest path
   drops the message on failure. So this chore is scheduled like
   `prune_loop`: `App.run` spawns it through `BackgroundTasks` after the
   health server is up, and it starts after a delay so it does not compete
   with the first burst of ingest writes. The awaited Honcho feed between rows
   is itself the throttle that keeps the single writer free;
   `SqlRecordStore.set_person_id` writes one row per statement for the same
   reason.

3. **Best-effort, like everything Honcho-facing.** `HonchoClient` is documented
   as never load-bearing and swallows its own failures into `False`; a
   `MemoryStore` that raises instead is converted to the same `False` here. A
   failing row is counted and skipped, a failing sweep is logged and counted,
   and neither can take startup or the process down (the discipline
   `nora.app.runtime.prune_loop` already follows).

4. **Idempotent, with `person_id` itself as the marker.** A marked row no
   longer matches "person_id IS NULL", so a second run finds nothing and feeds
   nothing. There is no separate ledger to keep in sync.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from nora.domain.external_files import render_file_links
from nora.domain.media_join import MediaJoin
from nora.domain.ports import MemoryStore, RecordStore
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
    """What one sweep did. `marked` can exceed `fed`: an attachment-only row
    has no text to replay, only a `person_id` to record."""

    scanned: int = 0
    resolved: int = 0
    fed: int = 0
    marked: int = 0
    failed: int = 0


async def backfill(
    *,
    audit: RecordStore,
    memory: MemoryStore,
    persons: Persons,
    metrics: Metrics,
    media_join: MediaJoin | None = None,
    limit: int = DEFAULT_SCAN_LIMIT,
    start_delay_seconds: float = DEFAULT_START_DELAY_SECONDS,
) -> AdoptionCounts:
    """Adopt every unattributed audit row whose sender now maps to a person."""
    if start_delay_seconds > 0:
        await asyncio.sleep(start_delay_seconds)

    scanned = resolved = fed = marked = failed = 0
    try:
        rows = await audit.unattributed(limit=limit)
        scanned = len(rows)
        if media_join is not None:
            # The shared media join. These rows are FED TO HONCHO below, so a
            # photo's description and a voice note's words must ride in the
            # body; without the join a back-filled row reaches memory as an
            # empty `[image a3f2c1]` marker.
            rows = list(await media_join.enrich(rows, path="adoption"))
        for row in rows:
            person = persons.resolve(row.platform, row.sender_id)
            if person is None:
                continue        # still unmapped — a later adoption's problem
            resolved += 1

            # FEED FIRST. `set_person_id` below is the marker, and marking a row
            # whose feed did not land would retire it from every future scan —
            # see this module's docstring, point 1.
            # The file-link handle, never the URL.
            text = render_file_links(row.body or "").strip()
            if text:
                if not await _feed(memory=memory, peer_id=person.honcho_peer_id,
                                   text=text, conversation_id=row.conversation_id):
                    failed += 1
                    metrics.inc("nora_adoption_failures_total", platform=row.platform)
                    continue
                fed += 1

            await audit.set_person_id(row_id=row.id, person_id=person.person_id)
            marked += 1
            metrics.inc("nora_lazy_adoptions_total", platform=row.platform)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Same discipline as `prune_loop`: a failing chore is logged and counted,
        # never fatal to the process it was spawned from. Whatever was already
        # fed and marked stays adopted; the rest is retried on the next start.
        log.exception("lazy-adoption backfill failed after %d adopted row(s)", marked)
        metrics.inc("nora_errors_total", stage="adoption")

    counts = AdoptionCounts(
        scanned=scanned, resolved=resolved, fed=fed, marked=marked, failed=failed,
    )
    # Unconditional, including the all-zero case. "The backfill found nothing"
    # and "the backfill never ran" must not look the same from the log (R-5).
    log.info(
        "lazy adoption: scanned %d unattributed row(s), %d now resolve to a person, "
        "fed %d to Honcho, marked %d, left %d unmarked to retry next start",
        counts.scanned, counts.resolved, counts.fed, counts.marked, counts.failed,
    )
    return counts


async def _feed(
    *, memory: MemoryStore, peer_id: str, text: str, conversation_id: str,
) -> bool:
    """One replayed message. `False` on any failure, never an exception.

    `HonchoClient` already returns `False` rather than raising, but the port
    does not promise that and a real outage can surface as either — a
    `MemoryStore` that raises must not abort the rows queued behind this one.
    """
    try:
        await memory.ensure_peer(peer_id=peer_id, conversation_id=conversation_id)
        return await memory.feed(
            peer_id=peer_id, text=text, conversation_id=conversation_id,
        )
    except Exception as exc:  # noqa: BLE001 — Honcho is never load-bearing
        log.warning("honcho feed failed during lazy adoption of %s: %s", peer_id, exc)
        return False
