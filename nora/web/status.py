"""GET /app/v1/status — Nora's public face.

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
from typing import Any

from nora.domain.ports import MemoryStore, RecordStore
from nora.domain.reminders import ReminderStore

# All-pending, not due(): the operator's-eye-view nora/app/cli.py's
# `nora reminders list` already uses (nora/domain/reminders.py:503's
# docstring) — every conversation's pending rows, not just overdue ones.
_REMINDERS_LIMIT = 50

# How many upcoming rows the payload names individually. The array is a
# friend-facing "what's coming up" glance, not an operator export — capped
# so a busy heartbeat can't turn this into an unbounded response.
_UPCOMING_SHOWN = 10


async def build_status_payload(
    *, store: RecordStore, honcho: MemoryStore, reminders: ReminderStore,
    started_at: float,
) -> dict[str, Any]:
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
    ordered = sorted(rows, key=lambda r: r.due_at)
    next_due_at = ordered[0].due_at if ordered else None

    return {
        "uptime_seconds": time.time() - started_at,
        "store": {"reachable": store_reachable},
        "honcho": {"reachable": honcho_reachable},
        "reminders": {
            "count": len(rows),
            "next_due_at": next_due_at.isoformat() if next_due_at else None,
            # due_at/platform/conversation_id only — NEVER `.text` (a
            # reminder's sentence is the requester's own words) and NEVER
            # `requester_person_id` (attributing "whose reminder" needs a
            # Persons collaborator this module doesn't take; a real future
            # addition, not assumed into existence here — same standard the
            # module docstring already holds the cross-room activity field to).
            "upcoming": [
                {
                    "due_at": r.due_at.isoformat(),
                    "platform": r.platform,
                    "conversation_id": r.conversation_id,
                }
                for r in ordered[:_UPCOMING_SHOWN]
            ],
        },
    }
