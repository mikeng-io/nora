"""The retrieval bake-off's harness measures fail-closed.

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
#: A pinned Hugging Face commit, the only revision HFEmbedder accepts.
SHA = "914f7f89142e33e77833254d9c9b90c3cef7303b"


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
    """Ollama's API: /api/show answers `capabilities`, /api/embed one unit
    vector per input. Records every embed body."""

    def __init__(self, dim: int, capabilities: tuple[str, ...] = ("embedding",)) -> None:
        self.dim = dim
        self.capabilities = list(capabilities)
        self.bodies: list[dict] = []
        self.shows = 0

    def __call__(self, method, url, body, timeout):
        if url.endswith("/api/show"):
            self.shows += 1
            return {"capabilities": self.capabilities, "details": {}}
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

    emb = HFEmbedder("google/embeddinggemma-2", revision=SHA, dtype="bfloat16",
                     native_dim=768, encode=encode)
    emb.embed_text(["打風"], "query")
    emb.embed_text(["打風"], "document")
    assert sent == [["task: search result | query: 打風"], ["title: none | text: 打風"]]


class FakeST:
    """What HFEmbedder reads off a loaded SentenceTransformer: its parameters'
    dtypes, the revision it records, and encode."""

    def __init__(self, dtype: str = "torch.float32", revision: str = SHA) -> None:
        self.dtype = dtype
        self.model_card_data = type("Card", (), {"base_model_revision": revision})()
        self.seen: list[list] = []

    def parameters(self):
        return iter([type("P", (), {"dtype": self.dtype})() for _ in range(3)])

    def encode(self, xs, normalize_embeddings=False):
        self.seen.append(list(xs))
        rows = [[1.0] + [0.0] * 767 for _ in xs]
        return type("Arr", (), {"tolist": lambda self: rows})()


def _hf(st: FakeST, dtype: str = "float32") -> HFEmbedder:
    return HFEmbedder("google/embeddinggemma-2", revision=SHA, dtype=dtype, native_dim=768,
                      load=lambda name, revision, dtype: st)


def test_hf_loads_at_the_pinned_revision_and_dtype() -> None:
    emb = _hf(FakeST())
    assert len(emb.embed_text(["打風"], "query")[0]) == 768
    assert emb.provenance()["param_dtype"] == "torch.float32"


def test_hf_refuses_a_model_whose_parameters_are_not_the_requested_dtype() -> None:
    """A model_kwargs the library ignores would load the checkpoint's own
    bfloat16 under a result that says float32."""
    with pytest.raises(ValueError, match="dtype"):
        _hf(FakeST(dtype="torch.bfloat16")).embed_text(["打風"], "query")


@pytest.mark.parametrize("revision", ["main", "r", SHA[:39], SHA.upper(), SHA + "0"])
def test_hf_requires_a_pinned_forty_hex_commit(revision: str) -> None:
    with pytest.raises(ValueError, match="revision"):
        HFEmbedder("google/embeddinggemma-2", revision=revision, dtype="float32",
                   native_dim=768, encode=lambda xs: [])


def test_hf_refuses_a_model_loaded_at_another_revision() -> None:
    with pytest.raises(ValueError, match="revision"):
        _hf(FakeST(revision="0" * 40)).embed_text(["打風"], "query")


def test_hf_sends_images_unprefixed(tmp_path: Path) -> None:
    """Gemma 2's card: prefixes are for text only, media goes in bare."""
    from PIL import Image

    paths = []
    for i, colour in enumerate(("red", "blue")):
        paths.append(tmp_path / f"{i}.jpg")
        Image.new("RGB", (8, 8), colour).save(paths[-1])
    st = FakeST()
    _hf(st).embed_media(paths)
    assert len(st.seen) == 1 and len(st.seen[0]) == 2
    assert all(isinstance(x, Image.Image) for x in st.seen[0])


WEMM = "tencent/WeMM-Embedding-2B"


def _wemm_hf(st: FakeST, pieces: list[str], *, text_only: bool = True) -> HFEmbedder:
    return HFEmbedder(WEMM, revision=SHA, dtype="float32", native_dim=2048, text_only=text_only,
                      load=lambda name, revision, dtype: st, pieces=lambda st: pieces)


def test_wemm_through_hf_renders_the_wrapper_llama_server_tokenized() -> None:
    """The HF processor must render the probe exactly as /tokenize did, with
    no instruction on the text (WeMM's `prompts` is {})."""
    st = FakeST()
    emb = _wemm_hf(st, list(adapters.WEMM_PIECES))
    emb.embed_text(["打風"], "query")
    assert st.seen == [["打風"]]
    assert emb.provenance()["template_pieces"] == list(adapters.WEMM_PIECES)


