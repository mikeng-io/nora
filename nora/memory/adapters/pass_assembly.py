"""Build a pass's context from its floor and one scoped graph retrieval."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from nora.domain.blocks import ContextBlock
from nora.domain.blocks import assemble as render_blocks
from nora.memory.adapters.pass_anchors import AnchorRead, SqlPassAnchors
from nora.memory.adapters.presentation_blocks import GraphPresentationBlocks
from nora.memory.app.retrieval import RetrievalService
from nora.memory.domain.pass_declarations import PassDeclaration, PassName, declaration
from nora.memory.domain.pass_inputs import PassInput, PassScope
from nora.memory.domain.presentations import lessons, related
from nora.memory.domain.retrieval import RetrievalResult, RetrievalStatus


@dataclass(frozen=True, slots=True)
class AssembledPass:
    blocks: tuple[ContextBlock, ...]
    status: RetrievalStatus
    cause: str | None

    def render(self) -> str:
        return render_blocks(tuple(replace(block, order=i) for i, block in enumerate(self.blocks)))


class PassAssembler:
    def __init__(self, anchors: SqlPassAnchors, retrieval: RetrievalService) -> None:
        self._anchors = anchors
        self._retrieval = retrieval

    async def assemble(
        self, pass_name: PassName, data: PassInput, floor: Sequence[ContextBlock],
        scope: PassScope, now: datetime,
    ) -> AssembledPass:
        choice = declaration(pass_name)
        bounds = choice.bounds(scope.as_of or now)
        seeds = await self._anchors.build(choice, data, scope, now)
        if seeds.cause is not None:
            result = RetrievalResult("unavailable", bounds, cause=seeds.cause)
        else:
            result = await self._retrieval.retrieve(
                scope.room, seeds.anchors, bounds, concerns=seeds.concerns,
                as_of=scope.as_of, now=now,
            )
        blocks = _presentation_order(choice, seeds, result, floor, now)
        return AssembledPass(blocks, result.status, result.cause)


def _presentation_order(
    choice: PassDeclaration, seeds: AnchorRead, result: RetrievalResult,
    floor: Sequence[ContextBlock], now: datetime,
) -> tuple[ContextBlock, ...]:
    builder = GraphPresentationBlocks(now, seeds.room, seeds.dm_rooms)
    early: list[ContextBlock] = []
    late: list[ContextBlock] = []
    shown: set[str] = set()
    chosen = choice.presentations
    if "graph_profile" in chosen:
        for subject in seeds.collective:
            block, ids = builder.profile_block(result, subject, seeds.names)
            early.append(block)
            shown.update(ids)
        for subject in seeds.people:
            block, ids = builder.profile_block(result, subject, seeds.names)
            late.append(block)
            shown.update(ids)
    if "graph_lessons" in chosen:
        rows = lessons(result)
        early.insert(1, builder.lessons_block(result, rows))
        shown.update(row.graph_id for row in rows)
    if "graph_standing" in chosen:
        block, ids = builder.standing_block(result, seeds.room_ref, seeds.room_entity)
        late.append(block)
        shown.update(ids)
    if "related" in chosen:
        late.append(builder.related_block(result, related(result, shown)))
    return (*early, *floor, *late)
