"""Qwen3-VL-Embedding-2B and jina-embeddings-v5-omni-small get exactly the
input their own files define.

The strings below are copied from each model's files at the pinned commit
(`adapters.py` names them). A format that drifts from them would lose the
comparison without an error, so the adapter checks every sequence the model
receives and refuses anything else; the fakes here feed it right and wrong
sequences on purpose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evals.retrieval_bakeoff import adapters, run
from evals.retrieval_bakeoff.adapters import (
    JINA_V5_OMNI_SMALL,
    OFFICIAL,
    QWEN3VL_DEFAULT_INSTRUCTION_VARIANT,
    QWEN3VL_EMBEDDING_2B,
    EmbedError,
    OfficialEmbedder,
    OfficialFormat,
    fed_sequence,
)
from evals.retrieval_bakeoff.run import Errored

QWEN, JINA = QWEN3VL_EMBEDDING_2B, JINA_V5_OMNI_SMALL


# --- the exact strings ---------------------------------------------------------------


def _qwen_turn(instruction: str, x: str) -> str:
    return (f"<|im_start|>system\n{instruction}<|im_end|>\n<|im_start|>user\n{x}<|im_end|>\n"
            "<|im_start|>assistant\n<|endoftext|>")


def test_qwen3vl_gives_a_query_the_vendors_retrieval_instruction() -> None:
    """QwenLM/Qwen3-VL-Embedding's own retrieval usage (its README's
    Transformers example, @393e2978, and its MMEB text-to-image eval) gives a
    QUERY "Retrieve images or text relevant to the user's query."; the
    tokenizer.json post-processor appends `<|endoftext|>`, the pooled token."""
    assert QWEN.sent("query", "打風嗰晚啲小巴停晒") == _qwen_turn(
        "Retrieve images or text relevant to the user's query.", "打風嗰晚啲小巴停晒")
    assert QWEN.encode["query"] == {
        "prompt": "Retrieve images or text relevant to the user's query."}


def test_qwen3vl_gives_a_document_the_default_instruction() -> None:
    """config_sentence_transformers.json `prompts.default`, chat_template.jinja's
    `default_system_message` and the repository script's `default_instruction`."""
    assert QWEN.sent("document", "打風嗰晚啲小巴停晒") == _qwen_turn(
        "Represent the user's input.", "打風嗰晚啲小巴停晒")
    assert QWEN.encode["document"] == QWEN.encode["image"] == {
        "prompt": "Represent the user's input."}


def test_the_default_instruction_variant_is_recorded_under_its_own_label() -> None:
    """Giving queries the default instruction too is a recorded variant, never
    the scored arm."""
    v = QWEN3VL_DEFAULT_INSTRUCTION_VARIANT
    assert v.label == "qwen3vl-defaultinst" and v.label not in OFFICIAL
    assert v.sent("query", "x") == _qwen_turn("Represent the user's input.", "x")
    assert (v.revision, v.image, v.sent("document", "x")) == (
        QWEN.revision, QWEN.image, QWEN.sent("document", "x"))


def test_qwen3vl_wraps_an_image_in_the_same_turn() -> None:
    assert QWEN.sent("image") == (
        "<|im_start|>system\nRepresent the user's input.<|im_end|>\n"
        "<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|><|im_end|>\n"
        "<|im_start|>assistant\n<|endoftext|>")


def test_qwen3vl_runs_no_remote_code() -> None:
    assert not QWEN.trust_remote_code


def test_jina_v5_omni_prefixes_text_with_its_retrieval_role_and_nothing_else() -> None:
    """custom_st.py tokenizes the prompt plus the text: no chat template, no
    special token."""
    assert JINA.sent("query", "two bowls of noodles") == "Query: two bowls of noodles"
    assert JINA.sent("document", "今晚七點喺旺角食飯") == "Document: 今晚七點喺旺角食飯"


def test_jina_v5_omni_wraps_a_pool_image_as_a_document_in_its_chat_template() -> None:
    """custom_st.py `_build_eval_image_prompt` with the `document` prompt: the
    card says the role applies to every modality, and a pool image is a
    document."""
    assert JINA.sent("image") == (
        "<|im_start|>user\nDocument: <|vision_start|><|image_pad|><|vision_end|><|im_end|>\n")
    assert JINA.encode == {"query": {"prompt_name": "query"},
                           "document": {"prompt_name": "document"},
                           "image": {"prompt_name": "document"}}


