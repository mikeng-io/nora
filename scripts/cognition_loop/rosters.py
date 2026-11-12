"""Room rosters frozen at run start, for a sandbox with no platform link."""

from __future__ import annotations

from typing import Any

from nora.domain.ports import Roster


class FrozenRoster:
    """`RosterSource` over the rosters frozen at run start: the sandbox has
    no platform connection, and a room's membership is what lets a note
    say who a line is owed by. A room the freeze could not read answers
    unavailable, as the live reader would."""

    def __init__(self, platform: str, rosters: dict[str, Any]) -> None:
        self.platform = platform
        self._rosters = rosters

    async def roster(self, *, conversation_id: str) -> Roster:
        members = self._rosters.get(conversation_id)
        if members is None:
            return Roster(available=False)
        return Roster(available=True,
                      members=tuple(frozenset(ids) for ids in members))


def frozen_rosters(frozen: dict[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in (frozen.get("rosters") or {}).items()}
