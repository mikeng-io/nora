"""Read in-unit witnesses and close intentions through the graph transaction."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus
from nora.domain.lineage import ROW, Lineage
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
    COMPUTER_TASKS,
    MESSAGES,
    ROW_HISTORY,
    SOURCE_HEALTH,
)
from nora.memory.adapters.condition_sources import ConditionSources, Source
from nora.memory.app.condition_matcher import ConditionMatcher, Evaluation, Witness
from nora.memory.domain.conditions import (
    ActEvent,
    ConditionValue,
    Ending,
    OwnDelivery,
    PersonEvent,
    Predicate,
)

_FORMS = ("act_event", "person_event", "predicate")
_ACT, _OUTCOME = NORA_ACT.c, NORA_ACT_OUTCOME.c
_INTENT = INTENTION.c


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
        self._sources = ConditionSources(conn)
        self._health: dict[tuple[str, Source], bool] = {}

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

    async def _healthy(self, unit: str, source: Source) -> bool:
        key = unit, source
        if key not in self._health:
            h = SOURCE_HEALTH.c
            stmt = sa.select(h.outcome).where(
                h.unit == unit, h.state == "live", h.source == source[0], h.scope == source[1],
                h.at_ms <= sa.extract("epoch", sa.func.clock_timestamp()) * 1000,
            ).order_by(h.at_ms.desc(), h.id.desc()).limit(1)
            self._health[key] = await self._conn.scalar(stmt) in {"up", "ok"}
        return self._health[key]

    async def _sources_healthy(self, unit: str, sources: set[Source]) -> bool:
        return bool(sources) and all([await self._healthy(unit, source) for source in sources])

    async def _act(
        self, intention: Mapping[str, Any], condition: ActEvent | OwnDelivery, *, trigger: bool,
    ) -> Evaluation:
        unit = intention["unit"]
        stmt = (
            sa.select(
                NORA_ACT_OUTCOME, _ACT.executor, _ACT.destination, _ACT.idempotency_key,
                _ACT.graph_id.label("act_graph_id"),
            )
            .join(NORA_ACT, sa.and_(_ACT.id == _OUTCOME.act_id, _ACT.unit == _OUTCOME.unit))
            .where(
                _OUTCOME.unit == unit, self._sources.act_match(intention, condition),
                _OUTCOME.graph_id.is_not(None), _OUTCOME.state == "live",
                _ACT.state == "live",
            )
            .order_by(_OUTCOME.id.desc()).limit(1)
        )
        row = (await self._conn.execute(stmt)).mappings().one_or_none()
        if row is None:
            return Evaluation("unknown")
        sources = self._sources.act_source(row)
        if trigger and isinstance(condition, ActEvent):
            if (row["executor"] == "computer.dispatch" and row["verdict"] == "uncertain"
                    and await self._computer_outcome_pending(row["idempotency_key"])):
                return Evaluation("unknown")
            # Any recorded outcome meets the trigger; only an act with a platform source waits
            # for that source to be healthy. The act, not its latest outcome, is the witness, so
            # an uncertain outcome later settled to done fires the intention once.
            if sources and not await self._sources_healthy(unit, sources):
                return Evaluation("unknown")
            return Evaluation("true", _witness({**row, "graph_id": row["act_graph_id"]},
                                               row["asserted_at"]))
        if row["verdict"] == "uncertain":
            return Evaluation("unknown")
        done = row["verdict"] == "done"
        # The recorded success is the witness for the intention's own delivery.
        recorded_delivery = done and isinstance(condition, OwnDelivery) and not trigger
        if not recorded_delivery and not await self._sources_healthy(unit, sources):
            return Evaluation("unknown")
        return Evaluation("true" if done else "false", _witness(row, row["asserted_at"]))

    async def _computer_outcome_pending(self, key: str) -> bool:
        try:
            task_id = UUID(key)
        except (TypeError, ValueError):
            return True
        task = COMPUTER_TASKS.c
        status = await self._conn.scalar(sa.select(task.status).where(task.id == task_id))
        # A dispatch acknowledgement is uncertain. Only a lost task or an
        # uncertain terminal state can report that verdict as the outcome.
        return status not in (None, "unavailable", "interrupted")

    async def _person(
        self, intention: Mapping[str, Any], condition: PersonEvent, *, trigger: bool,
    ) -> Evaluation:
        unit = intention["unit"]
        if not await self._sources_healthy(unit, await self._sources.resolve(intention, condition)):
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
        aggregate = await self._sources.aggregate(unit, condition.graph_id)
        if aggregate is None:
            return Evaluation("unknown")
        table, row = aggregate
        sources = await self._sources.aggregate_sources(table, row)
        if not await self._sources_healthy(unit, sources):
            return Evaluation("unknown")
        if await self._has_expired_inputs(table, row):
            return Evaluation("unknown")
        # The edge pins the history version by graph id and recorded time.
        stamp = await self._revision_time(table, row)
        if stamp is None:
            return Evaluation("unknown")
        return Evaluation(condition.measure(dict(row)), _witness(row, stamp))

    async def _has_expired_inputs(self, table: sa.Table, row: RowData) -> bool:
        at = row["occurred_at"] or row["asserted_at"]
        start = row["valid_from"] or at
        end = row["valid_to"] or at
        if table.name == "platform_object_state":
            sources: tuple[str, ...] = ("platform_observation",)
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
