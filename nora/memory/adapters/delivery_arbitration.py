"""Unit-wide arbitration over scheduled intentions and observed delivery acts."""

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql.cognitive_graph_schema import (
    EDGE,
    ENTITY,
    GRAPH_STIMULUS_CLAIM,
    INTENTION,
    NORA_ACT,
    NORA_ACT_OUTCOME,
)
from nora.memory.domain.delivery_arbitration import DeliveryEvidence
from nora.memory.domain.delivery_identity import (
    DuplicateDeliveryError,
    ResolvedDelivery,
    SubjectEdge,
    resolve_delivery,
)
from nora.memory.domain.time_trigger import decode_time_trigger


class DeliveryReadError(Exception):
    """The graph cannot resolve a unique subject head."""


class _Prior(NamedTuple):
    intention: str
    destination: str
    verdict: str | None
    asserted_at: datetime | None


async def lock_deliveries(conn: AsyncConnection, unit: str) -> None:
    await conn.execute(
        sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(
            f"graph-delivery:{unit}", sa.literal(0, type_=sa.BigInteger()),
        )))
    )


async def check_deliveries(
    conn: AsyncConnection, unit: str, admitted: Collection[str],
) -> None:
    """Reject any conflict outside the open's own operation or revision chain."""
    intention = INTENTION.c
    pending = {
        row["id"]: row for row in (await conn.execute(
            sa.select(
                intention.id, intention.owed_to, intention.asserted_at,
                intention.origin_room, intention.replaces,
            ).where(
                intention.unit == unit,
                intention.state == "open",
                intention.delivery_mode == "choose_moment",
            ),
        )).mappings()
    }
    incoming = {key: pending[key] for key in admitted}
    resolved = tuple(
        key for key, delivery in (await _identities(conn, unit, incoming)).items()
        if delivery.identity is not None
    )
    if not resolved:
        return
    acts = await _acts(conn, unit, {incoming[key]["origin_room"] for key in resolved}, firing=True)
    other = pending.keys() - incoming.keys()
    rows = await _include_authorities(conn, unit, pending, acts)
    identities = await _identities(conn, unit, rows)
    for key in resolved:
        delivery = identities[key]
        if delivery.identity is None:
            continue
        row = incoming[key]
        own = await _predecessors(conn, unit, row["replaces"])
        _check_conflicts(row, delivery, identities, other - own, acts, own)


def _check_conflicts(
    row: sa.RowMapping, delivery: ResolvedDelivery, identities: Mapping[str, ResolvedDelivery],
    pending: Collection[str], acts: Sequence[_Prior], own: set[str],
) -> None:
    for other in sorted(pending):
        if identities[other].identity == delivery.identity:
            raise DuplicateDeliveryError(other)
    for act in acts:
        if identities[act.intention].identity != delivery.identity:
            continue
        if act.verdict in (None, "uncertain") and act.intention not in own:
            raise DuplicateDeliveryError(act.intention)
        if act.destination == row["origin_room"] and _delivered(act, delivery):
            raise DuplicateDeliveryError(act.intention)


class SqlDeliveryHistory:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read(
        self, unit: str, intention: str, destination: str, *, occurrence: str | None = None,
    ) -> DeliveryEvidence | None:
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            raw = (await conn.execute(sa.select(
                INTENTION.c.owed_to, INTENTION.c.asserted_at, INTENTION.c.state,
                INTENTION.c.delivery_mode, INTENTION.c.origin_room, INTENTION.c.trigger,
            ).where(
                INTENTION.c.unit == unit, INTENTION.c.id == intention,
            ))).mappings().one_or_none()
            if raw is None:
                return None
            successor = await conn.scalar(sa.select(INTENTION.c.id).where(
                INTENTION.c.unit == unit, INTENTION.c.replaces == intention,
            ))
            origin = raw["origin_room"]
            if (raw["state"] != "open" or raw["delivery_mode"] != "choose_moment"
                    or successor is not None):
                return DeliveryEvidence(False, origin)
            schedule = decode_time_trigger(raw["trigger"])
            if occurrence is not None and schedule is not None and schedule.recurrence is not None:
                separate = _other_occurrence(unit, intention, occurrence)
            else:
                separate = sa.false()
            acts = await _acts(conn, unit, {destination}, firing=False, separate=separate)
            rows = await _include_authorities(conn, unit, {intention: raw}, acts)
            identities = await _identities(conn, unit, rows)
            delivery = identities[intention]
            for act in acts:
                same = act.intention == intention or (
                    delivery.identity is not None
                    and identities[act.intention].identity == delivery.identity
                )
                if same and act.verdict == "uncertain":
                    return DeliveryEvidence(True, origin, "uncertain_prior")
                if same and _delivered(act, delivery):
                    return DeliveryEvidence(True, origin, "already_delivered")
            return DeliveryEvidence(True, origin)


def _delivered(act: _Prior, delivery: ResolvedDelivery) -> bool:
    return (
        act.verdict == "done" and act.asserted_at is not None
        and act.asserted_at > delivery.current_since
    )


async def _predecessors(conn: AsyncConnection, unit: str, base: str | None) -> set[str]:
    if base is None:
        return set()
    row = INTENTION.c
    chain = sa.select(row.id, row.replaces).where(row.unit == unit, row.id == base).cte(
        "delivery_predecessors", recursive=True,
    )
    chain = chain.union(sa.select(row.id, row.replaces).join(
        chain, row.id == chain.c.replaces,
    ).where(row.unit == unit))
    return set((await conn.scalars(sa.select(chain.c.id))).all())


