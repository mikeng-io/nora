"""Render the wake agenda and lesson evidence from the same scoped prefetch."""

from nora.domain.blocks import ContextBlock, render_elements
from nora.domain.composed import compose_body
from nora.domain.graph_stimuli import GraphStimulus
from nora.memory.adapters.presentation_blocks import GraphPresentationBlocks
from nora.memory.domain.pass_inputs import WakeInput
from nora.memory.domain.retrieval import RetrievalResult


def wake_blocks(
    data: WakeInput, result: RetrievalResult, builder: GraphPresentationBlocks,
    anchors: tuple[str, ...],
) -> tuple[tuple[ContextBlock, ...], frozenset[str]]:
    if result.status == "unavailable":
        blocks = tuple(
            ContextBlock(name, compose_body(""), attrs=(("status", "unavailable"),),
                         when_empty="self_close")
            for name in ("agenda", "lesson_corrections")
        )
        return blocks, frozenset()
    rows = {row.graph_id: row for row in result.rows if row.unavailable is None}
    stimuli = tuple(stimulus for stimulus in data.stimuli if stimulus.graph_id in rows)
    open_intentions = tuple(rows[id_] for id_ in anchors
                            if id_ in rows and rows[id_].kind == "intention"
                            and rows[id_].state == "open")

    def stimulus_rows(stimulus: GraphStimulus) -> str:
        return builder.graph_rows((rows[stimulus.graph_id],))

    agenda = render_elements(
        stimuli, tag="item",
        attrs=lambda stimulus, _: (
            ("kind", stimulus.kind), ("time", stimulus.eligible_at.isoformat()),
        ),
        body=stimulus_rows,
    )
    if open_intentions:
        agenda += "\nOpen intentions touched by these changes:\n"
        agenda += builder.graph_rows(open_intentions)
    corrections = tuple(
        s for s in stimuli
        if s.kind == "strong_cue" and "addresses_nora" in s.cues
        and s.person not in (None, "nora") and rows[s.graph_id].kind == "item"
    )
    verdict_ids = tuple(s.graph_id for s in data.stimuli if s.kind == "verdict")
    # PENDING-RESTACK: join verdict_ids to 5a's scoped verdict rows before rendering.
    evidence_state = (("status", "partial"),) if verdict_ids else ()
    blocks = (
        ContextBlock(
            name="agenda", body=compose_body(agenda), when_empty="self_close",
        ),
        ContextBlock(
            name="lesson_corrections", body=compose_body(builder.graph_rows(
                tuple(rows[s.graph_id] for s in corrections),
            )), attrs=evidence_state, when_empty="self_close",
        ),
    )
    shown = {s.graph_id for s in stimuli} | {row.graph_id for row in open_intentions}
    return blocks, frozenset(shown)
