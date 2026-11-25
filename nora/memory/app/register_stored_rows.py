"""Register the retained Record and Rollup after the graph-planes migration."""

from dataclasses import dataclass
from typing import Protocol

from nora.memory.domain.registration import BACKFILL_AGGREGATE_FAMILIES, BACKFILL_ITEM_SOURCES


@dataclass(frozen=True, slots=True)
class TableRegistration:
    registered: int
    unscoped: int
    covered: int = 0


class StoredRows(Protocol):
    async def register_table(self, name: str, *, batch_size: int) -> TableRegistration: ...
    async def backfill_presence_fix_units(self, *, batch_size: int) -> TableRegistration: ...
    async def validate_pairs(self) -> None: ...


class RegisterStoredRows:
    def __init__(self, store: StoredRows, *, batch_size: int = 2000) -> None:
        if batch_size < 1:
            raise ValueError("batch size must be positive")
        self._store = store
        self._batch_size = batch_size

    async def run(self) -> dict[str, TableRegistration]:
        # Parents are registered before dependent evidence or buckets.
        items = (
            "messages", "message_edit", "message_reaction", "message_delete",
            "fetched_pages", "social_poll", "social_item",
            "social_item_count", "social_item_state", "platform_observation", "source_health",
        )
        aggregates = (
            "presence_stays", "presence_trips", "presence_derived_places", "presence_corridors",
            "presence_occupancy", "presence_place_rhythm", "rollup_colocation", "rollup_outing",
            "rollup_response", "rollup_tie", "rollup_daybook", "presence_places",
            "platform_object_state",
        )
        if (
            set(items) != set(BACKFILL_ITEM_SOURCES)
            or set(aggregates) != set(BACKFILL_AGGREGATE_FAMILIES)
        ):
            raise RuntimeError("stored-row registration order does not cover its declared tables")
        counts = {
            name: await self._store.register_table(name, batch_size=self._batch_size)
            for name in (*items, *aggregates)
        }
        counts["presence_fixes"] = await self._store.backfill_presence_fix_units(
            batch_size=self._batch_size,
        )
        await self._store.validate_pairs()
        return counts
