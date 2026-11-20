"""Resolve typed conditions to the health sources that can witness them."""


import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.lineage import ROW, Lineage, split_room
from nora.infra.sql.cognitive_graph_schema import NORA_ACT
from nora.infra.sql.cognitive_graph_store import RowData
from nora.infra.sql.schema import (
    METADATA,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_FIXES,
    ROLLUP_BUILD,
    SOCIAL_ITEM,
)
from nora.memory.domain.act_executors import executor_health_platform
from nora.memory.domain.conditions import ActEvent, ConditionValue, OwnDelivery, PersonEvent
from nora.memory.domain.registration import AGGREGATE_FAMILIES

Source = tuple[str, str]
_ACT = NORA_ACT.c
_CURRENT_BUILD = sa.select(ROLLUP_BUILD.c.id).where(
    ROLLUP_BUILD.c.status == "current",
).scalar_subquery()


class ConditionSources:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def resolve(self, intention: RowData, condition: ConditionValue) -> set[Source]:
        if isinstance(condition, PersonEvent):
            return {(condition.platform, "")}
        if isinstance(condition, (ActEvent, OwnDelivery)):
            stmt = sa.select(_ACT.executor, _ACT.destination).where(
                _ACT.unit == intention["unit"], _ACT.state == "live",
                self.act_match(intention, condition),
            ).order_by(_ACT.asserted_at.desc(), _ACT.id.desc()).limit(1)
            act = (await self._conn.execute(stmt)).mappings().one_or_none()
            if act is not None:
                return self.act_source(act)
            platform, _ = split_room(intention["origin_room"] or "")
            return {(platform, "")} if platform else set()
        aggregate = await self.aggregate(intention["unit"], condition.graph_id)
        return await self.aggregate_sources(*aggregate) if aggregate is not None else set()

    @staticmethod
    def act_match(intention: RowData, condition: ActEvent | OwnDelivery) -> sa.ColumnElement[bool]:
        if isinstance(condition, OwnDelivery):
            return sa.and_(
                _ACT.act_kind == "delivery", _ACT.authority_intention == intention["id"],
            )
        return sa.and_(
            _ACT.id == condition.act_id,
            sa.or_(_ACT.authority_intention.is_(None), _ACT.authority_intention == intention["id"]),
        )

    @staticmethod
    def act_source(act: RowData) -> set[Source]:
        platform = executor_health_platform(act["executor"], act["destination"])
        return {(platform, "")} if platform else set()

    async def aggregate(self, unit: str, graph_id: str) -> tuple[sa.Table, RowData] | None:
        for name in AGGREGATE_FAMILIES:
            table = METADATA.tables[name]
            c = table.c
            stmt = sa.select(table).where(c.unit == unit, c.graph_id == graph_id, c.state == "live")
            if "build_id" in c:
                stmt = stmt.where(c.build_id == _CURRENT_BUILD)
            result = await self._conn.execute(stmt.with_for_update(read=True))
            row = result.mappings().one_or_none()
            if row is not None:
                return table, dict(row)
        return None

    async def aggregate_sources(self, table: sa.Table, row: RowData) -> set[Source]:
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
        sources: set[Source] = set()
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

    async def _presence_sources(self, unit: str, subject: str | None) -> set[Source]:
        if subject is None:
            return set()
        p = PRESENCE_FIXES.c
        circles = await self._conn.scalars(sa.select(p.circle_id).where(
            p.unit == unit, p.person_id == subject, p.state == "live",
        ).distinct())
        return {("presence", circle) for circle in circles}
