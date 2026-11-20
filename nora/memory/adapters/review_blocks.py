"""Render the review docket with its event and assertion times."""

from collections import defaultdict
from datetime import datetime

from nora.agent.egress import mask_coordinates
from nora.domain.blocks import ContextBlock, render_elements
from nora.domain.composed import compose_body
from nora.domain.prompt import escape
from nora.memory.adapters.presentation_blocks import GraphPresentationBlocks
from nora.memory.domain.retrieval import RetrievalResult, RetrievedRow


def review_block(
    result: RetrievalResult, builder: GraphPresentationBlocks,
    due: tuple[str, ...], evidence: tuple[tuple[str, str], ...],
) -> tuple[ContextBlock, frozenset[str], tuple[tuple[str, datetime], ...]]:
    row_map = {row.graph_id: row for row in result.rows if row.unavailable is None}
    tallies = {
        row.content["claim_id"]: row.asserted_at for row in row_map.values()
        if row.source == "lesson_tally" and isinstance(row.content, dict)
    }
    docket = tuple(
        (id_, tallies.get(id_, row_map[id_].asserted_at)) for id_ in due
        if id_ in row_map and (row_map[id_].part != "lesson" or id_ in tallies)
    )
    targets: dict[str, list[str]] = defaultdict(list)
    for ref, target in evidence:
        targets[ref].append(target)
    rows = tuple(row_map[id_] for id_ in dict.fromkeys((*due, *targets)) if id_ in row_map)

    def attrs(row: RetrievedRow, _: int) -> tuple[tuple[str, str | None], ...]:
        return (
            ("role", "due" if row.graph_id in due else "evidence"),
            ("asserted_at", row.asserted_at.isoformat()),
            ("event_time", (row.occurred_at or row.asserted_at).isoformat()),
            ("targets", ", ".join(targets[row.graph_id]) if row.graph_id in targets else None),
        )

    def body(row: RetrievedRow) -> str:
        text = builder.graph_rows((row,))
        prediction = row.close_when.get("prediction")
        if row.graph_id in due and isinstance(prediction, str):
            text += "\n" + escape(mask_coordinates(
                f"Prediction {prediction}\nWindow ends {row.close_when.get('window_end')}",
            ))
        return text

    text = render_elements(rows, tag="item", attrs=attrs, body=body)
    details = (("status", result.status),)
    if result.limits:
        details += (("limit", ", ".join(result.limits)),)
    if result.cause:
        details += (("cause", result.cause),)
    block = ContextBlock(
        name="review_docket", body=compose_body(text), attrs=details, when_empty="self_close",
    )
    return block, frozenset(row.graph_id for row in rows), docket
