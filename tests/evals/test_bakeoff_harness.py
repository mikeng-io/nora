"""The retrieval bake-off's harness measures fail-closed (design/unified-memory/20).

Every embedding failure is an ERROR for that item, never a miss: an ERROR
query is left out of the mean and counted, and any ERROR makes the arm's
verdict on the slice ERROR. The embedders here are fakes, so each failure is
produced on purpose; the metrics are checked against hand-computed values.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from pathlib import Path

import pytest

from evals.retrieval_bakeoff import adapters, run
from evals.retrieval_bakeoff.adapters import (
    GEMMA2_PREFIX,
    EmbedError,
    HFEmbedder,
    OllamaEmbedder,
    matryoshka,
)
from evals.retrieval_bakeoff.run import Errored

SET_DIR = Path(__file__).resolve().parents[2] / "evals" / "retrieval_bakeoff"


class FakeEmbedder:
    """Answers from a table: text -> vector, or a callable that misbehaves."""

    name = "fake"

    def __init__(self, table: dict[str, list[float]], native_dim: int = 4, *, sleep: float = 0,
                 raise_: Exception | None = None, drop_one: bool = False) -> None:
        self.table = table
        self.native_dim = native_dim
        self.sleep = sleep
        self.raise_ = raise_
        self.drop_one = drop_one
        self.media_calls: list[Path] = []

    def embed_text(self, texts, role):
        if self.sleep:
            time.sleep(self.sleep)
        if self.raise_ is not None:
            raise self.raise_
        out = [self.table[t] for t in texts]
        return out[:-1] if self.drop_one else out

    def embed_media(self, paths):
        self.media_calls.extend(paths)
        return [[1.0, 0.0, 0.0, 0.0] for _ in paths]

    def provenance(self):
        return {"model": "fake"}


# --- the fail-closed rule: every failure is an ERROR for its item ----------------


def _embed(fake, items, **kw):
    timeout = kw.pop("timeout", 5)
    return run.embed_texts(fake, items, "query", dim=fake.native_dim, timeout=timeout, **kw)


def test_a_nan_vector_is_an_error_for_that_item_only() -> None:
    fake = FakeEmbedder({"a": [1, 0, 0, 0], "b": [math.nan, 1, 0, 0]})
    got = _embed(fake, {"qa": "a", "qb": "b"})
    assert set(got.vectors) == {"qa"}
    assert got.errors == {"qb": "nonfinite"}


def test_a_wrong_dimension_is_an_error() -> None:
    fake = FakeEmbedder({"a": [1, 0, 0]})
    assert _embed(fake, {"qa": "a"}).errors == {"qa": "wrong_dimension"}


def test_a_zero_vector_is_an_error() -> None:
    fake = FakeEmbedder({"a": [0, 0, 0, 0]})
    assert _embed(fake, {"qa": "a"}).errors == {"qa": "zero_vector"}


def test_a_timeout_is_an_error_for_every_item_in_the_batch() -> None:
    fake = FakeEmbedder({"a": [1, 0, 0, 0], "b": [0, 1, 0, 0]}, sleep=0.5)
    got = _embed(fake, {"qa": "a", "qb": "b"}, timeout=0.05)
    assert got.vectors == {}
    assert got.errors == {"qa": "timeout", "qb": "timeout"}


def test_an_exception_is_an_error_for_every_item_in_the_batch() -> None:
    fake = FakeEmbedder({}, raise_=ConnectionError("refused"))
    assert _embed(fake, {"qa": "a"}).errors == {"qa": "exception"}


def test_a_short_answer_is_an_error_for_the_whole_batch() -> None:
    fake = FakeEmbedder({"a": [1, 0, 0, 0], "b": [0, 1, 0, 0]}, drop_one=True)
    got = _embed(fake, {"qa": "a", "qb": "b"})
    assert got.errors == {"qa": "count_mismatch", "qb": "count_mismatch"}


def test_a_media_file_whose_bytes_do_not_match_is_an_error_and_never_embedded(
    tmp_path: Path,
) -> None:
    good = tmp_path / "good.jpg"
    good.write_bytes(b"good")
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"tampered")
    fake = FakeEmbedder({})
    got = run.embed_media(fake, {
        "img-1": (good, hashlib.sha256(b"good").hexdigest()),
        "img-2": (bad, hashlib.sha256(b"original").hexdigest()),
        "img-3": (tmp_path / "missing.jpg", hashlib.sha256(b"x").hexdigest()),
    }, dim=4, timeout=5)
    assert set(got.vectors) == {"img-1"}
    assert got.errors == {"img-2": "hash_mismatch", "img-3": "hash_mismatch"}
    assert fake.media_calls == [good]


def test_an_error_query_is_left_out_of_the_mean_never_scored_as_a_miss() -> None:
    labels = {"q1": frozenset({"a"}), "q2": frozenset({"b"}), "q3": frozenset({"c"})}
    rankings = {"q1": ["a"], "q2": ["x", "b"], "q3": Errored("nonfinite")}
    s = run.score(rankings, labels)
    assert s["n_scored"] == 2
    assert s["errors"] == {"queries": 1, "pool": 0, "by_kind": {"nonfinite": 1}}
    assert s["recall5"] == pytest.approx(1.0)  # a miss would have made it 2/3
    assert s["mrr10"] == pytest.approx((1 + 0.5) / 2)
    assert s["verdict"] == "ERROR"


def test_a_pool_error_makes_the_verdict_error_even_when_every_query_scored() -> None:
    s = run.score({"q1": ["a"]}, {"q1": frozenset({"a"})}, pool_errors={"img-9": "timeout"})
    assert s["verdict"] == "ERROR"
    assert s["errors"]["pool"] == 1
    clean = run.score({"q1": ["a"]}, {"q1": frozenset({"a"})})
    assert clean["verdict"] == "OK"


def test_a_query_whose_embedding_failed_ranks_as_an_error_not_an_empty_list() -> None:
    fake = FakeEmbedder({"a": [1, 0, 0, 0], "b": [math.nan, 0, 0, 0], "d1": [1, 0, 0, 0]})
    q = _embed(fake, {"qa": "a", "qb": "b"})
    p = run.embed_texts(fake, {"img-1": "d1"}, "document", dim=4, timeout=5)
    ranked = run.rank_dense(q, p)
    assert ranked["qa"] == ["img-1"]
    assert ranked["qb"] == Errored("nonfinite")


# --- the metrics, by hand -----------------------------------------------------------


def test_recall_at_5_divides_by_the_smaller_of_relevant_and_five() -> None:
    assert run.recall_at(["x", "a", "y", "b", "z"], frozenset({"a", "b"})) == pytest.approx(1.0)
    six = frozenset({"a", "b", "c", "d", "e", "f"})
    assert run.recall_at(["a", "x", "b", "y", "z", "c"], six) == pytest.approx(2 / 5)
    assert run.recall_at(["x"] * 5 + ["a"], frozenset({"a"})) == 0.0


def test_mrr_at_10_is_the_reciprocal_rank_of_the_first_relevant_inside_ten() -> None:
    assert run.mrr_at(["x", "a", "b"], frozenset({"a", "b"})) == pytest.approx(0.5)
    tenth = [f"x{i}" for i in range(9)] + ["a"]
    assert run.mrr_at(tenth, frozenset({"a"})) == pytest.approx(0.1)
    assert run.mrr_at([*tenth[:9], "y", "a"], frozenset({"a"})) == 0.0


def test_rrf_sums_one_over_sixty_plus_rank_and_an_error_in_either_channel_wins() -> None:
    a = {"q1": ["x", "y"], "q2": Errored("timeout")}
    b = {"q1": ["y", "z"], "q2": ["x"]}
    fused = run.rrf([a, b])
    assert fused["q1"] == ["y", "x", "z"]  # 1/61+1/62 > 1/61 > 1/62
    assert fused["q2"] == Errored("timeout")
    assert run.RRF_K == 60


def test_matryoshka_truncates_then_renormalises() -> None:
    assert matryoshka([3.0, 4.0, 12.0], 2) == pytest.approx([0.6, 0.8])
    with pytest.raises(EmbedError) as e:
        matryoshka([0.0, 0.0, 1.0], 2)
    assert e.value.kind == "zero_vector"


def test_the_bootstrap_ci_brackets_the_mean_and_is_reproducible() -> None:
    values = [0.0, 1.0, 1.0, 0.5, 0.0, 1.0]
    lo, hi = run.bootstrap_ci(values)
    assert lo <= sum(values) / len(values) <= hi
    assert run.bootstrap_ci(values) == (lo, hi)


def test_the_reading_gap_is_a_query_no_lexical_run_finds_in_its_top_ten() -> None:
    labels = {"q1": frozenset({"a"}), "q2": frozenset({"b"}), "q3": frozenset({"c"})}
    trigram = {"q1": ["a"], "q2": ["x"], "q3": Errored("exception")}
    bm25 = {"q1": ["x"], "q2": [f"x{i}" for i in range(10)] + ["b"], "q3": ["c"]}
    gap = run.reading_gap([trigram, bm25], labels)
    assert gap == {"q1": False, "q2": True, "q3": Errored("exception")}


# --- the adapters --------------------------------------------------------------------


class FakeTransport:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.bodies: list[dict] = []

    def __call__(self, method, url, body, timeout):
        self.bodies.append(body)
        return {"embeddings": [[1.0] + [0.0] * (self.dim - 1) for _ in body["input"]]}


def test_gemma2_prefixes_live_in_the_ollama_adapter() -> None:
    t = FakeTransport(768)
    emb = OllamaEmbedder("embeddinggemma-2:440m-bf16-text-vision", native_dim=768,
                         dtype="bf16", transport=t)
    emb.embed_text(["打風"], "query")
    emb.embed_text(["打風"], "document")
    assert t.bodies[0]["input"] == ["task: search result | query: 打風"]
    assert t.bodies[1]["input"] == ["title: none | text: 打風"]
    assert GEMMA2_PREFIX.query == "task: search result | query: "
    assert GEMMA2_PREFIX.document == "title: none | text: "


def test_bge_m3_gets_no_prefix_and_ollama_never_truncates_silently() -> None:
    t = FakeTransport(1024)
    OllamaEmbedder("bge-m3", native_dim=1024, transport=t).embed_text(["打風"], "query")
    assert t.bodies[0]["input"] == ["打風"]
    assert t.bodies[0]["truncate"] is False


def test_gemma2_prefixes_live_in_the_hf_adapter() -> None:
    sent: list[list[str]] = []

    def encode(texts):
        sent.append(list(texts))
        return [[1.0] + [0.0] * 767 for _ in texts]

    emb = HFEmbedder("google/embeddinggemma-2", revision="r", dtype="bfloat16",
                     native_dim=768, encode=encode)
    emb.embed_text(["打風"], "query")
    emb.embed_text(["打風"], "document")
    assert sent == [["task: search result | query: 打風"], ["title: none | text: 打風"]]


@pytest.mark.parametrize("dtype", ["float16", "fp16", "torch.float16", "half"])
def test_float16_is_refused_at_construction(dtype: str) -> None:
    with pytest.raises(ValueError, match="float16"):
        HFEmbedder("google/embeddinggemma-2", revision="r", dtype=dtype, native_dim=768,
                   encode=lambda xs: [])
    with pytest.raises(ValueError, match="float16"):
        OllamaEmbedder("embeddinggemma-2:740m", native_dim=768, dtype="F16",
                       transport=FakeTransport(768))


def test_ollama_refuses_media_rather_than_embed_the_text_alone() -> None:
    """Ollama 0.35 answers an `images` field on /api/embed with the text-only
    vector (probed 2026-10-15): a silent pass of the wrong thing."""
    t = FakeTransport(2048)
    with pytest.raises(EmbedError) as e:
        OllamaEmbedder("milkey/wemm-embedding-2b:Q4_K_M", native_dim=2048,
                       transport=t).embed_media([Path("x.jpg")])
    assert e.value.kind == "unsupported"
    assert t.bodies == []


def test_llama_server_sends_the_recorded_media_shape(tmp_path: Path) -> None:
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpeg")
    bodies: list[dict] = []

    def transport(method, url, body, timeout):
        bodies.append(body)
        return [{"index": 0, "embedding": [[1.0] + [0.0] * 2047]}]

    emb = adapters.LlamaServerEmbedder("wemm-2b", native_dim=2048, transport=transport)
    vec = emb.embed_media([img])
    assert len(vec[0]) == 2048
    content = bodies[0]["content"]
    assert content["multimodal_data"] == ["anBlZw=="]
    assert "<__media__>" in content["prompt_string"]


# --- the set, the watchdog and the output --------------------------------------------


def test_a_set_that_does_not_match_its_lock_refuses_to_load(tmp_path: Path) -> None:
    copy = tmp_path / "set"
    shutil.copytree(SET_DIR, copy, ignore=shutil.ignore_patterns("results"))
    run.load_set(copy)  # the committed set loads
    labels = copy / "labels.yaml"
    labels.write_text(labels.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(run.LockMismatchError):
        run.load_set(copy)


def _relock(root: Path, names: list[str]) -> None:
    lines = [f"{hashlib.sha256((root / n).read_bytes()).hexdigest()}  {n}" for n in names]
    (root / "labels.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _per_slice_files(tmp_path: Path) -> Path:
    """The set in #18's shape: each slice names its items and labels file, and
    the lock covers more files than the slice reads."""
    root = tmp_path / "set"
    shutil.copytree(SET_DIR, root, ignore=shutil.ignore_patterns("results", "*.py"))
    (root / "media.yaml").rename(root / "images.yaml")
    (root / "labels.yaml").rename(root / "labels_image.yaml")
    (root / "texts.yaml").write_text("items: {}\n", encoding="utf-8")
    the_set = (root / "set.yaml").read_text(encoding="utf-8")
    (root / "set.yaml").write_text(the_set.replace(
        "  text_image:\n",
        "  text_image:\n    items: images.yaml\n    labels: labels_image.yaml\n", 1,
    ), encoding="utf-8")
    _relock(root, ["set.yaml", "images.yaml", "texts.yaml", "labels_image.yaml"])
    return root


def test_the_loader_reads_the_files_the_slice_names(tmp_path: Path) -> None:
    root = _per_slice_files(tmp_path)
    got = run.load_set(root)
    assert got.queries == run.load_set(SET_DIR).queries
    assert set(got.lock) == {"set.yaml", "images.yaml", "texts.yaml", "labels_image.yaml"}
    (root / "texts.yaml").write_text("items: {changed: 1}\n", encoding="utf-8")
    with pytest.raises(run.LockMismatchError):  # every locked file, not only the slice's
        run.load_set(root)


def test_a_file_the_slice_reads_but_the_lock_omits_refuses(tmp_path: Path) -> None:
    root = _per_slice_files(tmp_path)
    _relock(root, ["set.yaml", "images.yaml"])
    with pytest.raises(run.LockMismatchError, match=r"labels_image\.yaml"):
        run.load_set(root)


GIB_KIB = 1024 * 1024


def test_the_watchdog_aborts_below_three_gib_available() -> None:
    reading = {"MemAvailable": 4 * GIB_KIB, "SwapTotal": 0, "SwapFree": 0}
    dog = run.MemoryWatchdog(read=lambda: dict(reading))
    dog.check()
    reading["MemAvailable"] = 3 * GIB_KIB - 1
    with pytest.raises(run.AbortedError, match="MemAvailable"):
        dog.check()


def test_the_watchdog_aborts_when_swap_grows_by_more_than_one_gib() -> None:
    reading = {"MemAvailable": 8 * GIB_KIB, "SwapTotal": 20 * GIB_KIB, "SwapFree": 10 * GIB_KIB}
    dog = run.MemoryWatchdog(read=lambda: dict(reading))
    reading["SwapFree"] = 9 * GIB_KIB - 1
    with pytest.raises(run.AbortedError, match="swap"):
        dog.check()


def test_the_watchdog_refuses_a_host_it_cannot_read() -> None:
    def unreadable():
        raise FileNotFoundError("/proc/meminfo")

    with pytest.raises(run.AbortedError):
        run.MemoryWatchdog(read=unreadable)


def test_the_result_holds_aggregates_only_no_text_and_no_vectors() -> None:
    the_set = run.load_set(SET_DIR)
    qids = list(the_set.queries)
    pool = list(the_set.pool)
    perfect = {q: sorted(the_set.relevant[q]) for q in qids}
    lexical = {q: [pool[0]] for q in qids}
    result = run.evaluate(
        the_set,
        {"lexical:trigram": run.ArmRun(perfect), "lexical:bm25": run.ArmRun(lexical)},
        lexical_arms=("lexical:trigram", "lexical:bm25"),
        provenance={"commit": "abc"},
    )
    blob = json.dumps(result, ensure_ascii=False)
    for q in the_set.queries.values():
        assert q.text not in blob
    for m in the_set.media.values():
        assert m.reading not in blob
    assert result["arms"]["lexical:trigram"]["recall5"] == pytest.approx(1.0)
    assert set(result["arms"]["lexical:trigram"]["by_lang"]) == {"yue", "en", "mixed"}
    assert result["labels_lock"] == the_set.lock
    assert "reading_gap" in result
