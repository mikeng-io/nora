"""Private evaluation views of canonical SQL history, revisions and delivery receipts."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora import memory
from nora.domain.culture import (
    character_key,
    nora_character_key,
    split_culture_key,
    unit_culture_key,
)
from nora.domain.lineage import room_key
from nora.infra.sql.history import APPEND_ONLY
from nora.infra.sql.schema import (
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    METADATA,
    ROW_HISTORY,
    STIMULUS_CLAIM,
)
from nora.subject.environment import IsolatedServices


@dataclass(frozen=True)
class EffectBoundary:
    at: datetime
    history_id: int
    positions: dict[str, int]


@dataclass(frozen=True)
class EffectScope:
    unit: str
    rooms: tuple[tuple[str, str], ...]
    profile_keys: tuple[str, ...]


class _SharedConnection:
    """Yields the frozen transaction's connection; exits close nothing."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        return None


class FrozenReads:
    """The engine-shaped surface `frozen_reads` hands to store readers.

    Every `connect()`/`begin()` yields the same REPEATABLE READ
    connection, so a writer that commits mid-measurement cannot land
    between two of this cut's reads — effects, state and diff always
    describe one database instant.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def connect(self) -> _SharedConnection:
        return _SharedConnection(self._conn)

    def begin(self) -> _SharedConnection:
        return _SharedConnection(self._conn)


@asynccontextmanager
async def frozen_reads(engine: AsyncEngine) -> AsyncIterator[FrozenReads]:
    """One stable measurement cut for every reader inside the block.

    The transaction is REPEATABLE READ: a commit that lands between two
    reads inside is absent from both. It is not READ ONLY — the effects
    read claims its offered stimuli as it goes — but nothing in a cut
    mutates semantic rows. Writers keep their own connections and
    cadence: the cut is the reader's own snapshot, never a lock on them.
    """
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            yield FrozenReads(conn)


async def _measured(engine: AsyncEngine | FrozenReads,
                    read: Callable[[Any], Any]) -> Any:
    """`read(conn)` on the shared cut when one is active, else its own."""
    if isinstance(engine, FrozenReads):
        async with engine.connect() as conn:
            return await read(conn)
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            return await read(conn)


def effect_scope(subject: Any) -> EffectScope:
    """Resolve the composed unit's rooms and profile keys once before execution."""
    from nora.app.composition_readers import social_unit_rooms

    policy, unit = subject.deps.config_reload.policy, subject.unit
    persons: set[str] = set()
    for room in policy.rooms():
        if room.unit == unit:
            persons |= (set(subject.deps.persons.by_person)
                        if room.persons is None else set(room.persons))
    return EffectScope(unit, tuple(social_unit_rooms(policy).get(unit, ())), (
        nora_character_key(unit), unit_culture_key(unit),
        *(character_key(unit, person) for person in sorted(persons)),
    ))


@dataclass(frozen=True)
class Source:
    table: str
    kind: str
    parent: str | None = None
    foreign_key: str | None = None


SOURCES = (
    Source("note_revision", "note", "note", "note_id"),
    Source("notebook_entry_revision", "topic", "notebook_entry", "entry_id"),
    Source("memo", "memo"), Source("memo_revision", "memo", "memo", "memo_id"),
    Source("lesson", "lesson"), Source("lesson_revision", "lesson", "lesson", "lesson_id"),
    Source("standing_revision", "standing"),
    Source("standing_decision_revision", "standing"),
    Source("standing_commitment_revision", "standing"),
    Source("standing_thread_revision", "standing"),
    Source("event_revision", "event", "events", "event_id"),
    Source("event_rsvps", "event", "events", "event_id"),
    Source("cognition_session", "wake"),
    Source("cognition_session_close", "wake", "cognition_session", "session_id"),
    Source("cognition_wake", "wake"), Source("stimulus_claim", "wake"),
    Source("cognition_self_wake", "wake"), Source("cognition_carry", "wake"),
    Source("cognition_wake_outcome", "wake"), Source("stimulus", "wake"),
    Source("bridge_action", "delivery"),
)
HISTORY_KINDS = {
    "legacy_entity": "graph", "legacy_relation": "graph", "legacy_relation_evidence": "graph",
    "note": "note", "notebook_entry": "topic", "events": "event",
    "standing": "standing", "standing_decision": "standing",
    "standing_commitment": "standing", "standing_thread": "standing",
    "claim": "profile", "reminders": "reminder", "messages": "record",
}
ROOM_SCOPED = frozenset({"events", "standing", "standing_revision", "reminders", "messages"})


