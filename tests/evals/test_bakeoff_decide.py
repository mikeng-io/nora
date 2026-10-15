"""The bake-off's decision rule, as a table (design/unified-memory/20 «Pass»,
operator ruling 2026-10-15).

The baseline is the best incumbent configuration per slice: the highest
recall@5 of bge-m3 alone, trigram+bge-m3 and bm25+bge-m3. A candidate WINS a
slice (text_image or names) when its miss rate is at most half the
baseline's, and must HOLD cantonese within 0.05 recall@5. Anything less is a
tie, and a tie keeps bge-m3. A candidate that wins images but loses Cantonese
is flagged media-only for the operator. Any ERROR, a missing journey slice
or a missing or non-finite recall decides ERROR.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from evals.retrieval_bakeoff.decide import (
    INCUMBENTS,
    Decision,
    Score,
    Verdict,
    decide,
    rerank_ships,
    scores_from_results,
)

RESULT = (Path(__file__).resolve().parents[2] / "evals" / "retrieval_bakeoff" / "results"
          / "20261015T033750Z-floor-text_image.json")


def _arm(text_image: float | None, cantonese: float, names: float, errors: int = 0) -> dict:
    return {
        "text_image": Score(text_image, 0.5, errors),
        "cantonese": Score(cantonese, 0.5, 0),
        "names": Score(names, 0.5, 0),
    }


#: bge-m3 alone is best on text_image (0.90), a fused incumbent on the others.
INCUMBENT = {
    "dense:bge-m3": _arm(0.90, 0.78, 0.60),
    "fused:trigram+bge-m3": _arm(0.88, 0.80, 0.70),
    "fused:bm25+bge-m3": _arm(0.86, 0.79, 0.68),
}


def _decide(**candidates: dict) -> Decision:
    return decide({**INCUMBENT, **candidates})


def test_the_baseline_is_the_best_incumbent_per_slice() -> None:
    # names: the best incumbent is trigram+bge-m3 (0.70, miss 0.30). 0.84
    # (miss 0.16) does not halve it, though it would halve bge-m3 alone's 0.40.
    d = _decide(**{"fused:bm25+gemma2@768": _arm(0.94, 0.80, 0.84)})
    assert d.verdict is Verdict.KEEP_BGE_M3
    assert d.baseline == {"text_image": 0.90, "cantonese": 0.80, "names": 0.70}


def test_halving_the_miss_rate_on_images_while_holding_cantonese_replaces() -> None:
    d = _decide(**{"fused:bm25+gemma2@768": _arm(0.95, 0.75, 0.70)})  # exactly on both edges
    assert d.verdict is Verdict.REPLACE
    assert d.winner == "fused:bm25+gemma2@768"
    assert d.wins == ("text_image",)


def test_halving_the_miss_rate_on_names_also_wins() -> None:
    d = _decide(**{"fused:bm25+gemma2@768": _arm(0.90, 0.80, 0.85)})
    assert d.verdict is Verdict.REPLACE
    assert d.wins == ("names",)


def test_less_than_half_the_miss_rate_is_a_tie_and_keeps_the_incumbent() -> None:
    d = _decide(**{"fused:bm25+gemma2@768": _arm(0.94, 0.80, 0.84)})
    assert d.verdict is Verdict.KEEP_BGE_M3
    assert d.winner is None
    assert d.media_only == ()


def test_a_perfect_baseline_cannot_be_beaten_so_a_perfect_candidate_ties() -> None:
    perfect = {name: _arm(1.0, 0.8, 0.7) for name in INCUMBENTS}
    d = decide({**perfect, "fused:bm25+gemma2@768": _arm(1.0, 0.8, 0.7)})
    assert d.verdict is Verdict.KEEP_BGE_M3


def test_a_win_on_images_that_loses_cantonese_is_media_only_and_keeps_the_incumbent() -> None:
    d = _decide(**{"fused:bm25+wemm@2048": _arm(0.99, 0.74, 0.70)})
    assert d.verdict is Verdict.KEEP_BGE_M3
    assert d.media_only == ("fused:bm25+wemm@2048",)


def test_any_error_on_a_journey_slice_decides_error() -> None:
    d = _decide(**{"fused:bm25+gemma2@768": _arm(0.99, 0.90, 0.90, errors=1)})
    assert d.verdict is Verdict.ERROR


def test_an_error_on_an_incumbent_decides_error() -> None:
    bad = dict(INCUMBENT, **{"fused:bm25+bge-m3": _arm(0.86, 0.79, 0.68, errors=2)})
    assert decide({**bad, "fused:bm25+gemma2@768": _arm(0.99, 0.8, 0.7)}).verdict is Verdict.ERROR


@pytest.mark.parametrize("bad", [None, math.nan])
def test_a_missing_or_non_finite_recall_decides_error(bad: float | None) -> None:
    assert _decide(**{"fused:bm25+gemma2@768": _arm(bad, 0.8, 0.7)}).verdict is Verdict.ERROR


def test_a_missing_journey_slice_decides_error_not_a_pass() -> None:
    partial = {"text_image": Score(0.99, 0.9, 0)}
    assert _decide(**{"fused:bm25+gemma2@768": partial}).verdict is Verdict.ERROR


def test_a_missing_incumbent_decides_error() -> None:
    without = {k: v for k, v in INCUMBENT.items() if k != "dense:bge-m3"}
    d = decide({**without, "fused:bm25+gemma2@768": _arm(0.99, 0.8, 0.7)})
    assert d.verdict is Verdict.ERROR


def test_two_winners_tie_on_images_and_the_lower_dimension_wins() -> None:
    d = _decide(**{
        "fused:bm25+gemma2@768": _arm(0.96, 0.80, 0.70),
        "fused:bm25+gemma2@256": _arm(0.96, 0.79, 0.70),
        "fused:bm25+wemm@2048": _arm(0.95, 0.80, 0.70),
    })
    assert d.verdict is Verdict.REPLACE
    assert d.winner == "fused:bm25+gemma2@256"


def test_the_committed_result_loads_into_decide_with_its_own_arm_names() -> None:
    r = scores_from_results([RESULT])
    assert set(r) == {
        "lexical:trigram", "lexical:bm25", "dense:bge-m3",
        "fused:trigram+bge-m3", "fused:bm25+bge-m3",
    }
    assert set(INCUMBENTS) <= set(r)
    assert r["dense:bge-m3"]["text_image"] == Score(0.9275, 0.7772, 0)
    gap = scores_from_results([RESULT], subset="in_reading_gap")
    assert gap["dense:bge-m3"]["text_image"].recall5 == pytest.approx(0.8462)
    # One slice measured: the rule cannot decide yet, and says so.
    assert decide(r).verdict is Verdict.ERROR


def test_the_loader_counts_query_and_pool_errors_together(tmp_path: Path) -> None:
    f = tmp_path / "r.json"
    f.write_text(
        '{"slice": "names", "arms": {"dense:bge-m3": {"recall5": 0.5, "mrr10": 0.4,'
        ' "errors": {"queries": 2, "pool": 3, "by_kind": {}}}}}', encoding="utf-8")
    assert scores_from_results([f])["dense:bge-m3"]["names"] == Score(0.5, 0.4, 5)


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
