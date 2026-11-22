"""The pass catalogue declares a bounded prefetch, presentations and pull."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast, get_args

import pytest

from nora.memory.domain import pass_declarations
from nora.memory.domain.pass_declarations import PassName, declaration
from nora.memory.domain.retrieval import RetrievalBounds

NOW = datetime(2026, 11, 2, tzinfo=UTC)
ALL = ("graph_profile", "graph_standing", "graph_lessons", "related")


def test_the_passes_declare_their_presentations_bounds_and_pull() -> None:
    cases = {
        "called": (ALL, ("read",), 2, 8, 40, 180),
        "ambient": (ALL, ("read",), 2, 8, 40, 180),
        "proactive": (ALL, ("read",), 2, 8, 40, 180),
        "debrief": (("graph_standing",), (), 1, 8, 30, 30),
        "wake": (ALL, ("read",), 3, 10, 80, 365),
        "wake_history": (ALL, ("read",), 3, 10, 80, 365),
        "review": ((), ("read",), 1, 10, 60, 365),
    }
    assert set(get_args(PassName)) == cases.keys()
    for name, (presentations, pull, depth, fanout, rows, days) in cases.items():
        choice = declaration(cast(PassName, name))
        assert (choice.presentations, choice.pull) == (presentations, pull), name
        assert choice.bounds(NOW) == RetrievalBounds(
            depth, fanout, rows, NOW - timedelta(days=days), NOW,
        ), name


def test_bounds_are_positive_and_provisional() -> None:
    assert "# Provisional bounds, measured in phase 8." in Path(
        pass_declarations.__file__,
    ).read_text()
    for name in get_args(PassName):
        bound = declaration(name).bounds(NOW)
        assert min(bound.depth, bound.fan_out, bound.rows) > 0
        assert bound.since < bound.until


def test_an_unknown_pass_is_refused() -> None:
    assert declaration("called").pull == ("read",)
    with pytest.raises(KeyError, match="seventh"):
        declaration(cast(PassName, "seventh"))