def _numeric(source: Source) -> bool:
    primary = list(METADATA.tables[source.table].primary_key)
    return len(primary) == 1 and isinstance(primary[0].type, sa.Integer)


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def observed_effects(value: Any) -> bool:
    """A complete captured projection, distinct from absent or partial evidence."""
    return (isinstance(value, dict) and value.get("status") == "observed"
            and all(isinstance(value.get(key), list) for key in ("items", "tools")))


def result_values(result: Any) -> list[Any]:
    """A Strands tool result's content values, JSON-decoded — the same
    decode `evidence_exchanges` applies, so a caller never reads the
    raw envelope."""
    if not isinstance(result, dict):
        return []
    values = []
    for part in result.get("content") or []:
        if not isinstance(part, dict):
            continue
        if "json" in part:
            values.append(part["json"])
        elif "text" in part:
            try:
                values.append(json.loads(part["text"]))
            except ValueError:
                values.append(part["text"])
    return values


def tool_outcome(result: Any) -> dict[str, Any]:
    """The tool's own three-state outcome inside the Strands envelope: the
    envelope says `success` for a refused or unavailable write too."""
    for value in result_values(result):
        if isinstance(value, dict) and "status" in value:
            return value
    return {}


async def prepare_effect_reads(engine: AsyncEngine, environment: IsolatedServices) -> None:
    """Index system-time reads once in the disposable database before owned execution."""
    if not environment.guarded or engine.url != sa.engine.make_url(environment.database_url):
        raise ValueError("effect indexes require the active case-owned database")
    environment.check()
    columns = {(source.table, APPEND_ONLY[source.table]) for source in SOURCES
               if not _numeric(source) and source.table in APPEND_ONLY}
    columns |= {("bridge_action", name) for name in ("created_at", "started_at", "completed_at")}
    async with engine.begin() as conn:
        for table, column in sorted(columns):
            await conn.execute(sa.text(
                f"CREATE INDEX IF NOT EXISTS eval_effect_{table}_{column} "
                f"ON {table} ({column})"))


async def effect_boundary(engine: AsyncEngine | FrozenReads) -> EffectBoundary:
    """Read before starting the owned execution; no semantic writer may be in flight.

    Inside `frozen_reads` the boundary is taken from the same cut as the
    before-state beside it, so the opening image is one instant too.
    """
    positions = {
        source.table: sa.select(sa.func.coalesce(sa.func.max(
            next(iter(METADATA.tables[source.table].primary_key))), 0)).scalar_subquery()
        for source in SOURCES if _numeric(source)
    }
    history = sa.select(sa.func.coalesce(sa.func.max(ROW_HISTORY.c.id), 0)).scalar_subquery()

    async def read(conn: Any) -> Any:
        return (await conn.execute(sa.select(
            sa.func.clock_timestamp().label("at"), history.label("history_id"),
            *(query.label(name) for name, query in positions.items()),
        ))).mappings().one()

    row = await _measured(engine, read)
    return EffectBoundary(row["at"], int(row["history_id"]),
                          {name: int(row[name]) for name in positions})


def _room_clause(platform: Any, conversation: Any, rooms: tuple[tuple[str, str], ...]) -> Any:
    return sa.tuple_(platform, conversation).in_(rooms)


def _scope(table: sa.Table, unit: str, rooms: tuple[tuple[str, str], ...]) -> Any:
    if table.name == "bridge_action":
        return _room_clause(table.c.platform, table.c.request["target"]["id"].astext, rooms)
    conditions = []
    if "unit" in table.c:
        conditions.append(table.c.unit == unit)
    elif "unit_id" in table.c:
        conditions.append(table.c.unit_id == unit)
    if table.name in ROOM_SCOPED:
        conditions.append(_room_clause(table.c.platform, table.c.conversation_id, rooms))
    if not conditions:
        raise ValueError(f"effect source has no declared scope: {table.name}")
    return sa.and_(*conditions)


