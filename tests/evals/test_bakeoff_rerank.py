"""The two-stage arm: the top k by
cosine on the leading search dimensions, re-ranked by full cosine. Exact here;
the HNSW twin is tests/integration/test_bakeoff_hnsw.py. k under 5 is refused,
and a candidate set shorter than k while the pool is not is an ERROR."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from evals.retrieval_bakeoff import decide, hnsw, run
from evals.retrieval_bakeoff.decide import Score, Verdict
from evals.retrieval_bakeoff.run import Embedded, Errored, Rerank


def _unit(*xs: float) -> list[float]:
    n = math.sqrt(sum(x * x for x in xs))
    return [x / n for x in xs]


#: Query weighted on the third dimension; `x` is the full-cosine best and the
#: prefix-cosine worst, so a k that excludes it loses it.
QUERY = Embedded(vectors={"q": _unit(1, 0, 3)})
POOL = Embedded(vectors={
    "p1": _unit(1, 0, 0), "p2": _unit(1, 0.1, 0), "p3": _unit(1, 0.2, 0.5),
    "p4": _unit(1, 0.3, 1), "p5": _unit(1, 0.4, 0), "p6": _unit(1, 0.5, 0.2),
    "x": _unit(0.3, 1, 3),
})


def test_the_top_k_by_prefix_is_re_ranked_by_full_cosine():
    ranked, times = run.rank_rerank(QUERY, POOL, search_dim=2, k=5)
    # Stage 1 keeps p1..p5 (prefix cosine 1 down to .93); x (.29) and p6 (.89) are out.
    assert ranked["q"] == ["p4", "p3", "p1", "p2", "p5"]
    assert len(times["stage1"]) == len(times["stage2"]) == 1


def test_a_k_that_holds_the_pool_is_dense_at_the_full_dimension():
    ranked, _ = run.rank_rerank(QUERY, POOL, search_dim=2, k=7)
    assert ranked["q"] == run.rank_dense(QUERY, POOL)["q"]
    assert ranked["q"][0] == "x"


def test_an_errored_query_stays_an_error():
    qs = Embedded(vectors=dict(QUERY.vectors), errors={"bad": "timeout"})
    ranked, _ = run.rank_rerank(qs, POOL, search_dim=2, k=5)
    assert ranked["bad"] == Errored("timeout")


@pytest.mark.parametrize("make", [
    lambda: run.rank_rerank(QUERY, POOL, search_dim=2, k=4),
    lambda: Rerank(256, 2048, ks=(4,)),
    lambda: Rerank(256, 2048, hnsw_ks=(4,), dsn="postgresql+asyncpg://h/bakeoff"),
], ids=["rank", "exact-arm", "hnsw-arm"])
def test_k_under_five_is_refused(make):
    with pytest.raises(ValueError, match="under 5"):
        make()


def test_an_hnsw_arm_needs_a_dsn_and_ef_search_at_least_k():
    with pytest.raises(ValueError, match="--dsn"):
        Rerank(256, 2048, hnsw_ks=(50,))
    with pytest.raises(ValueError, match="ef_search"):
        Rerank(256, 2048, hnsw_ks=(50,), dsn="postgresql+asyncpg://h/bakeoff", ef_search=40)


@pytest.mark.parametrize(("n", "k", "pool", "short"), [
    (50, 50, 400, False), (49, 50, 400, True), (30, 50, 30, False), (29, 50, 30, True),
])
def test_a_candidate_set_short_of_k_is_short_only_when_the_pool_is_not(n, k, pool, short):
    assert hnsw.short_candidates(n, k, pool) is short


def test_a_short_candidate_set_is_an_error_in_the_exact_arm(monkeypatch):
    monkeypatch.setattr(run, "_cosines", lambda q, pool, ids: [(0.0, i) for i in sorted(ids)][:3])
    ranked, _ = run.rank_rerank(QUERY, POOL, search_dim=2, k=5)
    assert ranked["q"] == Errored("short_candidates")


def test_the_arms_are_named_and_scored_through_the_harness():
    rr = Rerank(2, 3, ks=(5, 7))
    arms = run._rerank_arms("m", rr, POOL, QUERY, {}, {}, "the text", "s")
    assert set(arms) == {"rerank:m@2>3/k5", "rerank:m@2>3/k7"}
    assert arms["rerank:m@2>3/k5"].rankings["q"][0] == "p4"
    assert arms["rerank:m@2>3/k5"].cost["stage1_search_ms"]["n"] == 1
    assert arms["rerank:m@2>3/k5"].provenance["k"] == 5


def test_an_hnsw_arm_that_cannot_run_errors_every_query(monkeypatch):
    def down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(hnsw, "rerank", down)
    rr = Rerank(2, 3, hnsw_ks=(5,), dsn="postgresql+asyncpg://h/bakeoff")
    arm = run._rerank_arms("m", rr, POOL, QUERY, {}, {}, "the text", "s")["hnsw-rerank:m@2>3/k5"]
    assert arm.rankings == {"q": Errored("exception")}
    assert run.score(arm.rankings, {"q": frozenset({"x"})})["verdict"] == "ERROR"


def _arm(r: float, errors: int = 0, misses: int = 4) -> dict[str, Score]:
    return {s: Score(r, 0.5, errors, misses) for s in decide.JOURNEY}


INCUMBENT = {"dense:bge-m3": _arm(0.80), "fused:trigram+bge-m3": _arm(0.80),
             "fused:bm25+bge-m3": _arm(0.80)}
RESULTS = Path(__file__).resolve().parents[2] / "evals" / "retrieval_bakeoff" / "results"


def test_a_tie_on_misses_goes_to_the_two_stage_arm_by_its_indexed_dimension():
    """Fewest total misses first ; on a tie, the lower
    dimension, and a two-stage arm's is the one its index searches (256),
    not the one it re-ranks with (2048)."""
    d = decide.decide({**INCUMBENT, "dense:qwen3vl-q4@2048": _arm(0.95, misses=4),
                       "rerank:qwen3vl-q4@256>2048/k50": _arm(0.95, misses=4),
                       "dense:qwen3vl-q4@256": _arm(0.95, misses=5)})
    assert d.verdict is Verdict.REPLACE
    assert d.winner == "rerank:qwen3vl-q4@256>2048/k50"


def test_an_error_in_the_hnsw_arm_decides_error():
    d = decide.decide({**INCUMBENT, "hnsw-rerank:qwen3vl-q4@256>2048/k50": _arm(0.95, errors=1)})
    assert d.verdict is Verdict.ERROR


def test_the_committed_two_stage_run_re_derives_its_verdict():
    """20261015T171213Z: every two-stage arm misses 12 (text_image 0,
    cantonese 7, names 5), as dense@2048 does; dense@256 misses 13. The four
    two-stage arms tie on misses and on the indexed 256, so the name picks."""
    d = decide.decide(decide.scores_from_results(
        sorted(RESULTS.glob("20261015T171213Z-stage2-qwen3vl-q4-*.json"))))
    assert (d.verdict, d.winner) == (Verdict.REPLACE, "hnsw-rerank:qwen3vl-q4@256>2048/k50")
    two_stage = [a for a in d.misses if "rerank:" in a]
    assert len(two_stage) == 4 and {d.misses[a] for a in two_stage} == {12}
    assert d.misses["dense:qwen3vl-q4@2048"] == 12 and d.misses["dense:qwen3vl-q4@256"] == 13


def test_each_relevant_item_s_rank_is_recorded_so_a_lost_item_re_derives():
    ranked, _ = run.rank_rerank(QUERY, POOL, search_dim=2, k=5)
    dense = run.rank_dense(QUERY, POOL)
    labels = {"q": frozenset({"x", "p3"})}
    assert run.relevant_ranks(dense, labels) == {"q": {"p3": 3, "x": 1}}
    assert run.relevant_ranks(ranked, labels) == {"q": {"p3": 2, "x": None}}
    assert run.relevant_ranks({"q": Errored("timeout")}, labels) == {"q": "ERROR:timeout"}


def test_ties_go_to_the_lower_id_in_both_stages():
    """Stage 1 admits the lowest ids at the k boundary; stage 2 orders equal
    full cosines by id, not by their stage-1 order. Inserted in reverse."""
    tie = {i: _unit(1, 0, 0) for i in "gfedcba"}
    ranked, _ = run.rank_rerank(Embedded(vectors={"q": _unit(1, 0, 0)}), Embedded(vectors=tie),
                                search_dim=2, k=5)
    assert ranked["q"] == ["a", "b", "c", "d", "e"]
    # Full cosine 0.6 for all; prefix cosine falls c, b, a, e, d.
    full_tie = {i: [0.6, y, math.sqrt(1 - 0.36 - y * y)]
                for i, y in {"c": 0.0, "b": 0.3, "a": 0.5, "e": 0.6, "d": 0.7}.items()}
    ranked, _ = run.rank_rerank(Embedded(vectors={"q": [1.0, 0.0, 0.0]}),
                                Embedded(vectors=full_tie), search_dim=2, k=5)
    assert ranked["q"] == ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize(("ef_search", "want"), [(0, 5), (80, 80)])
def test_the_hnsw_arm_asks_for_ef_search_at_least_k(monkeypatch, ef_search, want):
    seen = {}

    def fake(dsn, pool, queries, **kw):
        seen.update(kw)
        return {q: list(pool)[:5] for q in queries}, {"stage1": [1.0], "two_stage": [2.0]}, {}

    monkeypatch.setattr(hnsw, "rerank", fake)
    rr = Rerank(2, 3, hnsw_ks=(5,), dsn="postgresql+asyncpg://h/bakeoff", ef_search=ef_search)
    run._rerank_arms("m", rr, POOL, QUERY, {}, {}, "the text", "s")
    assert seen["ef_search"] == want and seen["k"] == 5


class _Dummy:
    name = "dummy"


Q, ONE = "http://127.0.0.1:1", "one candidate per run"


@pytest.mark.parametrize(("argv", "match"), [
    (["--qwen", Q, "--gemma", "embeddinggemma-2"], f"{ONE}: --qwen"),
    (["--qwen", Q, "--wemm", "http://127.0.0.1:2"], f"{ONE}: --qwen"),
    (["--qwen", Q, "--official", "qwen3vl"], f"{ONE}: --official"),
    (["--gemma", "embeddinggemma-2", "--official", "qwen3vl"], f"{ONE}: --official"),
    (["--rerank", "256>2048"], "--rerank needs a 2048-dimension candidate"),
    (["--gemma", "embeddinggemma-2", "--rerank", "256>2048"], "--rerank needs"),
], ids=["qwen+gemma", "qwen+wemm", "official+qwen", "official+gemma", "rerank-alone",
        "rerank+gemma"])
def test_main_refuses_runs_it_cannot_measure_fairly(monkeypatch, tmp_path, argv, match):
    monkeypatch.setattr(run, "qwen_embedder", lambda **kw: _Dummy() if kw["llama_url"] else None)
    monkeypatch.setattr(run, "official_embedder", lambda label, dtype: _Dummy() if label else None)
    monkeypatch.setattr(run, "host_sample", lambda: pytest.fail("refusal came after the run began"))
    with pytest.raises(ValueError, match=match):
        run.main(["--dsn", "postgresql+asyncpg://x/bakeoff", "--out", str(tmp_path), *argv])
