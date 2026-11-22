"""Wire the due-intention fire path to the live graph and room slots."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.app.proactive_fire import FireAgent, FireRoom, ProactiveFire
from nora.app.proactive_poller import ProactiveLanding, ProactivePoller
from nora.config import HeartbeatConfig
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages.graph_debrief import Wiring


def build_proactive(
    memory_wire: Any, engine: AsyncEngine, heartbeat: HeartbeatConfig,
    clock: Callable[[], datetime], slots: ConversationSlots, room: FireRoom,
    agent: FireAgent, debrief: Wiring, assembler: Any,
) -> ProactivePoller:
    claims = memory_wire.proactive_claims(engine)
    turn = ProactiveFire(
        room, agent, assembler,
        claims, memory_wire.act_envelope(engine), debrief,
        memory_wire.delivery_arbitration(engine, room.policy), clock,
    )
    return ProactivePoller(
        claims, slots, ProactiveLanding(room.policy, room.access, room.adapters), turn,
        clock, tick_seconds=heartbeat.tick_seconds, timeout_seconds=heartbeat.timeout_seconds,
    )
