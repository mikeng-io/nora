"""The bake-off's decision rule, as a table (design/unified-memory/20 «Pass»).

A candidate replaces bge-m3 only if its fused recall@5 holds the Cantonese
slice within 0.05 AND beats bge-m3 by 0.10 on another journey slice. Inside
the margins is a tie, and a tie keeps the incumbent. A candidate that wins
images but loses Cantonese is flagged media-only for the operator. Any ERROR,
or a journey slice with no result, decides nothing.
"""

from __future__ import annotations

import pytest

from evals.retrieval_bakeoff.decide import (
    BASELINE,
    Decision,
    Score,
    Verdict,
    decide,
    rerank_ships,
)


def _arm(text_image: float, cantonese: float, names: float, errors: int = 0) -> dict[str, Score]:
    return {
        "text_image": Score(text_image, 0.5, errors),
        "cantonese": Score(cantonese, 0.5, 0),
        "names": Score(names, 0.5, 0),
    }


BASE = _arm(0.60, 0.80, 0.70)


def _decide(**arms: dict[str, Score]) -> Decision:
    return decide({BASELINE: BASE, **arms})


def test_a_tie_keeps_the_incumbent() -> None:
    d = _decide(**{"fused:gemma2@768": _arm(0.69, 0.80, 0.75)})
    assert d.verdict is Verdict.KEEP_BGE_M3
    assert d.winner is None
    assert d.media_only == ()


def test_a_win_on_images_that_holds_cantonese_replaces() -> None:
    d = _decide(**{"fused:gemma2@768": _arm(0.70, 0.75, 0.70)})  # exactly on both margins
    assert d.verdict is Verdict.REPLACE
    assert d.winner == "fused:gemma2@768"
    assert d.wins == ("text_image",)


def test_a_win_on_images_that_loses_cantonese_is_media_only_and_keeps_the_incumbent() -> None:
    d = _decide(**{"fused:wemm@2048": _arm(0.90, 0.74, 0.70)})
    assert d.verdict is Verdict.KEEP_BGE_M3
    assert d.media_only == ("fused:wemm@2048",)


def test_any_error_on_a_journey_slice_decides_error() -> None:
    d = _decide(**{"fused:gemma2@768": _arm(0.95, 0.90, 0.90, errors=1)})
    assert d.verdict is Verdict.ERROR


def test_an_error_on_the_baseline_decides_error() -> None:
    d = decide({BASELINE: _arm(0.6, 0.8, 0.7, errors=2), "fused:gemma2@768": _arm(0.9, 0.8, 0.7)})
    assert d.verdict is Verdict.ERROR


def test_a_missing_journey_slice_decides_error_not_a_pass() -> None:
    partial = {"text_image": Score(0.95, 0.9, 0)}
    d = decide({BASELINE: BASE, "fused:gemma2@768": partial})
    assert d.verdict is Verdict.ERROR


def test_no_baseline_decides_error() -> None:
    assert decide({"fused:gemma2@768": _arm(0.9, 0.8, 0.8)}).verdict is Verdict.ERROR


def test_two_winners_tie_on_images_and_the_lower_dimension_wins() -> None:
    d = _decide(**{
        "fused:gemma2@768": _arm(0.80, 0.80, 0.70),
        "fused:gemma2@256": _arm(0.80, 0.79, 0.70),
        "fused:wemm@2048": _arm(0.75, 0.80, 0.70),
    })
    assert d.verdict is Verdict.REPLACE
    assert d.winner == "fused:gemma2@256"


@pytest.mark.parametrize(("lift", "p95", "caller", "ships"), [
    (0.05, 300, "prefetch", True),
    (0.04, 100, "prefetch", False),
    (0.05, 301, "prefetch", False),
    (0.10, 2000, "cognition", True),
])
def test_a_reranker_ships_on_lift_and_latency(
    lift: float, p95: float, caller: str, ships: bool,
) -> None:
    fused = {s: Score(0.5, 0.5, 0) for s in ("text_image", "cantonese", "names")}
    reranked = dict(fused, names=Score(0.5 + lift, 0.5, 0))
    assert rerank_ships(fused, reranked, p95, caller) is ships
