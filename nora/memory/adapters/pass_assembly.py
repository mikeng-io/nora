"""Build a pass's context from its floor and one scoped graph retrieval."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from nora.domain.blocks import ContextBlock
from nora.memory.adapters.pass_anchors import AnchorRead, SqlPassAnchors
from nora.memory.adapters.presentation_blocks import GraphPresentationBlocks
from nora.memory.adapters.review_blocks import review_block
from nora.memory.adapters.wake_blocks import wake_blocks
from nora.memory.app.act_scope import active_act_authority
from nora.memory.app.retrieval import RetrievalService
from nora.memory.domain.pass_declarations import PassDeclaration, PassName, declaration
from nora.memory.domain.pass_inputs import (
    AssembledPass,
    DebriefInput,
    PassInput,
    PassScope,
    ProactiveInput,
    ReviewInput,
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
        authority = active_act_authority()
        context = authority.pass_context if authority is not None else None
        clock = context.clock if context is not None else None
        if clock is not None:
            scope = replace(scope, as_of=None)
        bounds = choice.bounds(scope.as_of or now, clock=clock)
        if clock is not None and isinstance(data, WakeInput) and data.stimuli:
            since = min(stimulus.eligible_at for stimulus in data.stimuli)
            bounds = replace(bounds, since=min(bounds.since, since),
                             rows=max(bounds.rows, len(data.stimuli)))
        seeds = await self._anchors.build(choice, data, scope, now, ceiling=clock)
        # Selected review targets and their evidence can predate the default window.
        if seeds.review_since is not None:
            bounds = replace(bounds, since=min(bounds.since, seeds.review_since))
        if seeds.cause is not None:
            result = RetrievalResult("unavailable", bounds, cause=seeds.cause)
        else:
            anchors = seeds.anchors
            if "graph_lessons" in choice.presentations and seeds.collective[0].graph_id:
                nora = seeds.collective[0].graph_id
                anchors = replace(anchors, graph_ids=(*anchors.graph_ids, nora))
            result = await self._retrieval.retrieve(
                scope.room, anchors, bounds,
                concerns=scope.concerns if clock is not None else seeds.concerns,
                as_of=scope.as_of, now=now,
            )
        builder = GraphPresentationBlocks(now, seeds.room, seeds.dm_rooms)
        due_ids: frozenset[str] = frozenset()
        agenda_stimuli: tuple[str, ...] = ()
        review_due: tuple[tuple[str, datetime], ...] = ()
        if isinstance(data, ProactiveInput):
            due, due_ids = builder.due_intention(result, data.intention)
            floor = (due, *floor)
        elif isinstance(data, WakeInput):
            agenda, due_ids, agenda_stimuli = wake_blocks(
                data, result, builder, seeds.anchors.graph_ids,
            )
            floor = (*agenda, *floor)
        elif isinstance(data, ReviewInput) and seeds.review_due:
            docket, due_ids, review_due = review_block(
                result, builder, seeds.review_due, seeds.review_evidence,
            )
            floor = (docket, *floor)
        blocks, shown = _presentation_order(
            choice, seeds, result, floor, now, open_room_and_unit=isinstance(data, DebriefInput),
            floor_ids=due_ids,
        )
        observed: tuple[tuple[str, str], ...] = ()
        if clock is not None and isinstance(data, WakeInput):
            digests = {row.graph_id: row.input_digest for row in result.rows}
            observed = tuple(
                (id_, digest) for id_ in agenda_stimuli if (digest := digests.get(id_))
            )
        return AssembledPass(
            blocks, result.status, result.cause, shown,
            not isinstance(data, ProactiveInput) or data.intention in due_ids, observed,
            agenda_stimuli, review_due, seeds.review_next,
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
