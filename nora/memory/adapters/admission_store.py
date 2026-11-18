"""Serialize admitted proposals to the shared graph store."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_store import GraphWriteError, SqlCognitiveGraph
from nora.memory.adapters.admission_rows import GRAPH_TABLES, SqlAdmissionRows
from nora.memory.domain.admission import Admitted, fields
from nora.memory.domain.admission_ports import AdmissionUnavailableError
from nora.memory.domain.admission_proposals import (
    AddEdge,
    CreateClaim,
    CreateEntity,
    CreateIntention,
    Link,
    PassContext,
    Retire,
    RowFacts,
)


class SqlAdmissionStore:
    def __init__(self, rows: SqlAdmissionRows, graph: SqlCognitiveGraph) -> None:
        self._rows = rows
        self._graph = graph

    async def read(self, unit: str, ids: frozenset[str]) -> Mapping[str, RowFacts]:
        return await self._rows.read(unit, ids)

    async def write(self, decision: Admitted, context: PassContext) -> None:
        row, edges = _serialize(decision, context)
        base, survivor = decision.base, decision.survivor
        try:
            await self._graph.write_admission(
                GRAPH_TABLES[decision.row.kind],
                row,
                edges,
                base_state=base.state if base is not None and survivor is None else None,
                retire=isinstance(decision.proposal, Retire),
                merge=(base.id, survivor.id) if base is not None and survivor is not None else None,
            )
        except GraphWriteError as exc:
            raise AdmissionUnavailableError(commit_uncertain=exc.commit_uncertain) from exc


def _stamp(context: PassContext) -> dict[str, str]:
    return {
        "writer": context.writer,
        "occasion": context.occasion,
        "pass": context.pass_id,
        "trace": context.trace,
        "model": context.model,
        "rev_code": context.rev_code,
        "rev_instruction": context.rev_instruction,
        "rev_skill": context.rev_skill,
        "rev_config": context.rev_config,
        "rev_identity": context.rev_identity,
        "rev_policy": context.rev_policy,
    }


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
        "persons": list(row.persons),
        "excluded_persons": list(row.excluded_persons),
        "exposure": row.exposure,
        "lineage": row.lineage.to_json(),
    }
    planes: dict[str, Any] = {
        **shared,
        "id": row.id,
        "asserted_at": sa.func.now(),
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
            content=proposal.content,
            prediction=proposal.prediction,
            window_end=proposal.window_end,
            valid_from=proposal.valid_from,
            valid_to=proposal.valid_to,
        )
    elif isinstance(proposal, CreateEntity):
        planes.update(name=proposal.name, aliases=list(proposal.aliases))
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
