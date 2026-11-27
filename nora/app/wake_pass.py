"""Run one bounded reflection pass for one claimed set of graph stimuli."""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from nora.agent.dreamer import AdaptorBudget, as_strands_tools
from nora.agent.surface import deferred_surface
from nora.app.cognition_agent_runner import CognitionAgentRunner
from nora.domain.blocks import BlockDeclaration, ContextBlock, assemble
from nora.domain.lineage import room_key
from nora.domain.stimuli import LIMIT_STOP_REASONS
from nora.instructions.dreamer import DreamerInstructions
from nora.language.catalogue_render import render_tool_catalogue
from nora.memory import (
    ActAuthority,
    AssembledPass,
    PassContext,
    PassName,
    PassScope,
    WakeClaim,
    WakeClosed,
    WakeFailed,
    WakeInput,
    WakeOutcome,
    act_pass,
    seed_pass_shown,
)
from nora.obs.tracing import current_trace_id

log = logging.getLogger(__name__)

_BLOCKS: tuple[BlockDeclaration, ...] = (
    BlockDeclaration("agenda", 0, "Orient around the changes and open intentions."),
    BlockDeclaration("clock", 1, "Interpret evidence at the pass instant."),
    BlockDeclaration("members", 2, "Identify the people in the reading room."),
    BlockDeclaration("presence", 3, "Read the presence visible in this room."),
    BlockDeclaration("transcript", 4, "Read the room's recent conversation."),
    BlockDeclaration("rooms", 5, "Locate the conversation in its unit."),
    BlockDeclaration("reads", 6, "Know where the read tools look."),
    BlockDeclaration("documents", 7, "Find documents available in the reading room."),
    BlockDeclaration("lesson_evidence", 8, "Read claimed verdicts and corrections."),
    BlockDeclaration("tool_catalogue", 9, "Choose from the offered tools."),
)


def wake_lane(unit: str) -> str:
    return f"wake:{unit}"


class WakeAssembler(Protocol):
    async def assemble(
        self, pass_name: PassName, data: WakeInput, floor: Sequence[ContextBlock],
        scope: PassScope, now: datetime,
    ) -> AssembledPass: ...


class WakeRooms(Protocol):
    async def scope(self, claim: WakeClaim) -> tuple[PassScope, bool]: ...

    async def floor(
        self, claim: WakeClaim, scope: PassScope, now: datetime,
    ) -> Sequence[ContextBlock]: ...


class WakeSurface(Protocol):
    def bind(
        self, scope: PassScope, now: datetime, room_busy: Callable[[], None],
    ) -> Sequence[Any]: ...


class WakePassRunner:
    def __init__(
        self, *, rooms: WakeRooms, assembler: WakeAssembler, surface: WakeSurface,
        agent: CognitionAgentRunner, instructions: DreamerInstructions, budget: AdaptorBudget,
        model: str, revisions: Callable[[], Mapping[str, str | None]],
        occasion: PassName = "wake", clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._rooms = rooms
        self._assembler = assembler
        self._surface = surface
        self._agent = agent
        self._instructions = instructions
        self._budget = budget
        self._model = model
        self._revisions = revisions
        self._occasion = occasion
        self._clock = clock

    async def run(self, claimed: WakeClaim) -> WakeOutcome:
        try:
            return await self._run(claimed)
        except TimeoutError:
            return WakeFailed("timeout")
        except Exception:
            log.exception("Wake agent failed")
            return WakeFailed("error")

    async def _run(self, claimed: WakeClaim) -> WakeOutcome:
        now = self._clock() if self._clock is not None else claimed.claimed
        scope, direct = await self._rooms.scope(claimed)
        instruction = self._instructions.for_objective(self._occasion.replace("_", "-"))
        revs = self._revisions()
        room = room_key(scope.room.platform, scope.room.conversation_id)
        context = PassContext(
            scope.unit, room, direct, self._occasion, "reflection", claimed.pass_id,
            current_trace_id() or "", self._model, revs.get("rev_code") or "",
            instruction.sha256, revs.get("rev_skill") or "", revs.get("rev_config") or "",
            revs.get("rev_identity") or "", revs.get("rev_policy") or "",
            clock=now if self._occasion == "wake_history" else None,
        )
        authority = ActAuthority(context.unit, room=context.room, pass_context=context)
        busy = False

        def room_busy() -> None:
            nonlocal busy
            busy = True

        with act_pass(authority):
            room_inputs = {block.name: block for block in
                           await self._rooms.floor(claimed, scope, now)}
            floor = tuple(room_inputs[spec.name] for spec in _BLOCKS if spec.name in room_inputs)
            assembled = await self._assembler.assemble(
                self._occasion, WakeInput(claimed.stimuli), floor, scope, now,
            )
            if assembled.status == "unavailable":
                return WakeFailed("error")
            claimed_ids = tuple(stimulus.graph_id for stimulus in claimed.stimuli)
            if assembled.agenda_stimuli != claimed_ids:
                return WakeFailed("error")
            if context.clock is not None:
                observed_ids = tuple(id_ for id_, _ in assembled.observed)
                if observed_ids != claimed_ids:
                    return WakeFailed("error")
            seed_pass_shown(assembled.shown)
            tools = as_strands_tools(self._surface.bind(scope, now, room_busy))
            surface = deferred_surface(tools, resident=(tool.tool_name for tool in tools))
            blocks = (*assembled.blocks, render_tool_catalogue(surface.catalogue))
            deadline = asyncio.timeout(self._budget.timeout_seconds)
            try:
                async with deadline:
                    result = await self._agent.run(
                        system_prompt=instruction.text,
                        prompt=assemble(tuple(
                            replace(block, order=i) for i, block in enumerate(blocks)
                        )),
                        tools=tools, budget=self._budget,
                        trace_attributes={
                            "wake.unit": scope.unit, "wake.pass_id": context.pass_id,
                            "wake.lane": wake_lane(scope.unit),
                        },
                    )
            except TimeoutError:
                if busy:
                    return WakeFailed("room_busy")
                return WakeFailed("bound" if deadline.expired() else "timeout")
            except Exception:
                if busy:
                    return WakeFailed("room_busy")
                raise
        if busy:
            return WakeFailed("room_busy")
        if result.stop_reason in LIMIT_STOP_REASONS:
            return WakeFailed("bound")
        if result.stop_reason == "end_turn":
            return WakeClosed(observed=assembled.observed)
        return WakeFailed("error")
