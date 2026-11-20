"""Build a pass's context from its floor and one scoped graph retrieval."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from nora.domain.blocks import ContextBlock
from nora.memory.adapters.pass_anchors import AnchorRead, SqlPassAnchors
from nora.memory.adapters.presentation_blocks import GraphPresentationBlocks
from nora.memory.adapters.wake_blocks import wake_blocks
from nora.memory.app.retrieval import RetrievalService
from nora.memory.domain.pass_declarations import PassDeclaration, PassName, declaration
from nora.memory.domain.pass_inputs import (
    AssembledPass,
    DebriefInput,
    PassInput,
    PassScope,
    ProactiveInput,
    WakeInput,
)
from nora.memory.domain.presentations import lessons, related
from nora.memory.domain.retrieval import RetrievalResult


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
            anchors = seeds.anchors
            if "graph_lessons" in choice.presentations and seeds.collective[0].graph_id:
                nora = seeds.collective[0].graph_id
                anchors = replace(anchors, graph_ids=(*anchors.graph_ids, nora))
            result = await self._retrieval.retrieve(
                scope.room, anchors, bounds, concerns=seeds.concerns,
                as_of=scope.as_of, now=now,
            )
        builder = GraphPresentationBlocks(now, seeds.room, seeds.dm_rooms)
        due_ids: frozenset[str] = frozenset()
        if isinstance(data, ProactiveInput):
            due, due_ids = builder.due_intention(result, data.intention)
            floor = (due, *floor)
        elif isinstance(data, WakeInput):
            agenda, due_ids = wake_blocks(data, result, builder, seeds.anchors.graph_ids)
            floor = (*agenda, *floor)
        blocks, shown = _presentation_order(
            choice, seeds, result, floor, now, open_room_and_unit=isinstance(data, DebriefInput),
            floor_ids=due_ids,
        )
        return AssembledPass(
            blocks, result.status, result.cause, shown,
            not isinstance(data, ProactiveInput) or data.intention in due_ids,
        )


def _presentation_order(
    choice: PassDeclaration, seeds: AnchorRead, result: RetrievalResult,
    floor: Sequence[ContextBlock], now: datetime, *, open_room_and_unit: bool = False,
    floor_ids: frozenset[str] = frozenset(),
) -> tuple[tuple[ContextBlock, ...], frozenset[str]]:
    builder = GraphPresentationBlocks(now, seeds.room, seeds.dm_rooms)
    early: list[ContextBlock] = []
    late: list[ContextBlock] = []
    shown: set[str] = set(floor_ids)
    chosen = choice.presentations
    if "graph_profile" in chosen:
        for subject in seeds.collective:
            block, ids = builder.profile_block(result, subject, seeds.names)
            early.append(block)
            shown.update(ids)
            if (choice.input_type is WakeInput and subject.pointer_ref == "nora"
                    and subject.graph_id):
                shown.add(subject.graph_id)
        for subject in seeds.people:
            block, ids = builder.profile_block(result, subject, seeds.names)
            late.append(block)
            shown.update(ids)
    if "graph_lessons" in chosen:
        rows = lessons(result)
        early.insert(1, builder.lessons_block(result, rows))
        shown.update(row.graph_id for row in rows)
    if "graph_standing" in chosen:
        block, ids = builder.standing_block(
            result, seeds.room_ref, seeds.room_entity, open_room_and_unit=open_room_and_unit,
        )
        late.append(block)
        shown.update(ids)
    if "related" in chosen:
        rows = related(result, shown)
        late.append(builder.related_block(result, rows))
        shown.update(row.graph_id for row in rows)
    return (*early, *floor, *late), frozenset(shown if result.status != "unavailable" else ())
