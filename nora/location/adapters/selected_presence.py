"""Fold current selected identity families while preserving original fix provenance."""

from typing import Any

from nora.infra.sql.presence import PresenceStore


class SelectedPresenceStore(PresenceStore):
    def __init__(self, engine: Any, *, access: Any, **kwargs: Any) -> None:
        super().__init__(engine, **kwargs)
        self._access = access

    async def _family(self, circle_id: str, person_id: str) -> tuple[str, ...]:
        canonical = await self._access.canonical_person(person_id)
        current = await self._access.life360_memberships()
        if not any(row.circle_id == circle_id and row.person_id == canonical for row in current):
            return ()
        return tuple(await self._access.identity_ids(person_id) or ())

    async def latest_fix(self, *, circle_id: str, person_id: str) -> Any:
        ids = await self._family(circle_id, person_id)
        rows = [await super(SelectedPresenceStore, self).latest_fix(
            circle_id=circle_id, person_id=original) for original in ids or ()]
        return max((row for row in rows if row is not None), key=lambda row: row.ts, default=None)

    async def window(self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int) -> Any:
        ids = await self._family(circle_id, person_id)
        rows = []
        for original in ids or ():
            rows.extend(await super().window(circle_id=circle_id, person_id=original,
                                            since_ts=since_ts, until_ts=until_ts))
        return sorted(rows, key=lambda row: (row.ts, row.recorded_at))
