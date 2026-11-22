"""Identity and scope for observed rows written in a source transaction."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.dml import Insert

from nora.domain.cues import graph_item_cues
from nora.domain.graph_stimuli import GraphStimulus
from nora.domain.managed import room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.cognitive_graph_schema import REGISTRY
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.schema import MESSAGES
from nora.infra.version import DEFAULT_MANIFEST_PATH

if TYPE_CHECKING:
    from nora.access import IdentityAccess
    from nora.domain.models import NormalizedMessage, PersonRef

log = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_OWN_ITEMS = frozenset({
    "nora_act", "nora_act_outcome", "nora_pass_output", "computer_events", "tool_call_log",
})
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


class _Conversation(Protocol):
    @property
    def unit(self) -> str: ...

    @property
    def platform(self) -> str: ...

    @property
    def ids(self) -> Set[str]: ...

    @property
    def persons(self) -> Set[str] | None: ...


class RegistrationPolicy(Protocol):
    @property
    def conversations(self) -> Sequence[_Conversation]: ...

    def conversation(self, platform: str, conversation_id: str) -> _Conversation | None: ...

    def rooms(self) -> Sequence[_Conversation]: ...


class _Membership(Protocol):
    @property
    def circle_id(self) -> str | None: ...

    @property
    def person_id(self) -> str: ...


class RegistrationPersons(Protocol):
    @property
    def life360(self) -> Sequence[_Membership]: ...

    def resolve(self, platform: str, sender_id: str) -> PersonRef | None: ...

    def subject_id_for(self, person_id: str) -> str: ...


class RegistrationScope:
    """The current process's policy and identity snapshot at a source write."""

    def __init__(
        self, policy: RegistrationPolicy, persons: RegistrationPersons | None, *,
        access: IdentityAccess | None = None,
    ) -> None:
        self._policy = policy
        self._persons = persons
        self._managed = access if access is not None and not access.is_file_backend else None

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

    async def person_id(self, platform: str, account: str | None) -> str | None:
        if not account:
            return None
        if self._managed is not None:
            try:
                bound = await self._managed.resolve_account(platform, account)
                return await self._managed.canonical_person(bound.person_id) if bound else None
            except Exception:  # noqa: BLE001 — retain the source with unresolved identity
                log.warning("graph registration identity unavailable")
                return None
        person = self._persons.resolve(platform, account) if self._persons is not None else None
        return person.person_id if person else None

    async def person_units(self, person: str) -> tuple[str, ...]:
        if self._managed is not None:
            try:
                return await self._managed.member_units(person)
            except Exception:  # noqa: BLE001 — retain the source without unit placement
                log.warning("graph registration membership unavailable")
                return ()
        return self._file_person_units(person)

    def _file_person_units(self, person: str) -> tuple[str, ...]:
        persons = self._persons
        if persons is None:
            return ()
        subject = persons.subject_id_for(person)
        return tuple(sorted({
            c.unit for c in self._policy.conversations
            if c.unit and any(
                persons.subject_id_for(member) == subject for member in c.persons or ()
            )
        }))

    async def _subject(self, person: str) -> str:
        if self._managed is not None:
            return await self._managed.canonical_person(person) or person
        return self._persons.subject_id_for(person) if self._persons is not None else person

    async def _life360(self) -> Sequence[_Membership]:
        if self._managed is not None:
            return await self._managed.life360_memberships()
        return self._persons.life360 if self._persons is not None else ()

    async def _unit_members(self, unit: str) -> frozenset[str] | None:
        """A unit's members as subject ids; None when the unit admits anyone."""
        if self._managed is not None:
            return await self._managed.unit_members(unit)
        rooms = [c for c in self._policy.conversations if c.unit == unit]
        if self._persons is None or any(c.persons is None for c in rooms):
            return None
        return frozenset({await self._subject(m) for c in rooms for m in c.persons or ()})

    async def circle_placements(
        self, circle: str, *, observed_persons: Iterable[str] | None = None,
    ) -> dict[str | None, tuple[str, ...]] | None:
        """Map each unit whose members all share the circle to those members, or None if
        unavailable; a unit the circle only partly covers receives nothing."""
        try:
            if observed_persons is None:
                observed_persons = (
                    m.person_id for m in await self._life360() if m.circle_id == circle
                )
            people = tuple(sorted(set(observed_persons)))
            sharing = {await self._subject(person): person for person in people}
            candidates: set[str] = set()
            for person in people:
                candidates.update(await self._managed.member_units(person) if self._managed
                                  else self._file_person_units(person))
            placements: dict[str | None, tuple[str, ...]] = {}
            for unit in sorted(candidates):
                members = await self._unit_members(unit)
                if members and members <= sharing.keys():
                    placements[unit] = tuple(sorted(sharing[m] for m in members))
            return placements or {None: people}
        except Exception:  # noqa: BLE001 — unknown membership must stay unscoped
            log.warning("graph registration circle membership unavailable")
            return None

    async def presence_units(self, person: str, circle: str | None = None) -> tuple[str, ...]:
        """Units a person's location from `circle`, or from any of their circles, is placed in."""
        try:
            subject = await self._subject(person)
            circles = {circle} if circle is not None else {
                m.circle_id for m in await self._life360()
                if m.circle_id and await self._subject(m.person_id) == subject
            }
        except Exception:  # noqa: BLE001 — unknown membership must stay unscoped
            log.warning("graph registration circle membership unavailable")
            return ()
        units = set()
        for each in sorted(c for c in circles if c is not None):
            for unit, members in (await self.circle_placements(each) or {}).items():
                if unit is not None and subject in {await self._subject(m) for m in members}:
                    units.add(unit)
        return tuple(sorted(units))

    async def health_units(self, source: str, scope: str) -> tuple[str, ...]:
        if source == "presence":
            placements = await self.circle_placements(scope)
            return tuple(unit for unit in placements or {} if unit is not None)
        if source.startswith("social."):
            access = self._managed
            if access is None:
                return tuple(sorted({
                    c.unit for c in self._policy.conversations if c.unit and c.persons
                }))
            try:
                units = set()
                for c in self._policy.conversations:
                    if not c.unit or c.unit in units:
                        continue
                    if await access.members_of(Scope(ScopeKind.UNIT, c.unit)) or any([
                        await access.members_of(
                            Scope(ScopeKind.ROOM, room_scope_id(c.platform, room))
                        )
                        for room in c.ids
                    ]):
                        units.add(c.unit)
                return tuple(sorted(units))
            except Exception:  # noqa: BLE001 — retain health evidence without guessed placement
                log.warning("graph registration health membership unavailable")
                return ()
        return tuple(sorted({
            c.unit for c in self._policy.conversations if c.platform == source and c.unit
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

    def __init__(
        self, config: Mapping[str, Any], policy: RegistrationPolicy,
        persons: RegistrationPersons | None,
    ) -> None:
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
            "rev_identity": self._loaded("identity", self._persons) if self._persons else None,
            "rev_policy": self._loaded("policy", self._policy),
        }


class SqlItemRegistration:
    """Prepares the registry reference and planes on the writer's connection."""

    def __init__(self, scope: RegistrationScope, revisions: Revisions) -> None:
        self.scope = scope
        self._revisions = revisions

    def revisions(self) -> Mapping[str, str | None]:
        return self._revisions.current()

    async def insert_once(
        self, conn: AsyncConnection, table: sa.Table, stmt: Insert, *,
        unit: str | None, occurred_at: datetime | None,
        persons: Iterable[str | None] = (), room: str | None = None,
        cues: tuple[str, ...] = (),
    ) -> Any:
        async with conn.begin_nested() as attempt:
            planes = await self.planes(
                conn, table, unit=unit, occurred_at=occurred_at,
                persons=persons, room=room, cues=cues,
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
        cues: tuple[str, ...] = (), own: bool = False, failed: bool = False,
    ) -> dict[str, Any]:
        people = tuple(person for person in persons if person)
        values: dict[str, Any] = {
            "unit": unit, "persons": sorted(set(people)),
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
        await _register_item(conn, graph_id, unit)
        values["graph_id"] = graph_id
        if failed or (not own and table.name not in _OWN_ITEMS):
            at = occurred_at or asserted_at
            if at is None:
                at = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
                values["asserted_at"] = at
            await SqlGraphStimuli.append_in(conn, GraphStimulus(
                unit, "failed_act" if failed else "strong_cue" if cues else "new_item",
                graph_id, at, table.name, room, next(iter(people), None), cues,
            ))
        return values


async def _register_item(conn: AsyncConnection, graph_id: str, unit: str) -> None:
    c = REGISTRY.c
    stmt = pg_insert(REGISTRY).values(id=graph_id, unit=unit, kind="item")
    # Both unique constraints can arbitrate concurrent first observations.
    if await conn.scalar(stmt.on_conflict_do_nothing().returning(c.id)) is not None:
        return
    target = c.id == graph_id, c.unit == unit, c.kind == "item"
    state = await conn.scalar(sa.select(c.state).where(*target).with_for_update(key_share=True))
    if state == "tombstoned":
        await conn.execute(sa.update(REGISTRY).where(*target).values(
            state="live", content_hash=None, tombstoned_at=None, occurred_at=None,
        ))


async def touch_planes(
    registration: SqlItemRegistration | None, conn: AsyncConnection,
    table: sa.Table, message_id: int, message: NormalizedMessage, person_id: str | None,
    *, own: bool = False,
) -> dict[str, Any]:
    if registration is None:
        return {}
    origin_room = (await conn.execute(sa.select(
        MESSAGES.c.conversation_id,
    ).where(MESSAGES.c.id == message_id))).scalar_one()
    return await registration.planes(
        conn, table, unit=registration.scope.message_unit(message),
        room=origin_room, occurred_at=epoch_ms(message.timestamp), persons=(person_id,),
        cues=graph_item_cues(None, addressed=message.conversation_type == "dm"), own=own,
    )


async def message_graph_cues(
    conn: AsyncConnection, message: NormalizedMessage, self_ids: frozenset[str],
) -> tuple[str, ...]:
    addressed = message.conversation_type == "dm" or bool(message.mentions & self_ids)
    if not addressed and message.quote is not None:
        addressed = bool(await conn.scalar(sa.select(sa.exists().where(
            MESSAGES.c.platform == message.platform,
            MESSAGES.c.conversation_id == message.conversation_id,
            MESSAGES.c.ts == message.quote.quoted_ts,
            MESSAGES.c.is_nora == 1,
        ))))
    return graph_item_cues(message.body, addressed=addressed)