def _history_scope(table: str, unit: str, rooms: tuple[tuple[str, str], ...],
                   profile_keys: tuple[str, ...]) -> Any:
    image = ROW_HISTORY.c.image
    if table == "claim":
        keys = tuple(split_culture_key(key) for key in profile_keys)
        people = [person for _, owner, person in keys if owner == unit and person is not None]
        return sa.and_(
            image["unit"].astext == unit,
            image["origin_room"].astext.in_(tuple(room_key(p, r) for p, r in rooms)),
            image["part"].astext.in_(memory.wire.PROFILE_PARTS),
            image["exposure"].astext == "external",
            image["excluded_identities"] == sa.cast(sa.literal("[]"), image.type),
            image["identities"].contained_by(people),
        )
    if table == "legacy_relation_evidence":
        relation = METADATA.tables["legacy_relation"]
        entity = METADATA.tables["legacy_entity"]
        return sa.or_(
            sa.exists().where(relation.c.unit == unit,
                              relation.c.id == image["relation_id"].astext),
            sa.exists().where(entity.c.unit == unit, entity.c.id == image["entity_id"].astext),
        )
    owner = METADATA.tables[table]
    conditions = []
    if "unit" in owner.c:
        conditions.append(image["unit"].astext == unit)
    elif "unit_id" in owner.c:
        conditions.append(image["unit_id"].astext == unit)
    if table in ROOM_SCOPED:
        conditions.append(_room_clause(image["platform"].astext,
                                       image["conversation_id"].astext, rooms))
    if not conditions:
        raise ValueError(f"history effect has no declared scope: {table}")
    return sa.and_(*conditions)


def _effect(kind: str, source: str, key: dict[str, Any], at: datetime,
            row: dict[str, Any], *, operation: str) -> dict[str, Any]:
    identity = json.dumps(_plain(key), sort_keys=True, separators=(",", ":"))
    return {"ref": f"{kind}:{source}:{identity}", "kind": kind, "source": source,
            "key": _plain(key), "at": at.isoformat(), "operation": operation,
            "row": _plain(row)}


async def read_effects(engine: AsyncEngine | FrozenReads, *, since: EffectBoundary,
                       unit: str, events: list[dict[str, Any]],
                       rooms: tuple[tuple[str, str], ...],
                       profile_keys: tuple[str, ...]) -> dict[str, Any]:
    """Read after owned work settles, retaining intermediate and terminal versions.

    Inside `frozen_reads` this is one of several reads on the same cut —
    a commit that lands while the measurement runs is absent from the
    effects, the state and the diff alike.
    """
    effects = []

    async def read(conn: Any) -> Any:
        until = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
        for table_name, kind in HISTORY_KINDS.items():
            rows = (await conn.execute(sa.select(ROW_HISTORY).where(
                ROW_HISTORY.c.id > since.history_id,
                ROW_HISTORY.c.table_name == table_name,
                _history_scope(table_name, unit, rooms, profile_keys),
            ).order_by(ROW_HISTORY.c.id))).mappings().all()
            for row in rows:
                payload = dict(row)
                if table_name == "claim":
                    payload = {key: row["image"].get(key) for key in memory.wire.PROFILE_METADATA}
                effects.append(_effect(kind, "row_history", {"id": row["id"]},
                    row["recorded_at"], payload, operation=row["op"]))
        for source in SOURCES:
            table = METADATA.tables[source.table]
            owner = METADATA.tables[source.parent] if source.parent else table
            columns = list(table.c)
            query = sa.select(*columns)
            if source.parent:
                query = query.select_from(table.join(owner,
                    table.c[cast(str, source.foreign_key)] == owner.c.id))
            stamp = table.c[APPEND_ONLY.get(source.table, "created_at")]
            lower = (next(iter(table.primary_key)) > since.positions[source.table]
                     if _numeric(source) else stamp >= since.at)
            if source.table == "bridge_action":
                lower = sa.or_(lower, table.c.started_at >= since.at,
                               table.c.completed_at >= since.at)
            query = query.where(lower, _scope(owner, unit, rooms))
            rows = (await conn.execute(query)).mappings().all()
            for row in rows:
                key = {column.name: row[column.name] for column in table.primary_key}
                at = row[stamp.name]
                operation = "append"
                if source.table == "bridge_action":
                    at = row["completed_at"] or row["started_at"] or at
                    operation = "receipt"
                effects.append(_effect(source.kind, source.table, key, at,
                                       dict(row), operation=operation))
        return until

    until = await _measured(engine, read)
    effects.sort(key=lambda effect: (effect["at"], effect["ref"]))
    return {"status": "observed", "unit": unit, "unit_rooms": _plain(rooms),
            "profile_keys": list(profile_keys), "since": since.at.isoformat(),
            "through": until.isoformat(), "history_after": since.history_id,
            "positions": dict(since.positions), "items": effects,
            "tools": tool_effects(events)}


