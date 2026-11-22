"""Resolve an observed row under the same scope rules as its writer."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.cognitive_graph_store import RowData
from nora.infra.sql.item_registration import RegistrationScope, epoch_ms, item_graph_id
from nora.infra.sql.schema import MESSAGES, SOCIAL_ITEM
from nora.infra.sql.social_privacy import public_accounts
from nora.memory.domain.registration import BACKFILL_ITEM_SOURCES

_SOCIAL_ITEMS = frozenset({"social_item", "social_item_count", "social_item_state"})


def _clock(value: datetime | str | int) -> datetime:
    if isinstance(value, int):
        instant = epoch_ms(value)
        if instant is None:
            raise ValueError("missing epoch timestamp")
        return instant
    return datetime.fromisoformat(value) if isinstance(value, str) else value


class ItemPlanes:
    def __init__(self, scope: RegistrationScope) -> None:
        self.scope = scope
        self._person_units: dict[str, tuple[str, ...]] = {}
        self._probes: dict[tuple[str, str], tuple[str, ...]] = {}
        self._circles: dict[tuple[str, str], tuple[str, ...]] = {}
        self._rooms: dict[tuple[str, str], str | None] = {}
        # (network, person) -> whether their latest recorded probe said public.
        self._public: dict[tuple[str, str], bool] = {}

    async def _person(self, person: str) -> tuple[str, ...]:
        if person not in self._person_units:
            self._person_units[person] = await self.scope.person_units(person)
        return self._person_units[person]

    def _room(self, platform: str, room: str) -> tuple[str, ...]:
        key = (platform, room)
        if key not in self._rooms:
            self._rooms[key] = self.scope.room_unit(platform, room)
        unit = self._rooms[key]
        return (unit,) if unit else ()

    async def dependencies(
        self, conn: AsyncConnection, family: str, batch: Sequence[RowData],
    ) -> dict[Any, Any]:
        if family in {"message_edit", "message_reaction", "message_delete"}:
            key = MESSAGES.c.id
            rows = await conn.execute(
                sa.select(
                    key, MESSAGES.c.platform, MESSAGES.c.conversation_id, MESSAGES.c.person_id,
                ).where(key.in_({row["message_id"] for row in batch})),
            )
            return {r.id: r for r in rows}
        if family in {"social_item_count", "social_item_state"}:
            item_key = sa.tuple_(SOCIAL_ITEM.c.network, SOCIAL_ITEM.c.item_id)
            rows = await conn.execute(
                sa.select(SOCIAL_ITEM.c.network, SOCIAL_ITEM.c.item_id, SOCIAL_ITEM.c.person)
                .where(item_key.in_({(r["network"], r["item_id"]) for r in batch})),
            )
            parents = {(r.network, r.item_id): r.person for r in rows}
            await self._privacy(conn, {(n, person) for (n, _), person in parents.items()})
            return parents
        if family == "social_item":
            await self._privacy(conn, {(r["network"], r["person"]) for r in batch})
        return {}

    async def _privacy(self, conn: AsyncConnection, pairs: set[tuple[str, str]]) -> None:
        missing = pairs - self._public.keys()
        public = await public_accounts(conn, missing)
        self._public.update({pair: pair in public for pair in missing})

    async def scope_for(
        self, name: str, row: RowData, parents: Mapping[Any, Any],
    ) -> tuple[tuple[str, ...], str | None, list[str]]:
        origin = None
        persons = []
        if name == "messages":
            origin = row["conversation_id"]
            units = self._room(row["platform"], origin)
            persons = [row["person_id"]]
        elif name in {"message_edit", "message_reaction", "message_delete"}:
            parent = parents.get(row["message_id"])
            if parent is None:
                return (), None, []
            origin = parent.conversation_id
            units = self._room(parent.platform, origin)
            persons = [parent.person_id if name == "message_edit" else row["person_id"]]
        elif name == "platform_observation":
            platform, origin = row["platform"], row["conversation_id"]
            units = self._room(platform, origin)
            persons = [
                await self.scope.person_id(platform, row[name])
                for name in ("actor_id", "target_author_id")
            ]
        elif name == "fetched_pages":
            origin = row["conversation_id"]
            unit = self.scope.unqualified_room_unit(origin)
            units = (unit,) if unit else ()
        elif name == "source_health":
            key = row["source"], row["scope"]
            if key not in self._probes:
                self._probes[key] = await self.scope.health_units(*key)
            units = self._probes[key]
        elif name in {"computer_events", "tool_call_log", "nora_act", "nora_act_outcome",
                      "nora_pass_output"}:
            # Executor items are scoped when written, using their original authority.
            return (), None, []
        elif name == "presence_fixes":
            persons = [row["person_id"]]
            key = row["person_id"], row["circle_id"]
            if key not in self._circles:
                self._circles[key] = await self.scope.presence_units(*key)
            units = self._circles[key]
        else:
            if name in {"social_item_count", "social_item_state"}:
                person = parents.get((row["network"], row["item_id"]))
            else:
                person = row["person"]
            persons = [person] if person else []
            # As its writer: a social item reaches units only from an account
            # whose latest recorded probe said public.
            placed = name not in _SOCIAL_ITEMS or self._public.get((row["network"], person), False)
            units = await self._person(person) if person and placed else ()
        return units, origin, sorted({person for person in persons if person})

    @staticmethod
    def values(
        name: str, row: RowData, *, unit: str, key: tuple[Any, ...],
        origin: str | None, persons: list[str],
    ) -> dict[str, Any]:
        if name == "platform_observation":
            occurred = row["occurred_ms"]
            if occurred is None:
                occurred = row["received_ms"]
        else:
            column = {
                "source_health": "at_ms", "social_item": "taken_at",
                "social_item_count": "recorded_at", "social_item_state": "recorded_at",
                "social_poll": "recorded_at", "fetched_pages": "fetched_at",
            }.get(name, "ts")
            occurred = row[column]
        asserted = {
            "messages": "created_at", "message_reaction": "created_at",
            "message_delete": "created_at", "fetched_pages": "fetched_at",
        }.get(name, "recorded_at")
        at = _clock(occurred)
        source = BACKFILL_ITEM_SOURCES[name]
        return {
            "unit": unit, "graph_id": item_graph_id(name, key, unit),
            "persons": persons, "origin_room": origin, "exposure": source.exposure,
            "writer": source.writer, "occurred_at": at, "valid_from": at,
            "asserted_at": _clock(row[asserted]),
        }
