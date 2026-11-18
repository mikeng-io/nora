"""Identity and scope for observed rows written in a source transaction."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Set
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.dml import Insert

from nora.infra.sql.cognitive_graph_schema import REGISTRY
from nora.infra.sql.schema import MESSAGES
from nora.infra.version import DEFAULT_MANIFEST_PATH

if TYPE_CHECKING:
    from nora.domain.models import NormalizedMessage
    from nora.persons import Persons
    from nora.policy import Policy

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PER_UNIT_SOURCES = frozenset({
    "presence_fixes", "social_item", "social_item_count",
    "social_item_state", "social_poll", "source_health",
})


def epoch_ms(value: int | None) -> datetime | None:
    return None if value is None else _EPOCH + timedelta(milliseconds=value)


def item_graph_id(table: str, key: tuple[Any, ...], unit: str) -> str:
    identity = (table, key, unit) if table in _PER_UNIT_SOURCES else (table, key)
    wire = json.dumps(identity, default=str, ensure_ascii=False)
    return f"item:{hashlib.sha256(wire.encode()).hexdigest()}"


class RegistrationScope:
    """The current process's policy and identity snapshot at a source write."""

    def __init__(self, policy: Policy, persons: Persons) -> None:
        self._policy = policy
        self._persons = persons

    def room_unit(self, platform: str, room: str) -> str | None:
        conversation = self._policy.conversation(platform, room)
        return (conversation.unit or None) if conversation is not None else None

    def message_unit(self, message: NormalizedMessage) -> str | None:
        if message.admitted_unit is not None:
            return message.admitted_unit or None
        return self.room_unit(message.platform, message.conversation_id)

    def unqualified_room_unit(self, room: str) -> str | None:
        units = {c.unit for c in self._policy.rooms() if room in c.ids and c.unit}
        return units.pop() if len(units) == 1 else None

    def person_id(self, platform: str, account: str | None) -> str | None:
        person = self._persons.resolve(platform, account) if account else None
        return person.person_id if person else None

    def person_units(self, person: str) -> tuple[str, ...]:
        subject = self._persons.subject_id_for(person)
        return tuple(sorted({
            c.unit for c in self._policy.conversations
            if c.unit and any(
                self._persons.subject_id_for(member) == subject for member in c.persons or ()
            )
        }))

    def circle_units(self, circle: str) -> tuple[str, ...]:
        return tuple(sorted({
            unit for member in self._persons.life360 if member.circle_id == circle
            for unit in self.person_units(member.person_id)
        }))

    def health_units(self, source: str, scope: str) -> tuple[str, ...]:
        if source == "presence":
            return self.circle_units(scope)
        if source.startswith("social."):
            return self._member_units()
        return tuple(sorted({
            c.unit for c in self._policy.conversations if c.platform == source and c.unit
        }))

    def _member_units(self) -> tuple[str, ...]:
        return tuple(sorted({
            c.unit for c in self._policy.conversations if c.unit and c.persons
        }))


class Revisions(Protocol):
    def current(self) -> Mapping[str, str | None]: ...


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return [[_canonical(k), _canonical(v)] for k, v in sorted(
            value.items(), key=lambda item: repr(item[0]),
        )]
    if isinstance(value, Set):
        return [_canonical(v) for v in sorted(value, key=repr)]
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _canonical(getattr(value, f.name)) for f in fields(value) if f.compare}
    return value


def _revision(value: Any) -> str:
    payload = json.dumps(_canonical(value), default=str, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _running_code() -> str | None:
    if sha := os.environ.get("NORA_GIT_SHA"):
        return sha
    try:
        value = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    commit = value.get("commit") if isinstance(value, dict) else None
    return commit if isinstance(commit, str) and commit else None


class RuntimeRevisions:
    """Content identifiers of the config and admitted policy/identity snapshots."""

    def __init__(self, config: Mapping[str, Any], policy: Policy, persons: Persons) -> None:
        self._config = _revision(config)
        self._code = _running_code()
        self._policy = policy
        self._persons = persons
        self._cache: dict[str, tuple[Any, str]] = {}

    def _loaded(self, key: str, source: Any) -> str:
        snapshot = getattr(source, "current", source)
        previous = self._cache.get(key)
        if previous is None or previous[0] is not snapshot:
            previous = (snapshot, _revision(snapshot))
            self._cache[key] = previous
        return previous[1]

    def current(self) -> Mapping[str, str | None]:
        return {
            "rev_code": self._code,
            "rev_config": self._config,
            "rev_identity": self._loaded("identity", self._persons),
            "rev_policy": self._loaded("policy", self._policy),
        }


class SqlItemRegistration:
    """Prepares the registry reference and planes on the writer's connection."""

    def __init__(self, scope: RegistrationScope, revisions: Revisions) -> None:
        self.scope = scope
        self._revisions = revisions

    async def insert_once(
        self, conn: AsyncConnection, table: sa.Table, stmt: Insert, *,
        unit: str | None, occurred_at: datetime | None,
        persons: Iterable[str | None] = (), room: str | None = None,
    ) -> Any:
        async with conn.begin_nested() as attempt:
            planes = await self.planes(
                conn, table, unit=unit, occurred_at=occurred_at, persons=persons, room=room,
            )
            inserted = (await conn.execute(stmt.values(**planes).returning(table.c.id))).scalar()
            if inserted is None:
                await attempt.rollback()
            return inserted

    async def planes(
        self, conn: AsyncConnection, table: sa.Table, *,
        unit: str | None, occurred_at: datetime | None,
        persons: Iterable[str | None] = (), room: str | None = None,
        key: tuple[Any, ...] | None = None, asserted_at: datetime | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "unit": unit, "persons": sorted({person for person in persons if person}),
            "origin_room": room, "occurred_at": occurred_at, "valid_from": occurred_at,
            "asserted_at": asserted_at if asserted_at is not None else sa.func.clock_timestamp(),
            **self._revisions.current(),
        }
        if unit is None:
            return values
        if key is None:
            if table.autoincrement_column is not None:
                row_id = (await conn.execute(sa.select(sa.func.nextval(
                    sa.func.pg_get_serial_sequence(table.name, "id"),
                )))).scalar_one()
            else:
                row_id = str(uuid4())
            values["id"] = row_id
            key = (row_id,)
        graph_id = item_graph_id(table.name, key, unit)
        await conn.execute(pg_insert(REGISTRY).values(
            id=graph_id, unit=unit, kind="item",
        ).on_conflict_do_nothing(index_elements=["id", "unit"]))
        values["graph_id"] = graph_id
        return values


async def touch_planes(
    registration: SqlItemRegistration | None, conn: AsyncConnection,
    table: sa.Table, message_id: int, message: NormalizedMessage, person_id: str | None,
) -> dict[str, Any]:
    if registration is None:
        return {}
    origin_room = (await conn.execute(sa.select(
        MESSAGES.c.conversation_id,
    ).where(MESSAGES.c.id == message_id))).scalar_one()
    return await registration.planes(
        conn, table, unit=registration.scope.message_unit(message),
        room=origin_room, occurred_at=epoch_ms(message.timestamp), persons=(person_id,),
    )