def test_jina_v5_omni_loads_the_retrieval_adapter_with_its_own_code() -> None:
    assert JINA.model_kwargs == {"default_task": "retrieval", "modality": "vision"}
    assert JINA.trust_remote_code
    assert JINA.right_padding_only


def test_each_candidate_is_pinned_and_records_its_licence() -> None:
    assert QWEN.revision == "9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda"
    assert JINA.revision == "950200abde3a3bcf3d5bdfe5d28fb5bb5e7dfbb0"
    assert QWEN.licence == "apache-2.0"
    assert JINA.licence.startswith("cc-by-nc-4.0") and "measurement only" in JINA.licence
    assert (QWEN.native_dim, JINA.native_dim) == (2048, 1024)
    assert set(OFFICIAL) == {"qwen3vl", "jina5omni"}


def test_a_role_that_is_not_query_document_or_image_is_refused() -> None:
    with pytest.raises(ValueError, match="role"):
        QWEN.sent("passage", "x")


# --- what is compared ----------------------------------------------------------------


def test_the_image_token_run_collapses_to_one_token() -> None:
    fed = "a<|vision_start|>" + "<|image_pad|>" * 176 + "<|vision_end|>b"
    assert fed_sequence(fed, None, right_padding_only=False) == (
        "a<|vision_start|><|image_pad|><|vision_end|>b")


def test_a_left_padded_row_never_matches_when_the_pool_reads_the_mask_length() -> None:
    assert fed_sequence("Query: x", [0, 0, 1, 1], right_padding_only=True) != "Query: x"
    assert fed_sequence("Query: x", [1, 1, 0, 0], right_padding_only=True) == "Query: x"
    assert fed_sequence("Query: x", [0, 0, 1, 1], right_padding_only=False) == "Query: x"


# --- the adapter, against a fake model -----------------------------------------------


class FakeST:
    """A loaded SentenceTransformer as OfficialEmbedder sees it: parameters,
    and an encode whose model appends what it was fed to `fed`."""

    def __init__(self, fmt: OfficialFormat, fed: list[str], *, dtype: Any = "torch.float32",
                 feed: Any = None) -> None:
        self.fmt, self.fed = fmt, fed
        self.dtypes = [dtype] * 3 if isinstance(dtype, str) else list(dtype)
        self.feed = feed or (lambda fmt, x, kw: fmt.sent(
            "image" if not isinstance(x, str) else next(
                r for r in ("query", "document") if fmt.encode[r] == kw), x if isinstance(x, str)
            else ""))
        self.calls: list[tuple[list[Any], dict[str, Any]]] = []

    def parameters(self):
        return iter([type("P", (), {"dtype": d, "numel": lambda self: 10})()
                     for d in self.dtypes])

    def encode(self, xs, *, batch_size, normalize_embeddings, convert_to_numpy, **kw):
        assert normalize_embeddings is False and convert_to_numpy is True
        self.calls.append((list(xs), kw))
        for x in reversed(xs):  # the library reorders a batch by length
            self.fed.append(self.feed(self.fmt, x, kw))
        return [[1.0] + [0.0] * (self.fmt.native_dim - 1) for _ in xs]


def _emb(fmt: OfficialFormat, *, dtype: str = "float32", st_dtype: Any = "torch.float32",
         feed: Any = None, snapshot_name: str | None = None) -> tuple[OfficialEmbedder, list]:
    made: list[FakeST] = []

    def load(path, f, d, fed):
        made.append(FakeST(f, fed, dtype=st_dtype, feed=feed))
        return made[-1]

    emb = OfficialEmbedder(fmt, dtype=dtype, load=load,
                           snapshot=lambda repo, rev: f"/hub/snapshots/{snapshot_name or rev}")
    return emb, made


def test_qwen3vl_texts_that_reach_the_model_in_its_format_are_embedded() -> None:
    emb, made = _emb(QWEN)
    out = emb.embed_text(["打風", "two bowls of noodles"], "query")
    assert len(out) == 2 and len(out[0]) == 2048
    assert made[0].calls[0][1] == {
        "prompt": "Retrieve images or text relevant to the user's query."}
    prov = emb.provenance()
    assert prov["format_checked"]["query"] == 2
    assert prov["loaded_revision"] == QWEN.revision and prov["licence"] == "apache-2.0"


def test_jina_queries_and_documents_ask_for_their_own_prompt() -> None:
    emb, made = _emb(JINA)
    emb.embed_text(["x"], "query")
    emb.embed_text(["y"], "document")
    assert [kw for _, kw in made[0].calls] == [{"prompt_name": "query"},
                                               {"prompt_name": "document"}]


