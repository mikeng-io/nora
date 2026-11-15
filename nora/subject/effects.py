"""Private evaluation views of canonical SQL history, revisions and delivery receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.history import APPEND_ONLY
from nora.infra.sql.schema import METADATA, ROW_HISTORY
from nora.subject.environment import IsolatedServices


@dataclass(frozen=True)
class EffectBoundary:
    at: datetime
    history_id: int
    positions: dict[str, int]


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
    Source("profile_line", "profile"), Source("standing_revision", "standing"),
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
    "entity": "graph", "relation": "graph", "relation_evidence": "graph",
    "note": "note", "notebook_entry": "topic", "events": "event",
    "standing": "standing", "standing_decision": "standing",
    "standing_commitment": "standing", "standing_thread": "standing",
    "profile": "profile", "reminders": "reminder", "messages": "record",
}
ROOM_SCOPED = frozenset({"events", "standing", "standing_revision", "reminders", "messages"})


def _numeric(source: Source) -> bool:
    primary = list(METADATA.tables[source.table].primary_key)
    return len(primary) == 1 and isinstance(primary[0].type, sa.Integer)


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


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


async def effect_boundary(engine: AsyncEngine) -> EffectBoundary:
    """Read before starting the owned execution; no semantic writer may be in flight."""
    positions = {
        source.table: sa.select(sa.func.coalesce(sa.func.max(
            next(iter(METADATA.tables[source.table].primary_key))), 0)).scalar_subquery()
        for source in SOURCES if _numeric(source)
    }
    history = sa.select(sa.func.coalesce(sa.func.max(ROW_HISTORY.c.id), 0)).scalar_subquery()
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(
            sa.func.clock_timestamp().label("at"), history.label("history_id"),
            *(query.label(name) for name, query in positions.items()),
        ))).mappings().one()
    return EffectBoundary(row["at"], int(row["history_id"]),
                          {name: int(row[name]) for name in positions})


def _room_clause(platform: Any, conversation: Any, rooms: tuple[tuple[str, str], ...]) -> Any:
    return sa.tuple_(platform, conversation).in_(rooms)


def _scope(table: sa.Table, unit: str, rooms: tuple[tuple[str, str], ...],
           profile_keys: tuple[str, ...]) -> Any:
    if table.name == "profile_line":
        return sa.and_(table.c.unit == unit, table.c.profile_key.in_(profile_keys))
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
    if table == "profile":
        return image["key"].astext.in_(profile_keys)
    if table == "relation_evidence":
        relation = METADATA.tables["relation"]
        entity = METADATA.tables["entity"]
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


async def read_effects(engine: AsyncEngine, *, since: EffectBoundary, unit: str,
                       events: list[dict[str, Any]],
                       rooms: tuple[tuple[str, str], ...],
                       profile_keys: tuple[str, ...]) -> dict[str, Any]:
    """Read after owned work settles, retaining intermediate and terminal versions."""
    effects = []
    async with engine.connect() as raw:
        conn = await raw.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            await conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            until = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
            for table_name, kind in HISTORY_KINDS.items():
                rows = (await conn.execute(sa.select(ROW_HISTORY).where(
                    ROW_HISTORY.c.id > since.history_id,
                    ROW_HISTORY.c.table_name == table_name,
                    _history_scope(table_name, unit, rooms, profile_keys),
                ).order_by(ROW_HISTORY.c.id))).mappings().all()
                for row in rows:
                    effects.append(_effect(kind, "row_history", {"id": row["id"]},
                        row["recorded_at"], dict(row), operation=row["op"]))
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
                query = query.where(lower, _scope(owner, unit, rooms, profile_keys))
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
    effects.sort(key=lambda effect: (effect["at"], effect["ref"]))
    return {"status": "observed", "unit": unit, "unit_rooms": list(rooms),
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