async def _acts(
    conn: AsyncConnection, unit: str, destinations: Collection[str | None], *, firing: bool,
    separate: sa.ColumnElement[bool] | None = None,
) -> list[_Prior]:
    act, outcome = NORA_ACT.c, NORA_ACT_OUTCOME.c
    latest = sa.select(outcome.verdict, outcome.asserted_at).where(
        outcome.unit == unit, outcome.act_id == act.id,
    ).order_by(outcome.id.desc()).limit(1).lateral()
    sent_here = sa.and_(act.destination.in_(destinations), latest.c.verdict == "done")
    unresolved = sa.or_(latest.c.verdict.is_(None), latest.c.verdict == "uncertain")
    relevant = (
        sa.or_(unresolved, sent_here) if firing
        else sa.and_(
            act.destination.in_(destinations), latest.c.verdict.in_(("done", "uncertain")),
        )
    )
    query = sa.select(
        act.authority_intention, act.destination, latest.c.verdict, latest.c.asserted_at,
    ).select_from(NORA_ACT.outerjoin(latest, sa.true())).where(
        act.unit == unit, act.act_kind == "delivery", act.authority_intention.is_not(None),
        relevant,
    ).order_by(act.id)
    if separate is not None:
        query = query.where(~separate)
    return [_Prior(*row) for row in (await conn.execute(query))]


def _other_occurrence(unit: str, intention: str, occurrence: str) -> sa.ColumnElement[bool]:
    claim, act = GRAPH_STIMULUS_CLAIM.c, NORA_ACT.c
    return sa.and_(act.authority_intention == intention, sa.exists().where(
        claim.unit == unit, claim.graph_id == intention, claim.pass_id == act.trigger_id,
        claim.claimant == "proactive", claim.kind == "intention_due", claim.state == "closed",
        claim.occurrence != occurrence,
    ))


async def _include_authorities(
    conn: AsyncConnection, unit: str, rows: dict[str, sa.RowMapping], acts: Sequence[_Prior],
) -> dict[str, sa.RowMapping]:
    missing = {act.intention for act in acts} - rows.keys()
    if not missing:
        return rows
    authorities = {
        row["id"]: row for row in (await conn.execute(sa.select(
            INTENTION.c.id, INTENTION.c.owed_to, INTENTION.c.asserted_at,
        ).where(
            INTENTION.c.unit == unit, INTENTION.c.id.in_(missing),
        ))).mappings()
    }
    if missing != authorities.keys():
        raise DeliveryReadError("missing delivery authority")
    return rows | authorities


async def _identities(
    conn: AsyncConnection, unit: str, rows: Mapping[str, sa.RowMapping],
) -> dict[str, ResolvedDelivery]:
    if not rows:
        return {}
    edges: dict[str, list[SubjectEdge]] = defaultdict(list)
    found = await conn.execute(_heads(unit, rows.keys()))
    for owner, relation, head, asserted_at, pointer in found:
        if head is None or asserted_at is None:
            raise DeliveryReadError("missing delivery subject head")
        edges[owner].append(SubjectEdge(relation, head, asserted_at, pointer))
    return {
        key: resolve_delivery({"owed_to": row["owed_to"], "asserted_at": row["asserted_at"]},
                              edges[key])
        for key, row in rows.items()
    }


def _heads(unit: str, intentions: Collection[str]) -> sa.Select:
    edge, entity, intention = EDGE.c, ENTITY.c, INTENTION.c
    seeds = sa.select(
        edge.id.label("edge_id"), edge.src_id, edge.type, edge.dst_id.label("target"),
    ).where(
        edge.unit == unit, edge.state == "live", edge.src_id.in_(intentions),
        edge.type.in_(("about", "serves")),
    ).cte("delivery_subjects")
    links = sa.union_all(
        sa.select(entity.replaces.label("parent"), entity.id.label("child")).where(
            entity.unit == unit, entity.replaces.is_not(None),
        ),
        sa.select(intention.replaces, intention.id).where(
            intention.unit == unit, intention.replaces.is_not(None),
        ),
        sa.select(edge.src_id, edge.dst_id).where(
            edge.unit == unit, edge.type == "merged_into", edge.state == "live",
        ),
    ).cte("delivery_successors")
    walk = sa.select(seeds.c.edge_id, seeds.c.target).cte("delivery_heads", recursive=True)
    walk = walk.union(sa.select(walk.c.edge_id, links.c.child).join(
        links, links.c.parent == walk.c.target,
    ))
    heads = sa.select(
        walk.c.edge_id, sa.func.min(walk.c.target).label("id"),
    ).select_from(walk.outerjoin(links, links.c.parent == walk.c.target)).where(
        links.c.parent.is_(None),
    ).group_by(walk.c.edge_id).having(sa.func.count() == 1).subquery()
    return sa.select(
        seeds.c.src_id, seeds.c.type, heads.c.id,
        sa.func.coalesce(entity.asserted_at, intention.asserted_at),
        entity.pointer_kind,
    ).select_from(seeds.outerjoin(heads, heads.c.edge_id == seeds.c.edge_id).outerjoin(
        ENTITY, sa.and_(entity.unit == unit, entity.id == heads.c.id, seeds.c.type == "about"),
    ).outerjoin(
        INTENTION,
        sa.and_(intention.unit == unit, intention.id == heads.c.id, seeds.c.type == "serves"),
    ))
