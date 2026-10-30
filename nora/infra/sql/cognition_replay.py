"""Read a recorded wake's stimulus batch from a restored snapshot."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.stimuli import Stimulus, UnitView
from nora.infra.sql.schema import COGNITION_SESSION, COGNITION_WAKE, STIMULUS
from nora.infra.sql.stimuli import jsonb_object, unclaimed_ids


async def recorded_wake(engine: AsyncEngine, wake_id: int) -> tuple[int, UnitView]:
    async with engine.connect() as conn:
        wake = (await conn.execute(sa.select(COGNITION_WAKE).where(
            COGNITION_WAKE.c.id == wake_id))).mappings().one()
        excluded = list(unclaimed_ids(wake["detail"]))
        session = (await conn.execute(sa.select(
            COGNITION_SESSION.c.agenda, COGNITION_SESSION.c.started_at_ms).where(
            COGNITION_SESSION.c.wake_id == wake_id))).mappings().one_or_none()
        agenda: dict[str, Any] | None = (
            jsonb_object(session["agenda"]) if session is not None else None)
        # at_ms is the pending watermark (tick minus settle margin), not the
        # session clock. Due time stimuli can be written at the tick itself.
        at_ms = (session["started_at_ms"] if session is not None
                 else int(wake["recorded_at"].timestamp() * 1000))
        ids = None
        if agenda:
            ids = {sid for item in agenda.get("items", []) for sid in item["stimulus_ids"]}
            ids.update(agenda.get("deferred_stimulus_ids", []))
            ids.update(agenda.get("skipped_stimulus_ids", []))
        batch = (STIMULUS.c.id.in_(ids) if ids is not None else sa.and_(
            STIMULUS.c.id > wake["from_stimulus_id"],
            STIMULUS.c.id <= wake["through_stimulus_id"],
            sa.func.coalesce(STIMULUS.c.due_ms,
                sa.extract("epoch", STIMULUS.c.recorded_at) * 1000) <= wake["at_ms"]))
        rows = (await conn.execute(sa.select(STIMULUS).where(
            STIMULUS.c.unit == wake["unit"],
            batch, STIMULUS.c.id.not_in(excluded),
            STIMULUS.c.recorded_at <= datetime.fromtimestamp(at_ms / 1000, UTC),
        ).order_by(STIMULUS.c.id))).mappings().all()
    if len(rows) != wake["stimulus_count"]:
        raise ValueError("recorded wake stimulus batch is incomplete")
    return at_ms, UnitView(unit=wake["unit"], pending=tuple(
        Stimulus(**{k: r[k] for k in Stimulus.__dataclass_fields__ if k in r},
                 recorded_at_ms=int(r["recorded_at"].timestamp() * 1000)) for r in rows
    ), last_activity_ms=None, last_wake=None)
