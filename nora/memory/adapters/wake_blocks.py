"""Render the wake agenda and lesson evidence from the same scoped prefetch."""

from nora.domain.blocks import ContextBlock, render_elements
from nora.domain.composed import compose_body
from nora.domain.graph_stimuli import GraphStimulus
from nora.memory.adapters.presentation_blocks import GraphPresentationBlocks
from nora.memory.domain.pass_inputs import WakeInput, review_weakening
from nora.memory.domain.retrieval import EvidenceHandle, RetrievalResult


def wake_blocks(
    data: WakeInput, result: RetrievalResult, builder: GraphPresentationBlocks,
    anchors: tuple[str, ...],
) -> tuple[tuple[ContextBlock, ...], frozenset[str], tuple[str, ...]]:
    if result.status == "unavailable":
        return (), frozenset(), ()
    rows = {row.graph_id: row for row in result.rows if row.unavailable is None}
    stimuli = tuple(
        stimulus for stimulus in data.stimuli if stimulus.graph_id in rows
        and (stimulus.source != "lesson_tally"
             or rows[stimulus.graph_id].lesson_tally is not None)
    )
    weakenings = frozenset(s for s in stimuli if review_weakening(s) in result.review_weakenings)
    open_intentions = tuple(rows[id_] for id_ in anchors
                            if id_ in rows and rows[id_].kind == "intention"
                            and rows[id_].state == "open")

    def stimulus_rows(stimulus: GraphStimulus) -> str:
        text = builder.graph_rows((rows[stimulus.graph_id],))
        if stimulus in weakenings:
            text += f"\nReview grade {EvidenceHandle(stimulus.occurrence).handle} outcome=weakened"
        return text

    def stimulus_attrs(stimulus: GraphStimulus, _: int) -> tuple[tuple[str, str], ...]:
        return (
            ("kind", stimulus.kind), ("time", stimulus.eligible_at.isoformat()),
        )

    agenda = render_elements(
        stimuli, tag="item", attrs=stimulus_attrs, body=stimulus_rows,
    )
    if open_intentions:
        agenda += "\nOpen intentions touched by these changes:\n"
        agenda += builder.graph_rows(open_intentions)
    corrections = tuple(
        s for s in stimuli
        if s.kind == "strong_cue" and "addresses_nora" in s.cues
        and s.person not in (None, "nora") and rows[s.graph_id].kind == "item"
    )
    verdicts = tuple(
        s for s in stimuli
        if s.kind == "verdict" and rows[s.graph_id].source == "intention_verdict"
    )
    evidence = render_elements(
        (*verdicts, *corrections), tag="item", attrs=stimulus_attrs, body=stimulus_rows,
    )
    claimed_verdict_ids = {s.graph_id for s in verdicts}
    previous = tuple(rows[id_] for id_ in anchors
                     if id_ in rows and rows[id_].source == "intention_verdict"
                     and id_ not in claimed_verdict_ids)
    if previous:
        evidence += "\nEarlier verdicts in the same classes:\n" + builder.graph_rows(previous)
    blocks = (
        ContextBlock(
            name="agenda", body=compose_body(agenda), when_empty="self_close",
        ),
        ContextBlock(
            name="lesson_evidence", body=compose_body(evidence), when_empty="self_close",
        ),
    )
    shown = {s.graph_id for s in stimuli} | {row.graph_id for row in open_intentions}
    shown.update(s.occurrence for s in stimuli if s in weakenings)
    shown.update(row.graph_id for row in previous)
    return blocks, frozenset(shown), tuple(s.graph_id for s in stimuli)
