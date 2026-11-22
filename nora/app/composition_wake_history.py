"""Compose the graph replay command with only its read and wake dependencies."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime

import nora.memory as memory
from nora.access import IdentityAccess
from nora.agent import factory
from nora.agent.dreamer import DreamerRuntime, RegistryToolSource
from nora.agent.egress import LeakGuard
from nora.agent.lineage import LineagePolicy
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.app.cli import _database_url, _persons, _policy
from nora.app.composition_dreamer import dreamer_model_source
from nora.app.composition_helpers import _adaptor_budget
from nora.app.composition_stores import build_doctrine, instructions, managed_identity
from nora.app.composition_wake import build_wake_runner
from nora.app.startup import StartupError
from nora.app.wake_pass import WakePassRunner
from nora.app.wake_rooms import WakeRoomSlots
from nora.config import Config
from nora.domain.blocks import ContextBlock
from nora.engagement.slot_keys import resolve_slot_room
from nora.engagement.slots import ConversationSlots
from nora.infra.sql import create_readonly_sql_engine, create_sql_engine
from nora.infra.sql.item_registration import RuntimeRevisions
from nora.language.context_render import render_clock, render_reads
from nora.obs.metrics import Metrics
from nora.policy import Policy


class WakeHistoryRooms:
    def __init__(self, policy: Policy, access: IdentityAccess, display_name: str) -> None:
        self._policy = policy
        self._access = access
        self._display_name = display_name

    async def scope(self, claim: memory.WakeClaim) -> tuple[memory.PassScope, bool]:
        room = resolve_slot_room(
            self._policy.rooms(), claim.reading_room, unit=claim.group.unit,
        )
        if room is None or not room.absorbed:
            raise LookupError("Historical wake reading room unavailable")
        direct = bool(room.ids & await self._access.direct_addresses(room.platform))
        return memory.PassScope(
            memory.ReadingRoom(room.platform, room.primary_id),
            room.unit, nora_name=self._display_name,
        ), direct

    async def floor(
        self, claim: memory.WakeClaim, scope: memory.PassScope, now: datetime,
    ) -> tuple[ContextBlock, ...]:
        return (
            render_clock(now=now, last_session_ms=None, report_first_look=False),
            render_reads(((claim.reading_room, "Reading room"),)),
        )


@asynccontextmanager
async def replay_dependencies(
    cfg: Config, *, inspect: bool,
) -> AsyncIterator[tuple[memory.wire.SqlReplayReceipts, WakePassRunner | None, int]]:
    async with AsyncExitStack() as stack:
        metrics = Metrics()
        policy = _policy(cfg)
        persons = _persons(cfg) if cfg.identity.backend == "file" else None
        engine = (create_readonly_sql_engine if inspect else create_sql_engine)(_database_url(cfg))
        stack.push_async_callback(engine.dispose)
        managed = await managed_identity(cfg, persons, policy, engine)
        if managed.session is not None:
            stack.push_async_callback(managed.session.close)
        access: IdentityAccess = managed.services.access
        await access.refresh_aliases(ttl_seconds=0)
        lineage = LineagePolicy.from_policy(policy, access=access)
        store = memory.wire.replay_receipts(engine, access, lineage, policy)
        if inspect:
            yield store, None, cfg.cognition.batch_max
            return

        corpus = instructions(cfg, metrics).dreamer_instructions
        corpus.for_objective("wake-history")
        if not cfg.dreamer.enabled:
            raise StartupError("Graph replay requires the Dreamer runtime")
        base_url, key, model = cfg.endpoint("dreamer", cfg.dreamer_model())
        client = factory.create_client(base_url=base_url, api_key=key)
        stack.push_async_callback(client.close)
        content_checks = cfg.content_checks.enabled
        doctrine = build_doctrine(cfg, content_checks, metrics).doctrine
        runtime = DreamerRuntime(
            models=dreamer_model_source(cfg, cfg.llm, model, client),
            instructions=corpus, metrics=metrics, model_name=model,
            tools=RegistryToolSource(
                registry=ToolRegistry(content_checks=content_checks),
                store=None, persons=persons, honcho=None, metrics=metrics, access=access,
            ),
            budgets={}, default_budget=_adaptor_budget(cfg.dreamer.budget_for("cognition")),
            retry=factory.build_retry_policy(timeout_seconds=cfg.llm.timeout_seconds),
            content_checks=content_checks, tool_budget_overrides=cfg.llm.budgets,
        )
        slots = ConversationSlots(maxlen=1, spawn=asyncio.create_task, policy=policy)
        admission = memory.wire.admission(
            engine, persons, policy, LeakGuard(doctrine, cfg.doctrine.canary),
            content_checks=content_checks, room_slots=WakeRoomSlots(policy, slots), access=access,
        )
        room_for: Callable[[str, str], Room] = lambda platform, room: Room(  # noqa: E731
            platform=platform, conversation_id=room, roster_source=None, access=access,
        )
        surface = memory.wire.WakeToolSurface(
            admission, memory.wire.graph_read_tools(engine, access, lineage, None, None),
            room_for, metrics, content_checks=content_checks,
            offered=memory.wire.WAKE_HISTORY_TOOLS, allowed_operations=memory.wire.WAKE_OPERATIONS,
        )
        revisions = RuntimeRevisions(cfg.model_dump(), policy, persons)
        wake = build_wake_runner(
            cfg, runtime, corpus, WakeHistoryRooms(policy, access, cfg.doctrine.display_name),
            memory.wire.pass_assembler(engine, access, lineage, None, None), surface, metrics,
            revisions=revisions.current, model=model, occasion="wake_history",
        )
        yield store, wake, cfg.cognition.batch_max