def tool_effects(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain tool domain outcomes, including refused writes and transport success."""
    return [{"ref": "tool:" + str(event["event_id"]),
             "invocation_id": event.get("invocation_id"), "attempt_id": event.get("attempt_id"),
             "tool": event.get("tool"),
             "transport_status": (event["result"].get("status")
                                  if isinstance(event.get("result"), dict) else None),
             "outcome": tool_outcome(event.get("result")),
             "values": result_values(event.get("result")),
             "error_type": event.get("error_type"), "retry": event.get("retry"),
             "argument_validation": event.get("argument_validation")}
            for event in events if event.get("kind") == "tool_finished"]


async def stimulus_session(reads: Any, stimulus_id: int) -> dict[str, Any]:
    """The claim and settled outcome for one stimulus row, on the cut."""
    async def read(conn: Any) -> dict[str, Any]:
        claims = [int(wid) for wid in (await conn.execute(
            sa.select(STIMULUS_CLAIM.c.wake_id).where(
                STIMULUS_CLAIM.c.stimulus_id == stimulus_id)
            .order_by(STIMULUS_CLAIM.c.id))).scalars().all()]
        outcomes: dict[int, dict[str, Any]] = {}
        if claims:
            for row in (await conn.execute(sa.select(COGNITION_WAKE_OUTCOME)
                    .where(COGNITION_WAKE_OUTCOME.c.wake_id.in_(claims)))
                    ).mappings().all():
                outcomes[int(row["wake_id"])] = dict(row)
        wake_id = claims[-1] if claims else None
        outcome = outcomes.get(wake_id) if wake_id is not None else None
        closed = [wid for wid in claims
                  if (outcomes.get(wid) or {}).get("outcome") == "closed"]
        # Two closed consumptions, or a closed claim a newer claim
        # followed, contradict which session the stimulus settled to —
        # conflicting evidence, never "the latest wins".
        conflict = (
            f"stimulus claims settle to conflicting sessions: {claims}"
            if len(closed) > 1 or (closed and closed[0] != wake_id)
            else None)
        return {
            "wake_id": wake_id,
            "claims": claims,
            "wake_outcome": (outcome or {}).get("outcome"),
            "session_id": ((outcome or {}).get("session_id")
                           if conflict is None
                           and (outcome or {}).get("outcome") == "closed"
                           else None),
            "session_conflict": conflict,
        }

    if hasattr(reads, "connect"):
        async with reads.connect() as conn:
            return await read(conn)
    async with reads.begin() as conn:
        return await read(conn)


async def cognition_wakes(engine: Any) -> list[dict[str, Any]]:
    """Every production wake row — the whole population, never a slice."""
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(COGNITION_WAKE))).mappings().all()
    return [dict(row) for row in rows]


async def cognition_wake_outcomes(engine: Any) -> list[dict[str, Any]]:
    """Every production wake outcome row — the settlement population."""
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(COGNITION_WAKE_OUTCOME))).mappings().all()
    return [dict(row) for row in rows]


async def wake_census(engine: Any) -> dict[str, Any]:
    """Every wake/outcome row, read after the owned lifecycle closed."""
    return {"wakes": await cognition_wakes(engine),
            "outcomes": await cognition_wake_outcomes(engine)}


async def offered_stimuli(engine: Any, unit: str,
                          at_ms: int) -> tuple[list[int], list[int]]:
    """The stimulus ids the wake evaluator offers this unit's session."""
    from nora.infra.sql.stimuli import SqlStimulusStore

    view = await SqlStimulusStore(engine).view(
        unit=unit, mode="live", now_ms=at_ms, limit=500)
    offered = sorted(s.id for s in view.pending)
    return offered, sorted(s.id for s in view.pending if s.kind == "self")


#: The tables whose whole population a journey retains as its receipts.
_JOURNEY_RECEIPTS = {
    "wakes": "cognition_wake", "outcomes": "cognition_wake_outcome",
    "sessions": "cognition_session", "notes": "note",
    "note_revisions": "note_revision", "actions": "bridge_action",
    "messages": "messages", "reminders": "reminders",
}


async def journey_receipts(engine: Any) -> dict[str, Any]:
    """The SQL receipts the journey's claims rest on, one table each."""
    out: dict[str, Any] = {}
    async with engine.connect() as conn:
        for name, table_name in _JOURNEY_RECEIPTS.items():
            table = METADATA.tables[table_name]
            rows = (await conn.execute(sa.select(table))).mappings().all()
            out[name] = [
                json.loads(json.dumps(dict(row), default=str)) for row in rows
            ]
    return out


async def sent_message_bodies(engine: Any) -> list[str]:
    """The delivery receipts she actually wrote: sent `is_nora` audit rows."""
    messages = METADATA.tables["messages"]
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(messages.c.body).where(
                messages.c.is_nora == 1,
                messages.c.delivery_status == "sent"))).scalars().all()
    return [str(row) for row in rows]
