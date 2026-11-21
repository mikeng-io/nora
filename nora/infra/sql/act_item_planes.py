"""Register executor log items with the authority of the act or pass that produced them."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.cognitive_graph_schema import NORA_ACT
from nora.memory import active_act_authority

_PROVENANCE = ("writer", "occasion", "pass", "trace", "model", "rev_code",
               "rev_instruction", "rev_skill", "rev_config", "rev_identity", "rev_policy")


async def executor_item_planes(
    conn: AsyncConnection, table: sa.Table, row_id: UUID, at: datetime, *,
    room: str, platform: str | None = None, person: str | None = None,
    task_id: UUID | None = None, tag: str | None = None,
) -> dict[str, Any]:
    sync_connection = conn.sync_connection
    if sync_connection is None:
        return {}
    registration = sync_connection.get_execution_options().get("item_registration")
    if registration is None:
        return {}
    active = active_act_authority()
    context = active.pass_context if active is not None else None
    parent = None
    if task_id is not None:
        parent = (await conn.execute(sa.select(NORA_ACT).where(
            NORA_ACT.c.executor == "computer.dispatch",
            NORA_ACT.c.idempotency_key == str(task_id),
        ))).mappings().one_or_none()
    stamp = {}
    if parent is not None:
        unit, room = parent["unit"], parent["origin_room"]
        persons = parent["persons"]
        stamp = {key: parent[key] for key in _PROVENANCE}
        stamp["act_id"] = parent["id"]
    elif (active is not None and context is not None
          and tag == context.pass_id and room == context.room):
        unit, persons = context.unit, (active.person,)
        stamp = context.provenance()
    else:
        unit = registration.scope.room_unit(platform, room) if platform else None
        persons = (person,)
    planes = await registration.planes(
        conn, table, unit=unit, occurred_at=at, persons=persons, room=room, key=(row_id,),
    )
    return {**planes, **stamp}