@pytest.mark.parametrize("pieces", [
    # transformers 5.19: the newline before `<embedding>` survives
    ["<|im_start|>", "user", "\n", "two", " bowls", " of", " noodles", "<|im_end|>", "\n",
     "<embedding>"],
    # no `<embedding>` appended: last-token pooling would read `<|im_end|>`
    ["<|im_start|>", "user", "\n", "two", " bowls", " of", " noodles", "<|im_end|>"],
])
def test_wemm_through_hf_refuses_any_other_rendering(pieces: list[str]) -> None:
    with pytest.raises(ValueError, match="renders"):
        _wemm_hf(FakeST(), pieces).embed_text(["打風"], "query")


def test_a_text_only_load_refuses_media(tmp_path: Path) -> None:
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpeg")
    with pytest.raises(EmbedError) as e:
        _wemm_hf(FakeST(), list(adapters.WEMM_PIECES)).embed_media([img])
    assert e.value.kind == "unsupported"


def test_wemm_arms_are_labelled_by_serving_path() -> None:
    """Q4 (llama-server) and full precision (HF) arms must not share a name,
    or a decide() over both would let one overwrite the other."""
    hf = run.wemm_embedder(hf_repo=WEMM, revision=SHA, dtype="float32", text_only=True,
                           name="x", timeout=1)
    q4 = run.wemm_embedder(llama_url="http://x", name="wemm-embedding-2b:Q4_K_M", timeout=1)
    assert hf is not None and q4 is not None
    assert (hf[1], q4[1]) == ("wemm-fp32", "wemm")
    assert isinstance(hf[0], HFEmbedder) and hf[0].native_dim == 2048
    with pytest.raises(ValueError, match="two serving paths"):
        run.wemm_embedder(llama_url="http://x", hf_repo=WEMM, revision=SHA, dtype="float32",
                          name="x", timeout=1)


@pytest.mark.parametrize("dtype", ["float16", "fp16", "torch.float16", "half"])
def test_float16_is_refused_at_construction(dtype: str) -> None:
    with pytest.raises(ValueError, match="float16"):
        HFEmbedder("google/embeddinggemma-2", revision=SHA, dtype=dtype, native_dim=768,
                   encode=lambda xs: [])
    with pytest.raises(ValueError, match="float16"):
        OllamaEmbedder("embeddinggemma-2:740m", native_dim=768, dtype="F16",
                       transport=FakeTransport(768))


def test_stage_two_runs_gemma_through_hf_at_a_pinned_revision_and_explicit_dtype() -> None:
    emb = run.gemma_embedder(hf_repo="google/embeddinggemma-2", revision=SHA, dtype="float32",
                             base_url="http://x", timeout=1)
    assert isinstance(emb, HFEmbedder)
    prov = emb.provenance()
    assert (prov["revision"], prov["dtype"], prov["native_dim"]) == (SHA, "float32", 768)
    assert prov["prefix"]["query"] == GEMMA2_PREFIX.query


@pytest.mark.parametrize("kw", [
    {"hf_repo": "google/embeddinggemma-2", "dtype": "float32"},          # no revision
    {"hf_repo": "google/embeddinggemma-2", "revision": SHA},             # no dtype
    {"hf_repo": "google/embeddinggemma-2", "revision": SHA, "dtype": "float16"},
    {"hf_repo": "google/embeddinggemma-2", "revision": SHA, "dtype": "float32",
     "ollama_tag": "embeddinggemma-2"},                                  # two paths
])
def test_stage_two_refuses_an_unpinned_half_precision_or_ambiguous_gemma(kw: dict) -> None:
    with pytest.raises(ValueError):
        run.gemma_embedder(base_url="http://x", timeout=1, **kw)


def test_stage_two_still_runs_gemma_through_ollama_and_is_off_by_default() -> None:
    assert isinstance(run.gemma_embedder(ollama_tag="embeddinggemma-2", base_url="http://x",
                                         timeout=1), OllamaEmbedder)
    assert run.gemma_embedder(base_url="http://x", timeout=1) is None


def test_ollama_refuses_media_for_a_model_without_vision() -> None:
    """Ollama answers an `images` field on /api/embed with the text-only
    vector (Ollama 0.35 and 0.40.4): a silent pass of the wrong
    thing, so a model that does not declare vision is never sent media."""
    t = FakeTransport(2048)
    with pytest.raises(EmbedError) as e:
        OllamaEmbedder("milkey/wemm-embedding-2b:Q4_K_M", native_dim=2048,
                       transport=t).embed_media([Path("x.jpg")])
    assert e.value.kind == "unsupported"
    assert t.bodies == []


