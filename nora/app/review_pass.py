"""Schedule bounded, independent grades of due claims in each unit."""

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from nora.agent.dreamer import as_strands_tools
from nora.agent.surface import deferred_surface
from nora.app.cognition_session import AdaptorBudget, CognitionAgentRunner
from nora.domain.blocks import BlockDeclaration, ContextBlock, assemble
from nora.domain.lineage import room_key
from nora.instructions.dreamer import DreamerInstructions
from nora.language.catalogue_render import render_tool_catalogue
from nora.memory import (
    ActAuthority,
    AdmissionResult,
    AssembledPass,
    CreateClaim,
    PassContext,
    PassName,
    PassScope,
    Proposal,
    Refusal,
    ReviewInput,
    act_pass,
    grade_target,
    pass_shown,
    seed_pass_shown,
)
from nora.obs.tracing import TRACE_KIND, current_trace_id, root_span
from nora.policy import Policy

log = logging.getLogger(__name__)
REVIEW_RETRY_SECONDS = 300

_BLOCKS: tuple[BlockDeclaration, ...] = (
    BlockDeclaration("review_docket", 0, "Grade the due rows against later evidence."),
    BlockDeclaration("tool_catalogue", 1, "Choose from the review tools."),
)


class ReviewLessons(Protocol):
    async def due_lessons(self, unit: str) -> tuple[str, ...]: ...


class ReviewRooms(Protocol):
    async def scope_for(self, unit: str, reading_room: str) -> tuple[PassScope, bool]: ...


class ReviewAssembler(Protocol):
    async def assemble(
        self, pass_name: PassName, data: ReviewInput, floor: tuple[ContextBlock, ...],
        scope: PassScope, now: datetime,
    ) -> AssembledPass: ...


class ReviewSurface(Protocol):
    def bind(
        self, scope: PassScope, now: datetime, room_busy: Callable[[], None], *,
        stage: Callable[[list[Proposal | Refusal], set[str]], bool],
    ) -> list[Any]: ...


class ReviewAdmission(Protocol):
    async def admit(
        self, proposals: Iterable[Proposal], context: PassContext, shown: Iterable[str],
    ) -> AdmissionResult: ...


class _GradeBatch:
    def __init__(self, due: tuple[tuple[str, datetime], ...]) -> None:
        self.due = dict(due)
        self.grades: dict[str, CreateClaim] = {}
        self.valid = True

    def stage(self, proposals: list[Proposal | Refusal], shown: set[str]) -> bool:
        pending: dict[str, CreateClaim] = {}
        for proposal in proposals:
            if not isinstance(proposal, CreateClaim) or proposal.part != "grade":
                self.valid = False
                return False
            target = grade_target(proposal)
            if target is None or target not in self.due or not set(proposal.citations) <= shown:
                self.valid = False
                return False
            pending[target] = replace(proposal, reviewed_at=self.due[target])
        self.grades.update(pending)
        return True


class ReviewPass:
    def __init__(
        self, *, lessons: ReviewLessons, rooms: ReviewRooms, assembler: ReviewAssembler,
        surface: ReviewSurface, admission: ReviewAdmission, agent: CognitionAgentRunner,
        instructions: DreamerInstructions, budget: AdaptorBudget, model: str,
        revisions: Callable[[], Mapping[str, str | None]], policy: Policy,
    ) -> None:
        self._lessons, self._rooms, self._assembler = lessons, rooms, assembler
        self._surface, self._admission, self._agent = surface, admission, agent
        self._instructions, self._budget, self._model = instructions, budget, model
        self._revisions, self._policy = revisions, policy
        self._attempts: dict[str, tuple[tuple[tuple[str, datetime], ...], datetime]] = {}
        self._running: dict[str, asyncio.Task[None]] = {}

    def poll(self, now: datetime) -> None:
        for unit, home in self._policy.home_rooms().items():
            if unit not in self._running:
                room = room_key(home.platform, home.primary_id)
                self._running[unit] = asyncio.create_task(self._tick(unit, room, now))

    async def drain(self) -> None:
        await asyncio.gather(*self._running.values(), return_exceptions=True)

    def cancel(self) -> None:
        for task in self._running.values():
            task.cancel()

    async def _tick(self, unit: str, room: str, now: datetime) -> None:
        try:
            await self.run_once(unit, room, now)
        except Exception:
            log.exception("Review pass failed")
        finally:
            self._running.pop(unit)

    async def run_once(self, unit: str, room: str, now: datetime) -> None:
        scope, direct = await self._rooms.scope_for(unit, room)
        data = ReviewInput(await self._lessons.due_lessons(unit))
        assembled = await self._assembler.assemble("review", data, (), scope, now)
        if assembled.status == "unavailable":
            return
        due = assembled.review_due
        if not due:
            self._attempts.pop(unit, None)
            return
        previous = self._attempts.get(unit)
        if previous is not None and due == previous[0] and now < previous[1]:
            return
        self._attempts[unit] = due, now + timedelta(seconds=REVIEW_RETRY_SECONDS)
        with root_span("review", links=(), parent=None, **{TRACE_KIND: "generation"}):
            await self._judge(scope, direct, assembled, now)

    async def _judge(
        self, scope: PassScope, direct: bool, assembled: AssembledPass, now: datetime,
    ) -> None:
        instruction = self._instructions.for_objective("review")
        revs = self._revisions()
        context = PassContext(
            scope.unit, room_key(scope.room.platform, scope.room.conversation_id), direct,
            "review", "reflection", f"review:{uuid4().hex}", current_trace_id() or "",
            self._model, revs.get("rev_code") or "", instruction.sha256,
            revs.get("rev_skill") or "", revs.get("rev_config") or "",
            revs.get("rev_identity") or "", revs.get("rev_policy") or "",
        )
        batch = _GradeBatch(assembled.review_due)
        with act_pass(ActAuthority(context.unit, room=context.room, pass_context=context)):
            seed_pass_shown(assembled.shown)
            tools = as_strands_tools(
                self._surface.bind(scope, now, lambda: None, stage=batch.stage),
            )
            surface = deferred_surface(tools, resident=(tool.tool_name for tool in tools))
            blocks = {block.name: block for block in assembled.blocks}
            blocks["tool_catalogue"] = render_tool_catalogue(surface.catalogue)
            result = await self._agent.run(
                system_prompt=instruction.text,
                prompt=assemble(tuple(replace(blocks[spec.name], order=spec.order)
                                      for spec in _BLOCKS if spec.name in blocks)),
                tools=tools, budget=self._budget,
                trace_attributes={"review.unit": scope.unit, "review.pass_id": context.pass_id},
            )
            shown = pass_shown()
            if result.stop_reason == "end_turn" and batch.valid and batch.grades and shown:
                await self._admission.admit(batch.grades.values(), context, shown)
