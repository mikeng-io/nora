"""Serialize admitted proposals to the shared graph store."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_registration import Exposure, InputScope, inherited_scope
from nora.domain.graph_stimuli import GraphStimulus
from nora.domain.lineage import Lineage
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY, INTENTION, REGISTRY
from nora.infra.sql.cognitive_graph_store import (
    AdmissionTopic,
    GraphWriteError,
    RowData,
    SqlCognitiveGraph,
    dependent_claims,
)
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.memory.adapters.admission_rows import GRAPH_TABLES, SqlAdmissionRows
from nora.memory.adapters.condition_matcher import SqlIntentionBatch
from nora.memory.adapters.delivery_arbitration import (
    DeliveryReadError,
    check_deliveries,
    lock_deliveries,
)
from nora.memory.adapters.lesson_tally import rebuild_lesson_tally
from nora.memory.adapters.review_grades import check_grade
from nora.memory.app.condition_matcher import ConditionMatcher
from nora.memory.domain.admission import Admitted, Refusal, fields
from nora.memory.domain.admission_ports import (
    AdmissionUnavailableError,
    AdmissionWriteRefusedError,
    Stored,
)
from nora.memory.domain.admission_proposals import (
    AddEdge,
    CreateClaim,
    CreateEntity,
    CreateIntention,
    Link,
    PassContext,
    Retire,
    Revise,
    RowFacts,
)
from nora.memory.domain.lesson_admission import LessonInventory, lesson_inventory_refusal


class SqlAdmissionStore:
    def __init__(
        self, rows: SqlAdmissionRows, graph: SqlCognitiveGraph, engine: AsyncEngine,
    ) -> None:
        self._rows = rows
        self._graph = graph
        self._engine = engine
        self._matcher = ConditionMatcher()

    async def read(self, unit: str, ids: frozenset[str]) -> Mapping[str, RowFacts]:
        return await self._rows.read(unit, ids)

    async def lesson_inventory(self, unit: str) -> LessonInventory:
        try:
            async with self._engine.connect() as conn:
                return await _lesson_inventory(conn, unit)
        except sa.exc.SQLAlchemyError as exc:
            raise AdmissionUnavailableError(commit_uncertain=False) from exc

    async def close(
        self, intention: str, witness: str, context: PassContext,
    ) -> Stored | Refusal:
        committing = False
        try:
            async with self._engine.begin() as conn:
                batch = SqlIntentionBatch(conn, self._graph)
                row = await batch.one(context.unit, intention)
                if row is None:
                    return Refusal("stale_base", "base")
                refusal = await self._matcher.close(batch, row, witness)
                if refusal is not None:
                    return refusal
                committing = True
        except sa.exc.SQLAlchemyError as exc:
            raise AdmissionUnavailableError(commit_uncertain=committing) from exc
        return Stored(intention)

    async def persist_admitted_proposal(
        self, decision: Admitted, context: PassContext, stored: Stored,
    ) -> None:
        row, edges = _serialize(decision, context)
        topics = tuple(
            AdmissionTopic(*_serialize(topic, context), topic.base.state if topic.base else None)
            for topic in decision.topics
        )
        base, survivor = decision.base, decision.survivor
        data = fields(decision.proposal)
        derive_about = isinstance(data, (CreateClaim, CreateIntention))
        carry_about = isinstance(decision.proposal, Revise) and isinstance(
            data, (CreateClaim, CreateIntention),
        ) and not data.replace_about and not any(link.type == "about" for link in data.links)
        revisions = isinstance(decision.proposal, (Revise, Retire))
        scheduled = (
            decision.row.kind == "intention" and decision.row.state == "open"
            and decision.row.delivery_mode == "choose_moment"
        )

        is_lesson = isinstance(data, CreateClaim) and data.part == "lesson"
        grade_target: RowData | None = None

        async def before_write(conn: AsyncConnection) -> None:
            nonlocal grade_target
            if isinstance(data, CreateClaim) and data.part == "grade":
                grade_target = await check_grade(conn, unit=context.unit, proposal=data)
                row["asserted_at"] = sa.func.clock_timestamp()
                return
            await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
                sa.func.hashtextextended(f"lesson:unit:{context.unit}", 0),
            )))
            if isinstance(data, CreateClaim) and isinstance(data.content, str):
                inventory = await _lesson_inventory(conn, context.unit)
                reason = lesson_inventory_refusal(
                    data.content, inventory, (), decision.held_lesson,
                    decision.lesson_evidence_at,
                )
                if reason is not None:
                    raise AdmissionWriteRefusedError(Refusal(reason, "content"))

        async def after_write(conn: AsyncConnection) -> None:
            if decision.deliveries:
                await _additional_deliveries(conn, decision.deliveries, context)
            if carry_about and base is not None:
                await _carry_inferred_about(conn, base.id, decision.row, context)
            if derive_about:
                for part in (decision, *decision.deliveries):
                    await _computed_about(conn, part.row, context)
            if scheduled:
                await lock_deliveries(conn, context.unit)
                await check_deliveries(conn, context.unit, (stored.id, *stored.other_deliveries))
            if is_lesson:
                at = (await conn.execute(sa.select(sa.func.now()))).scalar_one()
                await rebuild_lesson_tally(
                    conn, self._graph, unit=context.unit, claim_id=stored.id, at=at,
                )
            if revisions:
                await _dependency(conn, stored, row, context)
            if (isinstance(data, CreateClaim) and data.grade == "weakened"
                    and grade_target is not None and row["exposure"] != "operator"):
                at = (await conn.execute(sa.select(sa.func.now()))).scalar_one()
                await SqlGraphStimuli.append_in(conn, GraphStimulus(
                    context.unit, "dependency", grade_target["id"], at, "review",
                    room=grade_target["origin_room"], occurrence=stored.id,
                ))

        try:
            await self._graph.write_admission(
                GRAPH_TABLES[decision.row.kind],
                row,
                edges,
                base_state=base.state if base is not None and survivor is None else None,
                retire=isinstance(decision.proposal, Retire),
                merge=(base.id, survivor.id) if base is not None and survivor is not None else None,
                topics=topics,
                before_write=before_write if is_lesson or context.occasion in (
                    "operator", "review",
                ) else None,
                after_write=after_write if derive_about or revisions or scheduled else None,
            )
        except GraphWriteError as exc:
            raise AdmissionUnavailableError(commit_uncertain=exc.commit_uncertain) from exc
        except DeliveryReadError as exc:
            raise AdmissionUnavailableError(commit_uncertain=False) from exc


async def _lesson_inventory(conn: AsyncConnection, unit: str) -> LessonInventory:
    c = CLAIM.c
    rows = (await conn.execute(sa.select(c.id, c.content, c.state, c.asserted_at).where(
        c.unit == unit, c.part == "lesson", sa.or_(
            sa.and_(c.state == "live", sa.or_(c.valid_to.is_(None), c.valid_to > sa.func.now())),
            sa.and_(c.state == "retired", c.writer == "operator", c.occasion == "operator"),
        ),
    ))).all()
    return LessonInventory(
        tuple((row.id, str(row.content)) for row in rows if row.state == "live"),
        tuple(row.asserted_at for row in rows if row.state == "retired"),
    )


async def _additional_deliveries(
    conn: AsyncConnection, decisions: tuple[Admitted, ...], context: PassContext,
) -> None:
    writes = [_serialize(decision, context) for decision in decisions]
    rows = [row for row, _ in writes]
    edges = [edge for _, links in writes for edge in links]
    await _register(conn, rows + edges)
    await conn.execute(sa.insert(INTENTION).values(rows))
    if edges:
        await conn.execute(sa.insert(EDGE), edges)


async def _register(conn: AsyncConnection, rows: list[dict[str, Any]]) -> None:
    await conn.execute(sa.insert(REGISTRY), [
        {name: row[name] for name in ("id", "unit", "kind")} for row in rows
    ])


async def _carry_inferred_about(
    conn: AsyncConnection, base: str, head: RowFacts, context: PassContext,
) -> None:
    e = EDGE.c
    rows = await conn.execute(sa.select(EDGE).where(
        e.unit == head.unit, e.src_id == base, e.type == "about", e.state == "live",
        e.basis == "inferred",
    ).with_for_update(read=True))
    head_scope = InputScope(head.excluded_identities, cast(Exposure, head.exposure), head.lineage)
    edges = []
    for old in rows.mappings():
        old_scope = InputScope(
            tuple(old["excluded_identities"]), old["exposure"], Lineage.from_json(old["lineage"]),
        )
        scope = inherited_scope(
            (head_scope, old_scope), build_lineage=Lineage(), minimum="external",
        )
        link = Link(
            type="about", source=head.id, target=old["dst_id"], basis="inferred",
            reasoning=old["reasoning"], confidence=old["confidence"],
        )
        edges.append({
            **_stamp(context), **_link_values(link), "id": f"edge:{uuid4().hex}",
            "unit": head.unit, "origin_room": head.origin_room, "identities": list(head.identities),
            "excluded_identities": list(scope.excluded_identities), "exposure": scope.exposure,
            "lineage": scope.lineage.to_json(),
        })
    if edges:
        await _register(conn, edges)
        await conn.execute(sa.insert(EDGE), edges)


async def _computed_about(conn: AsyncConnection, row: RowFacts, context: PassContext) -> None:
    people = {identity for identity in row.identities if identity is not None}
    if not people:
        return
    entity = ENTITY.c
    query = sa.select(
        entity.id, entity.exposure, entity.excluded_identities, entity.lineage,
    ).where(
        entity.unit == row.unit, entity.pointer_kind == "identity",
        entity.pointer_ref.in_(people), entity.state == "admitted",
    ).ext(distinct_on(entity.pointer_ref)).order_by(entity.pointer_ref, entity.id)
    pointers = (await conn.execute(query)).mappings().all()
    if not pointers:
        return

    source = InputScope(row.excluded_identities, cast(Exposure, row.exposure), row.lineage)
    edges = []
    for pointer in pointers:
        target = InputScope(
            tuple(pointer["excluded_identities"]), pointer["exposure"],
            Lineage.from_json(pointer["lineage"]),
        )
        scope = inherited_scope((source, target), build_lineage=Lineage(), minimum="external")
        link = Link(type="about", source=row.id, target=pointer["id"], basis="computed")
        edges.append({
            **_stamp(context), **_link_values(link), "id": f"edge:{uuid4().hex}",
            "unit": row.unit, "origin_room": row.origin_room, "identities": list(row.identities),
            "excluded_identities": list(scope.excluded_identities), "exposure": scope.exposure,
            "lineage": scope.lineage.to_json(),
        })
    await _register(conn, edges)
    await conn.execute(sa.insert(EDGE), edges)


async def _dependency(
    conn: AsyncConnection, stored: Stored, row: Mapping[str, Any], context: PassContext,
) -> None:
    if context.clock is not None or stored.base is None:
        return
    dependents = await dependent_claims(conn, row["unit"], stored.base)
    if dependents:
        at = (await conn.execute(sa.select(sa.func.now()))).scalar_one()
        await SqlGraphStimuli.append_in(conn, GraphStimulus(
            row["unit"], "dependency", stored.base, at, "admission",
            room=row["origin_room"], revision_id=stored.id, dependents=dependents,
            occurrence=stored.id,
        ))


def _stamp(context: PassContext) -> dict[str, Any]:
    provenance: dict[str, Any] = dict(context.provenance())
    if context.clock is not None:
        provenance.update(asserted_at=context.clock, occurred_at=context.clock)
    return provenance


def _link_values(link: Link) -> dict[str, Any]:
    return {
        "kind": "edge",
        "basis": link.basis,
        "reasoning": link.reasoning,
        "confidence": link.confidence,
        "state": "live",
        "type": link.type,
        "src_id": link.source,
        "dst_id": link.target,
    }


def _serialize(
    decision: Admitted,
    context: PassContext,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    proposal = fields(decision.proposal)
    row = decision.row
    shared = {
        **_stamp(context),
        "unit": row.unit,
        "origin_room": row.origin_room,
        "identities": list(row.identities),
        "excluded_identities": list(row.excluded_identities),
        "exposure": row.exposure,
        "lineage": row.lineage.to_json(),
    }
    planes: dict[str, Any] = {
        "asserted_at": (
            sa.func.clock_timestamp() if context.occasion == "operator" else sa.func.now()
        ),
        **shared,
        "id": row.id,
        "kind": row.kind,
        "basis": row.basis,
        "state": row.state,
        "reasoning": proposal.reasoning,
        "confidence": proposal.confidence,
    }
    links = decision.links
    if isinstance(proposal, AddEdge) or decision.survivor is not None:
        planes.update(_link_values(links[0]))
        links = links[1:]
    if decision.base is not None and decision.survivor is None:
        planes["replaces"] = decision.base.id
    if isinstance(proposal, CreateClaim):
        planes.update(
            part=proposal.part,
            grade=proposal.grade,
            content=proposal.content,
            prediction=proposal.prediction,
            window_end=proposal.window_end,
            valid_from=proposal.valid_from,
            valid_to=proposal.valid_to,
        )
    elif isinstance(proposal, CreateEntity):
        planes.update(name=proposal.name, aliases=list(proposal.aliases))
        if row.source_ref is not None:
            planes.update(
                source_ref_kind=row.source_ref.kind, source_ref_value=row.source_ref.value,
            )
    elif isinstance(proposal, CreateIntention):
        planes.update(_intention(decision, proposal))
    elif isinstance(proposal, Retire) and row.kind == "intention":
        planes.update(ending=proposal.ending, ended_at=sa.func.now())
    edges = [{**shared, **_link_values(link), "id": f"edge:{uuid4().hex}"} for link in links]
    return planes, edges


def _intention(decision: Admitted, proposal: CreateIntention) -> dict[str, Any]:
    guard = "done_when" if proposal.form == "achieve" else "holds_while"
    drop = proposal.drop_when
    return {
        "owner": decision.owner,
        "owed_to": decision.owed_to,
        "form": proposal.form,
        guard: proposal.condition.value,
        f"{guard}_form": proposal.condition.form,
        "drop_when": drop.value if drop else None,
        "drop_when_form": drop.form if drop else None,
        "trigger": proposal.trigger,
        "delivery_mode": proposal.delivery_mode,
        "deadline": proposal.deadline,
        "review_time": proposal.review_time,
    }