def test_images_go_in_as_rgb_pictures_under_the_image_role(tmp_path: Path) -> None:
    from PIL import Image

    paths = []
    for i, colour in enumerate(("red", "blue")):
        paths.append(tmp_path / f"{i}.png")
        Image.new("RGBA", (8, 8), colour).save(paths[-1])
    emb, made = _emb(JINA)
    assert len(emb.embed_media(paths)) == 2
    xs, kw = made[0].calls[0]
    assert kw == {"prompt_name": "document"}
    assert all(isinstance(x, Image.Image) and x.mode == "RGB" for x in xs)
    assert emb.provenance()["format_checked"]["image"] == 2


@pytest.mark.parametrize("wrong", [
    # sentence-transformers without the prompt: no system turn
    lambda fmt, x, kw: "<|im_start|>user\nx<|im_end|>\n<|im_start|>assistant\n<|endoftext|>",
    # the tokenizer's appended pooled token missing
    lambda fmt, x, kw: fmt.sent("query", x).removesuffix("<|endoftext|>"),
    # an instruction the card does not use
    lambda fmt, x, kw: fmt.sent("query", x).replace(
        "Retrieve images or text relevant to the user's query.", "Represent the user's input."),
])
def test_qwen3vl_a_sequence_not_in_its_format_is_an_error_never_a_vector(wrong) -> None:
    emb, _ = _emb(QWEN, feed=wrong)
    with pytest.raises(EmbedError) as e:
        emb.embed_text(["x"], "query")
    assert e.value.kind == "unsupported"


@pytest.mark.parametrize("wrong", [
    lambda fmt, x, kw: x,                                  # no role prefix
    lambda fmt, x, kw: "Document: " + x,                   # the other role
    lambda fmt, x, kw: "<left-padded: the last-token pool reads a pad>",
])
def test_jina_a_query_not_in_its_format_is_an_error_never_a_vector(wrong) -> None:
    emb, _ = _emb(JINA, feed=wrong)
    with pytest.raises(EmbedError, match="unsupported"):
        emb.embed_text(["x"], "query")


def test_a_model_fed_fewer_sequences_than_inputs_is_an_error() -> None:
    emb, made = _emb(JINA)
    emb.embed_text(["warm"], "query")
    made[0].feed = lambda fmt, x, kw: "Query: a"
    with pytest.raises(EmbedError, match="unsupported"):
        emb.embed_text(["a", "b"], "query")


def test_the_harness_turns_a_format_error_into_an_error_for_every_item() -> None:
    emb, _ = _emb(QWEN, feed=lambda fmt, x, kw: x)
    out = run.embed_texts(emb, {"q1": "a", "q2": "b"}, "query", dim=256, timeout=5)
    assert out.errors == {"q1": "unsupported", "q2": "unsupported"} and not out.vectors


def test_a_snapshot_that_is_not_the_pinned_commit_is_refused() -> None:
    emb, _ = _emb(QWEN, snapshot_name="main")
    with pytest.raises(ValueError, match="pinned"):
        emb.embed_text(["x"], "query")


def test_a_model_that_did_not_load_at_the_requested_dtype_is_refused() -> None:
    """jina's loader keeps its base in bf16 whatever it is passed: a result
    that says float32 must have been computed in float32."""
    emb, _ = _emb(JINA, st_dtype="torch.bfloat16")
    with pytest.raises(ValueError, match=r"dtype|bfloat16"):
        emb.embed_text(["x"], "query")


@pytest.mark.parametrize("mixed", [["torch.bfloat16", "torch.float32", "torch.float32"],
                                   ["torch.float32", "torch.float32", "torch.bfloat16"]])
def test_a_model_with_any_parameter_off_the_requested_dtype_is_refused(mixed) -> None:
    """jina's load leaves its base parameters in bf16 beside its fp32 LoRA:
    a check that only asks whether float32 is among the dtypes passes that."""
    emb, _ = _emb(JINA, st_dtype=mixed)
    with pytest.raises(ValueError, match="bfloat16"):
        emb.embed_text(["x"], "query")


@pytest.mark.parametrize("dtype", ["float16", "fp16", "half"])
def test_float16_is_refused_for_either_candidate(dtype: str) -> None:
    for fmt in (QWEN, JINA):
        with pytest.raises(ValueError, match="float16"):
            OfficialEmbedder(fmt, dtype=dtype)


