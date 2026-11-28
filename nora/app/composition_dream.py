"""Build the room-scoped tool source the Dreamer speaks through."""

from __future__ import annotations

import logging
from typing import Any

from nora.agent.dreamer import (
    RegistryToolSource,
)
from nora.app.composition_readers import scope_home_rooms
from nora.domain.live import LiveMapping

log = logging.getLogger("nora.app.composition")


def _dream_tool_source(
    allowlist: Any, audit: Any, cfg: Any,
    metrics: Any, persons: Any, registry: Any,
    rosters: Any, tool_registry: Any, access: Any,
) -> Any:
    """The registry-backed tool source every Dreamer session speaks through."""
    return RegistryToolSource(
        registry=tool_registry,
        store=audit, access=access,
        persons=persons,
        metrics=metrics,
        ai_peer=cfg.agent.name,
        scope_rooms=LiveMapping(lambda: scope_home_rooms(allowlist)),
        rosters=rosters,
        # EVERY platform's aliases, for `_presence_preflight`'s reason: a home room's roster
        # must skip Nora's own account rather than count it as one unnamed stranger — which
        # alone makes `where_is` refuse in a location-trusted room.
        self_ids=registry.self_aliases(),
    )
