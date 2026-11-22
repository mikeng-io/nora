"""Read in-unit witnesses and close intentions through the graph transaction."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus
from nora.domain.lineage import ROW, Lineage, split_room
from nora.infra.sql.cognitive_graph_schema import (
    GRAPH_STIMULUS,
    INTENTION,
    NORA_ACT,
    NORA_ACT_OUTCOME,
)
from nora.infra.sql.cognitive_graph_store import RowData, SqlCognitiveGraph
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.history import HISTORY_TABLES
from nora.infra.sql.item_retention import expired_between
from nora.infra.sql.schema import (
    MESSAGES,
    METADATA,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_FIXES,
    ROLLUP_BUILD,
    ROW_HISTORY,
    SOCIAL_ITEM,
    SOURCE_HEALTH,
)
from nora.memory.app.condition_matcher import ConditionMatcher, Evaluation, Witness
from nora.memory.domain.conditions import (
    ActEvent,
    ConditionValue,
    Ending,
    OwnDelivery,
    PersonEvent,
    Predicate,
)
from nora.memory.domain.registration import AGGREGATE_FAMILIES

_Source = tuple[str, str]
_FORMS = ("act_event", "person_event", "predicate")
_ACT, _OUTCOME = NORA_ACT.c, NORA_ACT_OUTCOME.c
_INTENT = INTENTION.c
_CURRENT_BUILD = sa.select(ROLLUP_BUILD.c.id).where(
    ROLLUP_BUILD.c.status == "current",
).scalar_subquery()


class SqlConditionMatcher:
    def __init__(self, engine: AsyncEngine, graph: SqlCognitiveGraph) -> None:
        self._engine = engine
        self._graph = graph
        self._matcher = ConditionMatcher()

    async def run_once(self) -> None:
        async with self._engine.begin() as conn:
            await self._matcher.observe(batch=SqlIntentionBatch(conn, self._graph))


class SqlIntentionBatch:
    def __init__(self, conn: AsyncConnection, graph: SqlCognitiveGraph) -> None:
        self._conn = conn
        self._graph = graph
        self._health: dict[tuple[str, _Source], bool] = {}

    async def one(self, unit: str, intention: str) -> Mapping[str, object] | None:
        row = (await self._conn.execute(sa.select(INTENTION).where(
            _INTENT.unit == unit, _INTENT.id == intention,
        ).with_for_update())).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def open(self) -> list[Mapping[str, object]]:
        table = INTENTION
        stmt = sa.select(table).where(
            _INTENT.state == "open",
            sa.or_(sa.and_(
                _INTENT.delivery_mode == "choose_moment",
                _INTENT.trigger["kind"].astext == "condition",
            ), *(
                _INTENT[f"{role}_form"].in_(_FORMS)
                for role in ("done_when", "holds_while", "drop_when")
            )),
        ).order_by(_INTENT.unit, _INTENT.id).with_for_update(skip_locked=True)
        return [dict(row) for row in (await self._conn.execute(stmt)).mappings()]

    async def evaluate(
        self, intention: Mapping[str, Any], condition: ConditionValue, *, trigger: bool = False,
    ) -> Evaluation:
        if isinstance(condition, (ActEvent, OwnDelivery)):
            return await self._act(intention, condition, trigger=trigger)
        if isinstance(condition, PersonEvent):
            return await self._person(intention, condition, trigger=trigger)
        return await self._predicate(intention, condition)

    async def _healthy(self, unit: str, source: _Source) -> bool:
        key = unit, source
        if key not in self._health:
            h = SOURCE_HEALTH.c
            stmt = sa.select(h.outcome).where(
                h.unit == unit, h.state == "live", h.source == source[0], h.scope == source[1],
                h.at_ms <= sa.extract("epoch", sa.func.clock_timestamp()) * 1000,
            ).order_by(h.at_ms.desc(), h.id.desc()).limit(1)
            self._health[key] = await self._conn.scalar(stmt) in {"up", "ok"}
        return self._health[key]

    async def _act(
        self, intention: Mapping[str, Any], condition: ActEvent | OwnDelivery, *, trigger: bool,
    ) -> Evaluation:
        unit = intention["unit"]
        if isinstance(condition, OwnDelivery):
            match = sa.and_(
                _ACT.act_kind == "delivery", _ACT.authority_intention == intention["id"],
            )
        else:
            match = sa.and_(
                _OUTCOME.act_id == condition.act_id,
                sa.or_(_ACT.authority_intention.is_(None),
                       _ACT.authority_intention == intention["id"]),
            )
        stmt = (
            sa.select(NORA_ACT_OUTCOME, _ACT.executor)
            .join(NORA_ACT, sa.and_(_ACT.id == _OUTCOME.act_id, _ACT.unit == _OUTCOME.unit))
            .where(
                _OUTCOME.unit == unit, match,
                _OUTCOME.graph_id.is_not(None), _OUTCOME.state == "live",
                _ACT.state == "live",
            )
            .order_by(_OUTCOME.id.desc()).limit(1)
        )
        row = (await self._conn.execute(stmt)).mappings().one_or_none()
        if row is None or row["verdict"] == "uncertain":
            return Evaluation("unknown")
        done = row["verdict"] == "done"
        # The recorded success is the witness for the intention's own delivery.
        recorded_delivery = done and isinstance(condition, OwnDelivery) and not trigger
        if not recorded_delivery and not await self._healthy(
            unit, (row["executor"], ""),
        ):
            return Evaluation("unknown")
        return Evaluation("true" if done else "false", _witness(row, row["asserted_at"]))

    async def _person(
        self, intention: Mapping[str, Any], condition: PersonEvent, *, trigger: bool,
    ) -> Evaluation:
        unit = intention["unit"]
        if not await self._healthy(unit, (condition.platform, "")):
            return Evaluation("unknown")
        m = MESSAGES.c
        stmt = sa.select(MESSAGES).where(
            m.unit == unit, m.person_id == condition.person_id, m.platform == condition.platform,
            m.conversation_id == condition.room, m.is_nora == 0, m.state == "live",
            m.graph_id.is_not(None), m.asserted_at >= intention["asserted_at"],
            m.body == condition.text,
        )
        if trigger:
            stimulus = GRAPH_STIMULUS.c
            stmt = stmt.where(~sa.exists().where(
                stimulus.unit == unit, stimulus.graph_id == intention["id"],
                stimulus.kind == "intention_due", stimulus.occurrence == m.graph_id,
            ))
        stmt = stmt.order_by(m.asserted_at, m.id).limit(1).with_for_update(read=True)
        row = (await self._conn.execute(stmt)).mappings().one_or_none()
        if row is None:
            expired = await self._conn.scalar(sa.select(expired_between(
                unit, intention["asserted_at"], sa.func.clock_timestamp(),
                sources=("messages",), persons=(condition.person_id,),
            )))
            return Evaluation("unknown" if expired else "false")
        return Evaluation("true", _witness(row, row["asserted_at"]))

    async def _predicate(self, intention: Mapping[str, Any], condition: Predicate) -> Evaluation:
        unit = intention["unit"]
        for name in AGGREGATE_FAMILIES:
            table = METADATA.tables[name]
            c = table.c
            conditions = [c.unit == unit, c.graph_id == condition.graph_id, c.state == "live"]
            if "build_id" in c:
                conditions.append(c.build_id == _CURRENT_BUILD)
            row = (await self._conn.execute(
                sa.select(table).where(*conditions).with_for_update(read=True),
            )).mappings().one_or_none()
            if row is None:
                continue
            sources = await self._aggregate_sources(table, row)
            if not sources or not all([await self._healthy(unit, source) for source in sources]):
                return Evaluation("unknown")
            if await self._has_expired_inputs(table, row):
                return Evaluation("unknown")
            # The edge pins the history version by graph id and recorded time.
            stamp = await self._revision_time(table, row)
            if stamp is None:
                return Evaluation("unknown")
            return Evaluation(condition.measure(dict(row)), _witness(row, stamp))
        return Evaluation("unknown")

    async def _has_expired_inputs(self, table: sa.Table, row: RowData) -> bool:
        at = row["occurred_at"] or row["asserted_at"]
        start = row["valid_from"] or at
        end = row["valid_to"] or at
        if table.name == "platform_object_state":
            sources = ("platform_observation",)
        elif table.name.startswith("presence_"):
            sources = ("presence_fixes",)
        else:
            item_sources = {
                "message": ("messages", "message_edit", "message_reaction"),
                "presence": ("presence_fixes",),
                "social": ("social_item",),
            }
            lineage = Lineage.from_json(row["build_lineage"])
            sources = tuple({source for entry in lineage.section(ROW)
                             for source in item_sources.get(entry.kind, ())})
        return bool(await self._conn.scalar(sa.select(expired_between(
            row["unit"], min(start, end), max(start, end),
            sources=sources, persons=row["persons"] or None,
        ))))

    async def _revision_time(self, table: sa.Table, row: RowData) -> datetime | None:
        key = {column: row[column] for column in HISTORY_TABLES[table.name]}
        h = ROW_HISTORY.c
        return await self._conn.scalar(sa.select(h.recorded_at).where(
            h.table_name == table.name, h.row_key == key, h.op != "delete",
            h.superseded_at.is_(None),
        ).order_by(h.id.desc()).limit(1))

    async def _aggregate_sources(self, table: sa.Table, row: RowData) -> set[_Source]:
        if table.name == "platform_object_state":
            return {(row["platform"], "")}
        if table.name.startswith("presence_"):
            circle = row.get("circle_id")
            if circle is None and "place_id" in row:
                p = PRESENCE_DERIVED_PLACES.c
                circle = await self._conn.scalar(sa.select(p.circle_id).where(
                    p.unit == row["unit"], p.id == row["place_id"],
                ))
            if circle:
                return {("presence", circle)}
            subject = row.get("subject_id")
            return await self._presence_sources(row["unit"], subject)
        sources: set[_Source] = set()
        entries = Lineage.from_json(row.get("build_lineage")).section(ROW)
        for entry in entries:
            if entry.kind == "message" and entry.origin:
                platform, _ = split_room(entry.origin)
                if not platform:
                    return set()
                sources.add((platform, ""))
            elif entry.kind == "presence" and entry.subject:
                found = await self._presence_sources(row["unit"], entry.subject)
                if not found:
                    return set()
                sources.update(found)
            elif entry.kind == "social" and entry.subject:
                s = SOCIAL_ITEM.c
                networks = set(await self._conn.scalars(sa.select(s.network).where(
                    s.unit == row["unit"], s.person == entry.subject, s.state == "live",
                ).distinct()))
                if not networks:
                    return set()
                sources.update((f"social.{network}", "") for network in networks)
            else:
                return set()
        return sources

    async def _presence_sources(self, unit: str, subject: str | None) -> set[_Source]:
        if subject is None:
            return set()
        p = PRESENCE_FIXES.c
        circles = await self._conn.scalars(sa.select(p.circle_id).where(
            p.unit == unit, p.person_id == subject, p.state == "live",
        ).distinct())
        return {("presence", circle) for circle in circles}

    async def end(
        self, intention: Mapping[str, Any], ending: Ending, witness: Witness,
    ) -> None:
        await self._graph.end_intention_in(
            self._conn, intention, ending,
            witness_id=witness.graph_id, witness_at=witness.recorded_at,
            witness_scope=witness.scope,
        )

    async def publish_due(self, intention: Mapping[str, Any], witness: Witness) -> None:
        await SqlGraphStimuli.append_in(self._conn, GraphStimulus(
            intention["unit"], "intention_due", intention["id"], witness.recorded_at,
            "condition_matcher", intention["origin_room"], intention["owed_to"],
            occurrence=witness.graph_id,
        ))


def _witness(row: RowData, recorded_at: datetime) -> Witness:
    return Witness(row["graph_id"], recorded_at, dict(row))
