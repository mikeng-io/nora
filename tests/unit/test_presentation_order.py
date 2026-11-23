"""The computed presentation order depends on graph facts."""

from dataclasses import replace
from datetime import timedelta
from random import Random

from nora.memory.domain.presentations import (
    EntityAnchor,
    intention_is_due,
    lessons,
    order_rows,
    profile,
    standing,
)
from nora.memory.domain.retrieval import (
    RetrievalBounds,
    RetrievalResult,
    RetrievedEdge,
    RetrievedSupport,
)
from tests.unit.test_graph_row_render import CLAIM, ITEM, NOW


def _table():
    older = NOW - timedelta(hours=2)
    newer = NOW - timedelta(hours=1)
    due = replace(
        ITEM, graph_id="due", distance=1, kind="intention", state="open",
        occurred_at=older, close_when={"deadline": older.isoformat()},
    )
    return (
        replace(ITEM, graph_id="ended-near", distance=0, state="ended", occurred_at=older),
        due,
        replace(ITEM, graph_id="live-newer", distance=1, occurred_at=None, asserted_at=NOW),
        replace(ITEM, graph_id="live-a", distance=1, occurred_at=newer),
        replace(ITEM, graph_id="live-b", distance=1, occurred_at=newer),
        replace(ITEM, graph_id="live-older", distance=1, occurred_at=older, asserted_at=NOW),
        replace(due, graph_id="open", close_when={}, asserted_at=older, occurred_at=None),
        replace(CLAIM, graph_id="retired", distance=1, state="retired", asserted_at=NOW),
        replace(CLAIM, graph_id="superseded", distance=1, state="superseded", asserted_at=newer),
        replace(ITEM, graph_id="tombstoned", distance=1, state="tombstoned", occurred_at=older),
    )


def test_rows_order_by_distance_state_event_time_and_id() -> None:
    expected = _table()
    rows = (*expected[4:], *expected[:4])
    due = {row.graph_id for row in rows if intention_is_due(row, NOW)}
    assert order_rows(rows, due=due) == expected


def test_shuffled_rows_order_the_same_way() -> None:
    expected = _table()
    rows = list(expected)
    due = {row.graph_id for row in rows if intention_is_due(row, NOW)}
    for seed in range(3):
        Random(seed).shuffle(rows)
        assert order_rows(rows, due=due) == expected


def test_a_higher_search_score_cannot_outrank_a_nearer_row() -> None:
    near = replace(ITEM, graph_id="near", distance=1)
    far = replace(ITEM, graph_id="far", distance=2)
    ranked = sorted(((0.2, near), (0.9, far)), key=lambda match: match[0], reverse=True)
    assert order_rows(row for _, row in ranked) == (near, far)


def test_lessons_order_by_support_then_recency_with_unknown_last() -> None:
    earlier = NOW - timedelta(hours=1)
    unknown = replace(CLAIM, graph_id="unknown", part="lesson", support=None)
    old = replace(
        unknown, graph_id="old", asserted_at=earlier, support=RetrievedSupport(2, earlier),
    )
    new = replace(old, graph_id="new", asserted_at=NOW)
    stronger = replace(old, graph_id="stronger", support=RetrievedSupport(4, earlier))
    result = RetrievalResult(
        "ok", RetrievalBounds(1, 4, 10, earlier, NOW), (unknown, old, new, stronger),
    )
    assert lessons(result) == (stronger, new, old, unknown)


def test_presentations_leave_grade_claims_out_of_profile_standing_and_lessons() -> None:
    def claim(part):
        return replace(CLAIM, graph_id=part, part=part, origin_room="room",
                       edges=(RetrievedEdge("about", part, "entity"),))

    guess, lesson, situation, grade = map(claim, ("guess", "lesson", "situation", "grade"))
    result = RetrievalResult(
        "ok", RetrievalBounds(1, 4, 10, NOW - timedelta(hours=1), NOW),
        (guess, lesson, situation, grade),
    )
    assert tuple(row for _, rows in profile(result, EntityAnchor("entity", None), {}).parts
                 for row in rows) == (guess,)
    assert tuple(entry.row for entry in standing(result, "room", "entity", NOW).situation) == (
        situation,
    )
    assert lessons(result) == (lesson,)
