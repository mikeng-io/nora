"""Filtered uptime and store reachability for the portal."""

import time
from typing import Any

from nora.domain.ports import RecordStore


async def build_status_payload(*, store: RecordStore, started_at: float) -> dict[str, Any]:
    try:
        await store.stats()
        store_reachable = True
    except Exception:  # noqa: BLE001 — reachability is the result
        store_reachable = False

    return {
        "uptime_seconds": time.time() - started_at,
        "store": {"reachable": store_reachable},
    }
