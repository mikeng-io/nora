"""Compute one scoped verdict and publish its stimulus in the caller's transaction."""

from collections.abc import Callable
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.graph_stimuli import GraphStimulus
from nora.infra.sql.cognitive_graph_schema import EDGE, INTENTION, INTENTION_VERDICT
from nora.infra.sql.cognitive_graph_store import RowData, SqlCognitiveGraph, visible_to_reads
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.memory.adapters.lesson_tally import rebuild_lessons_for_verdict


async def record_intention_verdicts(
    conn: AsyncConnection, graph: SqlCognitiveGraph, clock: Callable[[], datetime],
) -> None:
    i, v = INTENTION.c, INTENTION_VERDICT.c
    stmt = sa.select(INTENTION).where(
        i.form == "achieve", i.state == "ended", visible_to_reads(INTENTION),
        ~sa.exists().where(v.unit == i.unit, v.intention_id == i.id),
    ).order_by(i.unit, i.id).with_for_update(skip_locked=True)
    for row in (await conn.execute(stmt)).mappings():
        await _record(conn, graph, row, clock)


async def _record(
    conn: AsyncConnection, graph: SqlCognitiveGraph, intention: RowData,
    clock: Callable[[], datetime],
) -> None:
    e = EDGE.c
    unit, id_ = intention["unit"], intention["id"]
    witness = (await conn.execute(sa.select(EDGE).where(
        e.unit == unit, e.src_id == id_, e.type == "closed_by", e.state == "live",
        visible_to_reads(EDGE),
    ))).mappings().one_or_none()
    ended_at = intention["ended_at"]
    # The closed-by edge pins the witness's inherited scope at closure.
    inputs = (intention, witness) if witness is not None else (intention,)
    planes = await graph.aggregate_planes(
        conn, INTENTION_VERDICT, unit=unit, key=(id_,), occurred_at=ended_at,
        asserted_at=ended_at, identities=intention["identities"], room=intention["origin_room"],
        inputs=inputs, minimum="internal",
    )
    role = "drop_when_form" if intention["ending"] == "dropped" else "done_when_form"
    stmt = insert(INTENTION_VERDICT).values(
        **planes, intention_id=id_, ending=intention["ending"], ended_at=ended_at,
        witness_id=witness["dst_id"] if witness is not None else None,
        form=intention["form"], owed_to=intention["owed_to"], condition_form=intention[role],
    ).on_conflict_do_nothing(constraint="pk_intention_verdict").returning(
        INTENTION_VERDICT.c.graph_id,
    )
    graph_id = await conn.scalar(stmt)
    if graph_id is None:
        return
    at = clock()
    if planes["exposure"] != "operator":
        await SqlGraphStimuli.append_in(conn, GraphStimulus(
            unit, "verdict", graph_id, at, "verify",
            intention["origin_room"], intention["owed_to"],
        ))
    await rebuild_lessons_for_verdict(conn, graph, unit=unit, intention_id=id_, at=at)
