"""Settle the handed-over tool calls a dead process left running."""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

import nora.memory as memory
from nora.infra.sql.cognitive_graph_schema import NORA_ACT, NORA_ACT_OUTCOME
from nora.memory import Outcome

log = logging.getLogger("nora.handover")

_EXECUTOR = "tool.handover"


async def sweep_handovers(engine: Any) -> int:
    """Record `uncertain` on every handed-over call with no outcome; return how many.

    A call lives only in the process that handed it over, and it is never run again, so what
    it did is unknown.
    """
    if "item_registration" not in engine.get_execution_options():
        return 0
    envelope = memory.wire.act_envelope(engine)
    # ponytail: at start every such act belongs to a dead process because the deployment runs
    # one; a second process would need an owner or lease on the act.
    act, outcome = NORA_ACT.c, NORA_ACT_OUTCOME.c
    stmt = sa.select(act.destination).where(
        act.executor == _EXECUTOR, act.state == "live",
        ~sa.exists().where(outcome.act_id == act.id),
    ).order_by(act.destination)
    async with engine.connect() as conn:
        destinations = list((await conn.scalars(stmt)).all())
    settled = 0
    for destination in destinations:
        try:
            found = await envelope.by_destination(_EXECUTOR, destination)
            if found is not None:
                await envelope.record_act_outcome(found, Outcome("uncertain"))
                settled += 1
        except Exception:
            log.exception("handed-over tool call could not be settled (%s)", destination)
    return settled
