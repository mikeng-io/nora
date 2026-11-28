"""Import the legacy commitment heads from a quiescent cutover snapshot."""

from datetime import datetime
from typing import Protocol


class LegacyAgenda(Protocol):
    async def import_open(self, *, review_deadline: datetime) -> dict[str, int]: ...


class MigrateLegacyAgenda:
    def __init__(self, store: LegacyAgenda) -> None:
        self._store = store

    async def run(self, *, review_deadline: datetime) -> dict[str, int]:
        """The operator supplies the review horizon for commitments without one."""
        if review_deadline.utcoffset() is None:
            raise ValueError("The legacy agenda review deadline must include a time zone")
        return await self._store.import_open(review_deadline=review_deadline)