def test_ollama_sends_media_as_image_objects_for_a_vision_model(tmp_path: Path) -> None:
    """The one shape Ollama 0.40.4 embeds: an `image`
    object per input. No prefix: Gemma 2's card puts none on media."""
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpeg")
    t = FakeTransport(768, capabilities=("embedding", "vision", "audio"))
    emb = OllamaEmbedder("embeddinggemma-2:latest", native_dim=768, transport=t)
    vecs = emb.embed_media([img, img])
    assert len(vecs) == 2
    assert t.bodies == [{"model": "embeddinggemma-2:latest", "truncate": False,
                         "input": [{"image": "anBlZw=="}, {"image": "anBlZw=="}]}]
    emb.embed_media([img])
    assert t.shows == 1  # capabilities read once


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
    """The set with the image slice's files renamed: the loader must follow
    the names `set.yaml` gives, and check every file the lock lists."""
    root = tmp_path / "set"
    shutil.copytree(SET_DIR, root, ignore=shutil.ignore_patterns("results", "*.py"))
    (root / "media.yaml").rename(root / "images.yaml")
    (root / "labels.yaml").rename(root / "labels_image.yaml")
    (root / "texts.yaml").write_text("items: {}\n", encoding="utf-8")
    the_set = (root / "set.yaml").read_text(encoding="utf-8")
    assert "    items: media.yaml\n    labels: labels.yaml\n" in the_set
    (root / "set.yaml").write_text(the_set.replace(
        "    items: media.yaml\n    labels: labels.yaml\n",
        "    items: images.yaml\n    labels: labels_image.yaml\n", 1,
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


def test_the_watchdog_aborts_when_free_swap_drops_below_one_gib() -> None:
    """A host already near the end of its swap cannot grow it by 1 GiB, so
    the growth rule alone never fires there (e.g. 0.3 GiB free)."""
    reading = {"MemAvailable": 8 * GIB_KIB, "SwapTotal": 24 * GIB_KIB, "SwapFree": GIB_KIB}
    dog = run.MemoryWatchdog(read=lambda: dict(reading))
    dog.check()
    reading["SwapFree"] = GIB_KIB - 1
    with pytest.raises(run.AbortedError, match="free swap"):
        dog.check()


def test_the_watchdog_does_not_abort_a_host_without_swap() -> None:
    dog = run.MemoryWatchdog(read=lambda: {"MemAvailable": 8 * GIB_KIB,
                                           "SwapTotal": 0, "SwapFree": 0})
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


# --- embed errors, vector identity, ranking and the watchdog -----------------------


def test_an_embed_error_from_a_batch_keeps_its_kind_for_every_item() -> None:
    fake = FakeEmbedder({}, raise_=EmbedError("unsupported"))
    assert _embed(fake, {"qa": "a", "qb": "b"}).errors == {"qa": "unsupported", "qb": "unsupported"}


def test_every_requested_id_is_embedded_or_an_error(monkeypatch, tmp_path: Path) -> None:
    """The invariant behind every other fail-closed rule: no id can vanish
    from a batch and be scored as if it never existed."""
    monkeypatch.setattr(run, "_embed_batch", lambda *a, **k: None)
    assert _embed(FakeEmbedder({}), {"qa": "a", "qb": "b"}).errors == {"qa": "lost", "qb": "lost"}
    img = tmp_path / "a.jpg"
    img.write_bytes(b"a")
    got = run.embed_media(FakeEmbedder({}), {"img-1": (img, hashlib.sha256(b"a").hexdigest())},
                          dim=4, timeout=5)
    assert got.errors == {"img-1": "lost"}


class SameVector(FakeEmbedder):
    """A runtime that drops the image and embeds only its wrapper prompt."""

    def __init__(self, vectors: list[list[float]]) -> None:
        super().__init__({})
        self.vectors = iter(vectors)

    def embed_media(self, paths):
        return [next(self.vectors) for _ in paths]


def _images(tmp_path: Path, n: int) -> dict[str, tuple[Path, str]]:
    out = {}
    for i in range(n):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(bytes([i]) * 10)
        out[f"img-{i}"] = (p, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def test_different_images_with_one_vector_are_errors(tmp_path: Path) -> None:
    v = [0.0, 1.0, 0.3, 0.0]
    got = run.embed_media(SameVector([v, [1.0, 0, 0, 0], v]), _images(tmp_path, 3),
                          dim=4, timeout=5)
    assert got.errors == {"img-0": "same_vector", "img-2": "same_vector"}
    assert set(got.vectors) == {"img-1"}


def test_the_same_image_twice_may_share_its_vector(tmp_path: Path) -> None:
    images = _images(tmp_path, 1)
    images["img-0-again"] = images["img-0"]
    v = [0.0, 1.0, 0.3, 0.0]
    got = run.embed_media(SameVector([v, v]), images, dim=4, timeout=5)
    assert got.errors == {}


def test_different_images_with_different_vectors_pass(tmp_path: Path) -> None:
    vs = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]]
    got = run.embed_media(SameVector(vs), _images(tmp_path, 3), dim=4, timeout=5)
    assert got.errors == {}


def test_a_trigram_arm_that_fails_to_load_ranks_every_query_as_an_error(monkeypatch) -> None:
    def down(*a, **k):
        raise RuntimeError("projector down")

    monkeypatch.setattr(run.lexical_seed, "load", down)
    the_set = run.load_set(SET_DIR)
    arm = run._trigram("postgresql+asyncpg://unused/bakeoff", the_set)
    assert set(arm.rankings.values()) == {Errored("exception")}
    assert len(arm.rankings) == len(the_set.queries)


def test_one_arm_with_an_error_makes_the_whole_result_error() -> None:
    the_set = run.load_set(SET_DIR)
    ok = {q: sorted(the_set.relevant[q]) for q in the_set.queries}
    broken = dict(ok, **{next(iter(ok)): Errored("timeout")})
    result = run.evaluate(
        the_set, {"lexical:trigram": run.ArmRun(ok), "lexical:bm25": run.ArmRun(ok),
                  "dense:bge-m3": run.ArmRun(broken)},
        lexical_arms=("lexical:trigram", "lexical:bm25"), provenance={})
    assert result["arms"]["dense:bge-m3"]["verdict"] == "ERROR"
    assert result["verdict"] == "ERROR"


def test_rrf_ranks_start_at_one() -> None:
    scores = run.rrf_scores([{"q": ["x", "y"]}, {"q": ["y", "z"]}])["q"]
    assert scores == pytest.approx({"x": 1 / 61, "y": 1 / 61 + 1 / 62, "z": 1 / 62})


def test_a_dense_tie_breaks_on_the_pool_id_ascending() -> None:
    fake = FakeEmbedder({"q": [1, 0, 0, 0], "d": [1, 0, 0, 0]})
    q = _embed(fake, {"q1": "q"})
    pool = run.embed_texts(fake, {"img-b1": "d", "img-a2": "d"}, "document", dim=4, timeout=5)
    assert run.rank_dense(q, pool)["q1"] == ["img-a2", "img-b1"]


def test_the_watchdog_guards_the_warm_ups_too() -> None:
    class Tripped:
        lowest_available_kib = 0

        def check(self):
            raise run.AbortedError("MemAvailable low")

    calls: list[list[str]] = []

    class Counting(FakeEmbedder):
        def embed_text(self, texts, role):
            calls.append(list(texts))
            return super().embed_text(texts, role)

    with pytest.raises(run.AbortedError):
        run._text_arms(Counting({"warm up": [1, 0, 0, 0]}), "fake", run.load_set(SET_DIR), (4,),
                       timeout=5, dog=Tripped(), base="http://unused")
    assert calls == []  # not even a warm-up reached the model


def test_a_host_the_watchdog_cannot_read_writes_an_aborted_result(
    monkeypatch, tmp_path: Path,
) -> None:
    def unreadable():
        raise FileNotFoundError("/proc/meminfo")

    monkeypatch.setattr(run, "read_meminfo", unreadable)
    rc = run.main(["--dsn", "postgresql+asyncpg://unused/bakeoff", "--out", str(tmp_path)])
    assert rc == 3
    [written] = tmp_path.glob("*-aborted.json")
    assert json.loads(written.read_text())["verdict"] == "ERROR"


def test_set_dir_clean_ignores_the_results_directory(monkeypatch) -> None:
    seen: list[list[str]] = []

    def quiet(cmd):
        seen.append(cmd)
        return ""

    monkeypatch.setattr(run, "_quiet", quiet)
    run._commit()
    status = next(c for c in seen if "status" in c)
    assert ":(exclude)results" in status


# --- stage 2: image vectors -----------------------------------------------------------


class ImageFake(FakeEmbedder):
    """One distinct unit vector per image byte value; queries point at img-1."""

    name = "embeddinggemma-2:latest"

    def embed_text(self, texts, role):
        return [[0.0, 1.0, 0.0, 0.0] for _ in texts]

    def embed_media(self, paths):
        out = []
        for p in paths:
            b = p.read_bytes()[0]
            out.append([[1.0, 0, 0, 0], [0, 1.0, 0.5, 0], [0, 0, 0, 1.0]][b])
        return out

    def provenance(self):
        return {"model": self.name}


def _tiny_set(tmp_path: Path) -> run.BakeoffSet:
    media = {}
    for i in range(3):
        (tmp_path / f"{i}.jpg").write_bytes(bytes([i]) * 4)
        sha = hashlib.sha256(bytes([i]) * 4).hexdigest()
        (tmp_path / f"{sha}.jpg").write_bytes(bytes([i]) * 4)
        media[f"img-{i}"] = run.Media(sha, "", "", "")
    (tmp_path / f"{media['img-2'].sha256}.jpg").write_bytes(b"tampered")
    return run.BakeoffSet(
        slice="text_image", version=1, pool=("img-0", "img-1", "img-2"),
        queries={"q1": run.Query("the second one", "en")}, relevant={"q1": frozenset({"img-1"})},
        hard_negatives={"q1": frozenset()}, media=media, lock={})


def test_image_arms_embed_once_and_derive_each_matryoshka_dimension(tmp_path: Path) -> None:
    arms = run._image_arms(ImageFake({}), "gemma2", _tiny_set(tmp_path), (4, 2), timeout=5,
                           dog=None, base="http://unused", media_dir=tmp_path)
    assert set(arms) == {"dense:gemma2@4", "dense:gemma2@2"}
    assert arms["dense:gemma2@4"].rankings["q1"][0] == "img-1"
    # img-2's bytes do not match its sha256: an ERROR at every dimension.
    assert arms["dense:gemma2@4"].pool_errors == {"img-2": "hash_mismatch"}
    assert arms["dense:gemma2@2"].pool_errors == {"img-2": "hash_mismatch"}
    assert arms["dense:gemma2@2"].provenance["dims"] == 2
    assert arms["dense:gemma2@2"].provenance["over"] == "the images"


def test_a_truncated_embedding_is_renormalised_and_keeps_its_errors() -> None:
    e = run.Embedded(vectors={"a": [0.6, 0.8, 0.0], "z": [0.0, 0.0, 1.0]}, errors={"b": "timeout"})
    t = run.truncated(e, 2)
    assert t.vectors["a"] == pytest.approx([0.6, 0.8])
    assert t.errors == {"b": "timeout", "z": "zero_vector"}


# --- the text slices ----------------------------------------------------------------------


@pytest.mark.parametrize(("slice_name", "pool", "queries"), [
    ("text_image", 224, 69), ("cantonese", 400, 61), ("names", 460, 48),
])
def test_every_slice_loads_under_the_five_file_lock(
    slice_name: str, pool: int, queries: int,
) -> None:
    s = run.load_set(SET_DIR, slice_name)
    assert (len(s.pool), len(s.queries)) == (pool, queries)
    assert set(s.lock) == {
        "set.yaml", "media.yaml", "texts.yaml", "labels.yaml", "labels_text.yaml"}
    assert all(s.text(pid) for pid in s.pool)  # every item has the text a text arm embeds


def test_a_text_slice_carries_its_messages_and_its_time_only_queries() -> None:
    s = run.load_set(SET_DIR, "cantonese")
    assert s.media == {}
    first = s.messages["msg-001"]
    assert first.sent_ms == 1783795260000  # 12 Jul 2026 02:41 Hong Kong time
    assert s.text("msg-001") == first.text
    assert s.subsets["time_only"] == ("ca-q16", "ca-q35", "ca-q36", "ca-q78", "ca-q79")
    assert run.load_set(SET_DIR, "names").subsets["time_only"] == ("nm-q22",)
    assert run.load_set(SET_DIR, "text_image").subsets == {}


def test_every_result_reports_the_time_only_queries_apart() -> None:
    s = run.load_set(SET_DIR, "names")
    perfect = {q: sorted(s.relevant[q]) for q in s.queries}
    result = run.evaluate(s, {"lexical:trigram": run.ArmRun(perfect)},
                          lexical_arms=("lexical:trigram",), provenance={})
    arm = result["arms"]["lexical:trigram"]
    assert arm["time_only"]["n_queries"] == 1
    assert arm["outside_time_only"]["n_queries"] == len(s.queries) - 1
    assert result["subsets"]["time_only"] == ["nm-q22"]
