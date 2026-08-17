"""GET /app/v1/status — Nora's public face (design spec §5.1).

A thin, filtered re-projection of what nora/obs/health.py::build_health_app
already computes, plus a reminders summary. Deliberately NOT the full
/health body: this is a friend-facing page, not an operator dashboard, and
carries none of /health's SSE/platform/doctrine-sha internals.

No cross-room "recent activity" field: RecordStore.recent() is scoped to one
conversation_id (nora/domain/ports.py:656), and there is no port method
spanning every room Nora is in — adding one is a real port change (both
SqlRecordStore and FakeRecordStore, plus a contract test), not assumed into
existence here.
"""

from __future__ import annotations

import time

from nora.domain.ports import MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore

# All-pending, not due(): the operator's-eye-view nora/app/cli.py's
# `nora reminders list` already uses (nora/domain/reminders.py:503's
# docstring) — every conversation's pending rows, not just overdue ones.
_REMINDERS_LIMIT = 50


async def build_status_payload(
    *, store: RecordStore, honcho: MemoryStore, reminders: ReminderStore,
    started_at: float,
) -> dict:
    try:
        await store.stats()
        store_reachable = True
    except Exception:                          # noqa: BLE001 — never raise from a status read
        store_reachable = False

    try:
        honcho_reachable = (await honcho.health()).reachable
    except Exception:                          # noqa: BLE001
        honcho_reachable = False

    rows = await reminders.all_pending(limit=_REMINDERS_LIMIT)
    # Timestamps only, never `.text` — a reminder's sentence is the
    # requester's own words and stays off this friend-facing surface.
    next_due_at = min((r.due_at for r in rows), default=None)

    return {
        "uptime_seconds": time.time() - started_at,
        "store": {"reachable": store_reachable},
        "honcho": {"reachable": honcho_reachable},
        "reminders": {
            "count": len(rows),
            "next_due_at": next_due_at.isoformat() if next_due_at else None,
        },
    }