# --- the run ----------------------------------------------------------------------------


def test_the_run_builds_one_official_candidate_by_label() -> None:
    emb = run.official_embedder("jina5omni", dtype="float32")
    assert isinstance(emb, OfficialEmbedder) and emb.fmt is JINA
    assert run.official_embedder("", dtype="float32") is None
    assert run.OFFICIAL_DIMS == {"qwen3vl": (2048, 256), "jina5omni": (1024, 256)}


@pytest.mark.parametrize("kw", [{"label": "qwen3", "dtype": "float32"},
                                {"label": "qwen3vl", "dtype": ""},
                                {"label": "qwen3vl", "dtype": "float16"}])
def test_the_run_refuses_an_unknown_candidate_or_dtype(kw: dict) -> None:
    with pytest.raises(ValueError):
        run.official_embedder(kw["label"], dtype=kw["dtype"])


def test_the_run_takes_one_candidate_at_a_time(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one candidate"):
        run.main(["--dsn", "postgresql+asyncpg://x/bakeoff", "--official", "qwen3vl",
                  "--gemma", "embeddinggemma-2", "--out", str(tmp_path)])


def test_per_query_ranks_name_the_first_relevant_rank_and_errors() -> None:
    labels = {"q1": frozenset({"b"}), "q2": frozenset({"z"}), "q3": frozenset({"a"})}
    ranks = run.first_relevant_ranks(
        {"q1": ["a", "b"], "q2": ["a", "b"], "q3": Errored("timeout")}, labels)
    assert ranks == {"q1": 2, "q2": None, "q3": "ERROR:timeout"}


# --- the real loader, against fake libraries ------------------------------------------


class _Tok:
    def decode(self, ids, skip_special_tokens=False):
        return "".join(chr(i) for i in ids)


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    def forward(self, *args, **kwargs):
        self.calls += 1
        return "out"


class _T:
    """The slice of a torch tensor the loader's forward wrapper uses."""

    def __init__(self, rows) -> None:
        self.rows = rows

    @property
    def shape(self):
        return (len(self.rows),)

    def __getitem__(self, i):
        if isinstance(i, _T):  # a boolean mask
            return _T([x for x, keep in zip(self.rows, i.rows, strict=True) if keep])
        r = self.rows[i]
        return _T(r) if isinstance(r, list) else r

    def __eq__(self, other):
        return _T([x == other for x in self.rows])

    def tolist(self):
        return list(self.rows)


class _LoadedST:
    def __init__(self, path, **kw) -> None:
        self.kw, self.cast = kw, None
        self.first = type("First", (), {})()
        self.first.model, self.first.tokenizer = _Model(), _Tok()

    def to(self, dtype):
        self.cast = dtype
        return self

    def __getitem__(self, i):
        return self.first


def _load_with_fakes(monkeypatch, fmt: OfficialFormat):
    import sys
    import types

    torch = types.ModuleType("torch")
    torch.float32 = "fake.float32"  # type: ignore[attr-defined]
    st_mod = types.ModuleType("sentence_transformers")
    st_mod.SentenceTransformer = _LoadedST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", st_mod)
    fed: list[str] = []
    st = adapters._load_official("/hub/snapshots/x", fmt, "float32", fed)
    return st, fed


def test_the_loader_casts_the_loaded_model_to_the_requested_dtype(monkeypatch) -> None:
    """Without the cast, jina's base stays bf16 under a result that says fp32."""
    st, _ = _load_with_fakes(monkeypatch, JINA)
    assert st.cast == "fake.float32"
    assert st.kw["model_kwargs"] == {"default_task": "retrieval", "modality": "vision",
                                     "dtype": "fake.float32"}
    assert st.kw["trust_remote_code"] is True


def test_the_loader_records_every_sequence_the_model_forward_receives(monkeypatch) -> None:
    """The format check reads what this wrapper records: a model whose
    forward is not wrapped would be checked against nothing."""
    st, fed = _load_with_fakes(monkeypatch, JINA)
    model = st[0].model
    ids = _T([[ord(c) for c in "Query: ab"], [ord(c) for c in "Query: a!"]])
    mask = _T([[1] * 9, [1] * 8 + [0]])
    assert model.forward(input_ids=ids, attention_mask=mask) == "out"
    assert model.calls == 1
    assert fed == ["Query: ab", "Query: a"]
    model.forward(input_ids=_T([[ord("x")] * 2]), attention_mask=_T([[0, 1]]))
    assert fed[-1].startswith("<left-padded")
