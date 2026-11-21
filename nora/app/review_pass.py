"""Schedule bounded, independent grades of due claims in each unit."""

import asyncio
import logging
from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from nora.agent.dreamer import as_strands_tools
from nora.agent.surface import deferred_surface
from nora.app.cognition_session import AdaptorBudget, CognitionAgentRunner
from nora.domain.blocks import BlockDeclaration, ContextBlock, assemble
from nora.domain.lineage import room_key
from nora.engagement.slot_keys import resolve_slot_room
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
    ReviewCursor,
    ReviewInput,
    ReviewTarget,
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


class ReviewTargets(Protocol):
    async def due_review_targets(self, unit: str, now: datetime) -> tuple[ReviewTarget, ...]: ...


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


@dataclass(frozen=True, slots=True)
class _UnitAttempt:
    room: str
    due: frozenset[tuple[str, datetime]]
    retry_at: datetime

    def blocks(self, due: frozenset[tuple[str, datetime]], now: datetime) -> bool:
        return due == self.due and now < self.retry_at


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
        self, *, targets: ReviewTargets, rooms: ReviewRooms, assembler: ReviewAssembler,
        surface: ReviewSurface, admission: ReviewAdmission, agent: CognitionAgentRunner,
        instructions: DreamerInstructions, budget: AdaptorBudget, model: str,
        revisions: Callable[[], Mapping[str, str | None]], policy: Policy,
        clock: Callable[[], datetime],
    ) -> None:
        self._targets, self._rooms, self._assembler = targets, rooms, assembler
        self._surface, self._admission, self._agent = surface, admission, agent
        self._instructions, self._budget, self._model = instructions, budget, model
        self._revisions, self._policy, self._clock = revisions, policy, clock
        # ponytail: retry and rotation state is in memory, so a restart resets both.
        self._attempts: dict[str, _UnitAttempt] = {}
        self._cursors: dict[tuple[str, str], ReviewCursor] = {}
        self._running: dict[str, asyncio.Task[None]] = {}

    def poll(self, now: datetime) -> None:
        for unit in {room.unit for room in self._policy.rooms() if room.absorbed}:
            if unit not in self._running:
                self._running[unit] = asyncio.create_task(self._tick(unit, now))

    async def drain(self) -> None:
        await asyncio.gather(*self._running.values(), return_exceptions=True)

    def cancel(self) -> None:
        for task in self._running.values():
            task.cancel()

    async def _tick(self, unit: str, now: datetime) -> None:
        try:
            targets = await self._due_rooms(unit, now)
            due = frozenset((target.graph_id, target.revision)
                            for entries in targets.values() for target in entries)
            previous = self._attempts.get(unit)
            if not due:
                if previous is not None:
                    self._attempts[unit] = replace(previous, due=due)
                return
            if previous is not None and previous.blocks(due, now):
                return
            rooms = sorted(targets)
            start = bisect_right(rooms, previous.room if previous is not None else "")
            for room in (*rooms[start:], *rooms[:start]):
                if await self._review_room(unit, room, now, targets[room], due):
                    return
        except Exception:
            log.exception("Review pass failed")
        finally:
            self._running.pop(unit)

    async def _due_rooms(self, unit: str, now: datetime) -> dict[str, list[ReviewTarget]]:
        allowed = self._policy.rooms()
        home = self._policy.home_rooms().get(unit)
        rooms: dict[str, list[ReviewTarget]] = {}
        for target in await self._targets.due_review_targets(unit, now):
            origin = target.origin_room
            room = home if origin is None else resolve_slot_room(allowed, origin, unit=unit)
            if room is not None and room.absorbed:
                rooms.setdefault(room_key(room.platform, room.primary_id), []).append(target)
        return rooms

    async def _review_room(
        self, unit: str, room: str, now: datetime, targets: list[ReviewTarget],
        due: frozenset[tuple[str, datetime]],
    ) -> bool:
        """Return whether to stop scanning rooms this tick."""
        try:
            scope, direct = await self._rooms.scope_for(unit, room)
        except LookupError:
            return False
        home = self._policy.home_rooms().get(unit)
        key = unit, room
        data = ReviewInput(
            tuple(target.graph_id for target in targets if target.part == "lesson"),
            after=self._cursors.get(key),
            include_roomless=home is not None and room == room_key(home.platform, home.primary_id),
            guesses=tuple(target.graph_id for target in targets if target.part == "guess"),
        )
        assembled = await self._assembler.assemble("review", data, (), scope, now)
        if assembled.status == "unavailable":
            return False
        if assembled.review_next is None:
            self._cursors.pop(key, None)
            return False
        if not assembled.review_due:
            self._cursors[key] = assembled.review_next
            return False
        # Replace only the revisions the judge can see on this page.
        revisions = dict(due)
        revisions.update(assembled.review_due)
        reviewed = frozenset(revisions.items())
        started = self._clock()
        self._cursors[key] = assembled.review_next
        with root_span("review", links=(), parent=None, **{TRACE_KIND: "generation"}):
            self._attempts[unit] = _UnitAttempt(
                room, reviewed, started + timedelta(seconds=REVIEW_RETRY_SECONDS),
            )
            await self._judge(scope, direct, assembled, now)
        return True

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
