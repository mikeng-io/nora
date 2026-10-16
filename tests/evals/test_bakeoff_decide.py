"""The bake-off's decision rule, as a table.

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


def _arm(text_image: float | None, cantonese: float, names: float, errors: int = 0,
         misses: tuple[int | None, int | None, int | None] = (None, None, None)) -> dict:
    return {
        "text_image": Score(text_image, 0.5, errors, misses[0]),
        "cantonese": Score(cantonese, 0.5, 0, misses[1]),
        "names": Score(names, 0.5, 0, misses[2]),
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


def test_several_passing_the_fewest_total_misses_wins_not_the_best_images() -> None:
    """Among passing arms, the fewest misses summed over text_image, cantonese and
    names; better images alone do not decide."""
    d = _decide(**{
        "dense:wemm@2048": _arm(1.00, 0.76, 0.70, misses=(0, 16, 5)),
        "dense:qwen3vl@2048": _arm(0.96, 0.88, 0.70, misses=(1, 7, 5)),
    })
    assert d.verdict is Verdict.REPLACE
    assert d.winner == "dense:qwen3vl@2048"
    assert d.misses == {"dense:wemm@2048": 21, "dense:qwen3vl@2048": 13}


def test_an_exact_tie_on_total_misses_is_broken_by_the_lower_dimension() -> None:
    d = _decide(**{
        "fused:bm25+gemma2@768": _arm(0.96, 0.80, 0.70, misses=(2, 9, 6)),
        "fused:bm25+gemma2@256": _arm(0.95, 0.79, 0.70, misses=(3, 9, 5)),
        "fused:bm25+wemm@2048": _arm(0.97, 0.80, 0.70, misses=(1, 10, 6)),
    })
    assert d.verdict is Verdict.REPLACE
    assert d.misses == {"fused:bm25+gemma2@768": 17, "fused:bm25+gemma2@256": 17,
                        "fused:bm25+wemm@2048": 17}
    assert d.winner == "fused:bm25+gemma2@256"


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_a_passing_arm_without_per_query_ranks_decides_error_never_a_guess(missing) -> None:
    misses: list[int | None] = [0, 7, 5]
    misses[missing] = None
    d = _decide(**{
        "dense:wemm@2048": _arm(1.00, 0.76, 0.70, misses=(0, 16, 5)),
        "dense:qwen3vl@2048": _arm(0.96, 0.88, 0.70, misses=tuple(misses)),  # type: ignore[arg-type]
    })
    assert d.verdict is Verdict.ERROR
    assert "dense:qwen3vl@2048" in d.reason and "per_query" in d.reason


def test_one_passing_arm_needs_no_miss_count() -> None:
    d = _decide(**{"dense:wemm@2048": _arm(1.00, 0.80, 0.70)})
    assert (d.verdict, d.winner) == (Verdict.REPLACE, "dense:wemm@2048")


def test_the_loader_counts_misses_from_per_query_ranks(tmp_path: Path) -> None:
    """A miss is a query whose first relevant item is not in the top 5: rank
    over 5, none in the top 50, or an errored query."""
    f = tmp_path / "r.json"
    f.write_text(
        '{"slice": "names", "subsets": {"time_only": ["q3", "q4"]},'
        ' "reading_gap": {"queries": ["q1", "q4"]}, "arms": {"dense:x@256": {'
        '"recall5": 0.5, "mrr10": 0.4, "errors": {"queries": 0, "pool": 0},'
        '"in_reading_gap": {"recall5": 0.5, "mrr10": 0.5},'
        '"outside_reading_gap": {"recall5": 0.5, "mrr10": 0.5},'
        '"outside_time_only": {"recall5": 0.5, "mrr10": 0.5},'
        '"time_only": {"recall5": 0.5, "mrr10": 0.5},'
        '"per_query": {"q1": 1, "q2": 5, "q3": 6, "q4": null, "q5": "ERROR:timeout"}}}}',
        encoding="utf-8")
    assert scores_from_results([f])["dense:x@256"]["names"].misses == 3
    assert scores_from_results([f], subset="time_only")["dense:x@256"]["names"].misses == 2
    assert scores_from_results([f], subset="in_reading_gap")["dense:x@256"]["names"].misses == 1
    assert scores_from_results([f], subset="outside_reading_gap")[
        "dense:x@256"]["names"].misses == 2
    assert scores_from_results([f], subset="outside_time_only")[
        "dense:x@256"]["names"].misses == 1


def test_a_subset_whose_queries_are_not_listed_has_unknown_misses(tmp_path: Path) -> None:
    f = tmp_path / "r.json"
    f.write_text(
        '{"slice": "names", "arms": {"dense:x@256": {"recall5": 0.5, "mrr10": 0.4,'
        ' "errors": {"queries": 0, "pool": 0}, "odd": {"recall5": 0.5, "mrr10": 0.5},'
        ' "per_query": {"q1": 9}}}}', encoding="utf-8")
    assert scores_from_results([f], subset="odd")["dense:x@256"]["names"].misses is None


def test_the_five_way_comparison_picks_qwen_by_fewest_total_misses() -> None:
    """The committed files: Qwen@2048 misses 12 queries (text_image 0,
    cantonese 7, names 5), WeMM@2048 21 (0, 16, 5), Qwen@256 17; WeMM would win
    on text_image recall alone (1.000 against 0.9855)."""
    g = lambda pat: sorted(RESULT.parent.glob(pat))  # noqa: E731
    files = (g("20261015T105051Z-stage2-*.json") + g("20261015T125057Z-stage2-wemm-*.json")
             + g("20261015T165508Z-stage2-qwen3vl-*.json")
             + g("20261015T145806Z-stage2-jina5omni-*.json"))
    d = decide(scores_from_results(files))
    assert (d.verdict, d.winner) == (Verdict.REPLACE, "dense:qwen3vl@2048")
    assert d.misses == {"dense:qwen3vl@2048": 12, "dense:qwen3vl@256": 17,
                        "dense:wemm@2048": 21}
    fp32 = (g("20261015T105051Z-stage2-*.json") + g("20261015T123946Z-stage2-wemm-fp32-*.json")
            + g("20261015T125057Z-stage2-wemm-text_image.json")
            + g("20261015T165508Z-stage2-qwen3vl-*.json")
            + g("20261015T145806Z-stage2-jina5omni-*.json"))
    d = decide(scores_from_results(fp32, rename={"wemm-fp32": "wemm"}))
    assert (d.verdict, d.winner) == (Verdict.REPLACE, "dense:qwen3vl@2048")


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


def test_slice_wins_applies_the_same_bar_to_one_slice() -> None:
    from evals.retrieval_bakeoff.decide import slice_wins

    r = {**INCUMBENT, "dense:gemma2@768": _arm(0.95, 0.5, 0.5),
         "dense:gemma2@256": _arm(0.94, 0.5, 0.5)}
    base, wins = slice_wins(r, "text_image")
    assert base == 0.90
    assert wins == {"dense:gemma2@256": False, "dense:gemma2@768": True}
    with pytest.raises(ValueError, match="ERROR"):
        slice_wins({**r, "dense:gemma2@768": _arm(0.95, 0.5, 0.5, errors=1)}, "text_image")


# --- tie-breaks, order independence and the floor --------------------------------------


def test_the_dimension_step_breaks_a_miss_tie_before_the_name() -> None:
    """By name alone `dense:a@768` would win; the lower dimension must."""
    d = _decide(**{
        "dense:a@768": _arm(0.96, 0.80, 0.70, misses=(1, 9, 5)),
        "dense:z@256": _arm(0.96, 0.80, 0.70, misses=(1, 9, 5)),
    })
    assert d.winner == "dense:z@256"


def test_the_name_step_breaks_a_tie_on_misses_and_dimension_ascending() -> None:
    d = _decide(**{
        "dense:b@256": _arm(0.96, 0.80, 0.70, misses=(1, 9, 5)),
        "dense:a@256": _arm(0.96, 0.80, 0.70, misses=(1, 9, 5)),
    })
    assert d.winner == "dense:a@256"


def _result(tmp_path: Path, name: str, slice_: str, arms: dict) -> Path:
    import json

    f = tmp_path / name
    f.write_text(json.dumps({"slice": slice_, "arms": arms}), encoding="utf-8")
    return f


def _a(recall: float, provenance: dict | None = None) -> dict:
    return {"recall5": recall, "mrr10": 0.5, "errors": {"queries": 0, "pool": 0},
            **({"provenance": provenance} if provenance else {})}


def test_a_candidate_arm_measured_twice_on_a_slice_is_refused_in_either_order(
    tmp_path: Path,
) -> None:
    """The verdict must not depend on which file is read last."""
    one = _result(tmp_path, "1.json", "names", {"dense:x@256": _a(0.9)})
    two = _result(tmp_path, "2.json", "names", {"dense:x@256": _a(0.5)})
    for order in ([one, two], [two, one]):
        with pytest.raises(ValueError, match=r"ERROR.*dense:x@256.*names"):
            scores_from_results(order)


def test_incumbent_and_lexical_arms_may_recur_across_runs(tmp_path: Path) -> None:
    """Every run re-measures the floor; those are not candidates."""
    arms = {"dense:bge-m3": _a(0.9), "lexical:bm25": _a(0.5)}
    r = scores_from_results([_result(tmp_path, "1.json", "names", arms),
                             _result(tmp_path, "2.json", "names", arms)])
    assert r["dense:bge-m3"]["names"].recall5 == 0.9


def test_an_arm_recorded_in_the_qwen_default_instruction_format_loads_as_the_variant(
    tmp_path: Path,
) -> None:
    from evals.retrieval_bakeoff.adapters import (
        QWEN3VL_DEFAULT_INSTRUCTION_VARIANT,
        QWEN3VL_EMBEDDING_2B,
    )

    variant = {"format": {**QWEN3VL_DEFAULT_INSTRUCTION_VARIANT.record(), "label": "qwen3vl"}}
    scored = {"format": QWEN3VL_EMBEDDING_2B.record()}
    v = _result(tmp_path, "v.json", "names", {"dense:qwen3vl@2048": _a(0.8, variant),
                                              "fused:bm25+qwen3vl@2048": _a(0.7)})
    s = _result(tmp_path, "s.json", "names", {"dense:qwen3vl@2048": _a(0.9, scored)})
    for order in ([v, s], [s, v]):
        r = scores_from_results(order)
        assert r["dense:qwen3vl@2048"]["names"].recall5 == 0.9
        assert r["dense:qwen3vl-defaultinst@2048"]["names"].recall5 == 0.8
        assert r["fused:bm25+qwen3vl-defaultinst@2048"]["names"].recall5 == 0.7


def test_the_committed_qwen_variant_and_scored_runs_load_together_in_either_order() -> None:
    g = lambda pat: sorted(RESULT.parent.glob(pat))  # noqa: E731
    v, q = g("20261015T135707Z-stage2-qwen3vl-*.json"), g("20261015T165508Z-stage2-qwen3vl-*.json")
    verdicts = set()
    for order in (v + q, q + v):
        r = scores_from_results(order)
        assert r["dense:qwen3vl@2048"]["text_image"].recall5 == 0.9855
        assert r["dense:qwen3vl-defaultinst@2048"]["text_image"].recall5 == 0.9493
        d = decide(r)
        verdicts.add((d.verdict, d.winner))
    assert verdicts == {(Verdict.REPLACE, "dense:qwen3vl@2048")}
