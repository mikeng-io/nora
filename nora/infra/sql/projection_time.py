"""Observation frontiers for the sources that feed the rollup."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.schema import (
    MESSAGE_DELETE,
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    PLATFORM_OBSERVATION,
    PRESENCE_FIXES,
    SOURCE_HEALTH,
)


@dataclass(frozen=True)
class ProjectionInputs:
    unit: str
    rooms: tuple[tuple[str, str], ...]
    presence: tuple[tuple[str, tuple[str, ...]], ...]


def _recorded_ms(at: Any) -> Any:
    return sa.cast(sa.func.ceil(sa.extract("epoch", at) * 1000), sa.BigInteger)


def _healthy(unit: str, source: str, scope: str, outcome: str) -> Any:
    h = SOURCE_HEALTH.c
    return sa.select(sa.func.max(sa.func.greatest(h.at_ms, _recorded_ms(h.recorded_at)))).where(
        h.unit == unit, h.state == "live", h.graph_id.is_not(None),
        h.source == source, h.scope == scope, h.outcome == outcome,
    ).scalar_subquery()


def _presence_frontier(unit: str, circle: str, people: tuple[str, ...]) -> sa.Select[Any]:
    fix = PRESENCE_FIXES.c
    fixes = sa.select(sa.func.max(fix.recorded_at)).where(
        sa.or_(fix.unit == unit, fix.unit.is_(None)),
        fix.circle_id == circle, fix.person_id.in_(people),
    ).scalar_subquery()
    frontier = sa.func.greatest(fixes, _healthy(unit, "presence", circle, "ok"))
    return sa.select(frontier.label("frontier"))


def _platform_frontier(unit: str, platform: str, rooms: tuple[str, ...]) -> sa.Select[Any]:
    m, obs = MESSAGES.c, PLATFORM_OBSERVATION.c
    scoped = (m.platform == platform, m.conversation_id.in_(rooms),
              sa.or_(m.unit == unit, m.unit.is_(None)), m.state == "live")
    message = sa.select(sa.func.max(_recorded_ms(sa.cast(m.created_at, sa.DateTime(timezone=True))))
                        ).where(*scoped, m.is_nora == 0).scalar_subquery()
    observed = sa.select(sa.func.max(_recorded_ms(obs.recorded_at))).where(
        obs.platform == platform, obs.conversation_id.in_(rooms), obs.state == "live",
        sa.or_(obs.unit == unit, obs.unit.is_(None)),
    ).scalar_subquery()
    revisions = [
        sa.select(sa.func.max(_recorded_ms(
            table.c.recorded_at if table is MESSAGE_EDIT
            else sa.cast(table.c.created_at, sa.DateTime(timezone=True)),
        ))).select_from(table.join(MESSAGES, table.c.message_id == m.id)).where(
            *scoped, table.c.state == "live", sa.or_(table.c.unit == unit, table.c.unit.is_(None)),
        ).scalar_subquery()
        for table in (MESSAGE_EDIT, MESSAGE_REACTION, MESSAGE_DELETE)
    ]
    frontier = sa.func.greatest(message, observed, *revisions, _healthy(unit, platform, "", "up"))
    return sa.select(frontier.label("frontier"))


async def projection_time(conn: AsyncConnection, inputs: Sequence[ProjectionInputs]) -> int | None:
    frontiers: list[sa.Select[Any]] = []
    for scope in inputs:
        frontiers.extend(_presence_frontier(scope.unit, circle, people)
                         for circle, people in scope.presence)
        for platform in sorted({p for p, _ in scope.rooms}):
            rooms = tuple(room for p, room in scope.rooms if p == platform)
            frontiers.append(_platform_frontier(scope.unit, platform, rooms))
    if not frontiers:
        return None
    sources = sa.union_all(*frontiers).subquery()
    return cast(int | None, await conn.scalar(sa.select(sa.func.min(sources.c.frontier))))
