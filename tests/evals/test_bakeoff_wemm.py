"""WeMM-Embedding-2B through llama-server: the input the model was trained on,
checked against the server that embeds it. Every check fails closed: a server
that would embed anything other than WeMM's own token sequence is an
`unsupported` ERROR, never a vector.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals.retrieval_bakeoff import adapters, run
from evals.retrieval_bakeoff.adapters import EmbedError, LlamaServerEmbedder

PID = 4242
GOOD_CMDLINE = ["/usr/local/lib/ollama/llama-server", "-m", "m.gguf", "--mmproj", "p.gguf",
                "--embedding", "--pooling", "last", "--host", "127.0.0.1", "--port", "11439"]
SPECIALS = r"<\|im_start\|>|<\|im_end\|>|<__media[^>]*>"


class FakeServer:
    """llama-server's /props, /tokenize and /embedding. /tokenize splits on
    the special tokens, then on newlines, the way the real one pieces them;
    `add_special` appends `tail` and prepends `head` (b42b7e6d3 with WeMM's
    GGUF: no BOS, `<embedding>` once). Records every string it tokenized and
    every prompt it embedded."""

    def __init__(self, *, tail: tuple[str, ...] = ("<embedding>",), head: tuple[str, ...] = (),
                 marker: str = "<__media_abc__>", special: bool = True,
                 merge_role: bool = False) -> None:
        self.tail, self.head, self.marker = list(tail), list(head), marker
        self.special, self.merge_role = special, merge_role
        self.tokenized: list[str] = []
        self.embedded: list[dict] = []

    def pieces(self, s: str) -> list[str]:
        out: list[str] = []
        for part in re.split(f"({SPECIALS})", s):
            if re.fullmatch(SPECIALS, part) and not self.special:
                out.extend(["<", part[1:-1], ">"])  # a GGUF that does not parse them
            elif re.fullmatch(SPECIALS, part):
                out.append(part)
            else:
                out.extend(p for p in re.split(r"(\n)", part) if p)
        if self.merge_role and out[1:3] == ["user", "\n"]:
            out[1:3] = ["user\n"]  # a vocabulary with the role and its newline as one token
        return out

    def __call__(self, method, url, body, timeout):
        if url.endswith("/props"):
            return {"media_marker": self.marker}
        if url.endswith("/tokenize"):
            self.tokenized.append(body["content"])
            p = self.pieces(body["content"])
            if body["add_special"]:
                p = [*self.head, *p, *self.tail]
            return {"tokens": [{"id": i, "piece": x} for i, x in enumerate(p)]}
        self.embedded.append(body["content"])
        return {"embedding": [1.0] + [0.0] * 2047}


def _llama(server: FakeServer, *, cmdline: list[str] | None = GOOD_CMDLINE,
           pid: int | None = PID, **kw) -> LlamaServerEmbedder:
    return LlamaServerEmbedder(
        "wemm-embedding-2b:Q4_K_M", native_dim=2048, transport=server, server_pid=pid,
        read_cmdline=lambda p: list(cmdline) if cmdline is not None else None, **kw)


def _img(tmp_path: Path) -> Path:
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpeg")
    return img


# --- the request shape --------------------------------------------------------------


def test_llama_server_sends_the_recorded_media_shape(tmp_path: Path) -> None:
    server = FakeServer()
    emb = _llama(server)
    vec = emb.embed_media([_img(tmp_path)])
    assert len(vec[0]) == 2048
    [content] = server.embedded
    assert content["multimodal_data"] == ["anBlZw=="]
    # WeMM's own template: no newline after the role before a leading image;
    # the server appends `<embedding>` itself.
    assert content["prompt_string"] == "<|im_start|>user<__media_abc__><|im_end|>"


def test_llama_server_falls_back_to_the_default_marker_only_when_props_names_none(
        tmp_path: Path) -> None:
    """b42b7e6d3 randomises the marker per start and refuses the default
    ("number of media markers in text (0) does not match number of bitmaps");
    an older server names none in /props."""
    published = _llama(FakeServer())
    published.embed_media([_img(tmp_path)])
    assert published.provenance()["media_marker_source"] == "/props"
    older = FakeServer(marker="")
    emb = _llama(older)
    emb.embed_media([_img(tmp_path)])
    assert older.embedded[0]["prompt_string"] == "<|im_start|>user<__media__><|im_end|>"
    assert emb.provenance()["media_marker_source"] == "default"


@pytest.mark.parametrize("role", ["query", "document"])
def test_llama_server_wraps_wemm_text_exactly_as_its_template_does(role: str) -> None:
    """tencent/WeMM-Embedding-2B renders a text input as one user turn, then
    `<embedding>` straight after `<|im_end|>` (its normalizer strips that
    newline), and has no query or document instruction (`prompts` is {}).
    The server appends `<embedding>`, so the prompt stops at `<|im_end|>`."""
    server = FakeServer()
    _llama(server).embed_text(["兩碗麵", "打風"], role)
    assert server.embedded == [{"prompt_string": "<|im_start|>user\n兩碗麵<|im_end|>"},
                               {"prompt_string": "<|im_start|>user\n打風<|im_end|>"}]


# --- 3. the server must pool the last token of an embedding run --------------------


def test_the_server_cmdline_is_recorded() -> None:
    emb = _llama(FakeServer())
    emb.embed_text(["打風"], "query")
    assert emb.provenance()["server_cmdline"] == GOOD_CMDLINE


@pytest.mark.parametrize("cmdline", [
    [a for a in GOOD_CMDLINE if a not in ("--pooling", "last")],      # model default pooling
    [*GOOD_CMDLINE[:6], "--pooling", "mean", *GOOD_CMDLINE[8:]],       # another pooling
    [a for a in GOOD_CMDLINE if a != "--embedding"],                    # not an embedding run
    None,                                                               # /proc unreadable
])
def test_a_server_not_pooling_last_or_not_embedding_is_refused(
        tmp_path: Path, cmdline: list[str] | None) -> None:
    server = FakeServer()
    emb = _llama(server, cmdline=cmdline)
    for call in (lambda: emb.embed_text(["x"], "query"), lambda: emb.embed_media([_img(tmp_path)])):
        with pytest.raises(EmbedError) as e:
            call()
        assert e.value.kind == "unsupported"
    assert server.embedded == []


def test_a_server_without_a_pid_is_refused() -> None:
    server = FakeServer()
    with pytest.raises(EmbedError) as e:
        _llama(server, pid=None).embed_text(["x"], "query")
    assert e.value.kind == "unsupported"
    assert server.embedded == []


@pytest.mark.parametrize("form", [["--pooling=last", "--embeddings"], ["--pooling", "last",
                                                                       "--embedding"]])
def test_both_spellings_of_the_flags_are_accepted(form: list[str]) -> None:
    emb = _llama(FakeServer(), cmdline=["llama-server", "-m", "m.gguf", *form, "--port", "11439"])
    assert len(emb.embed_text(["x"], "query")) == 1


def test_a_pid_whose_server_listens_on_another_port_is_refused() -> None:
    cmd = [a if a != "11439" else "8081" for a in GOOD_CMDLINE]
    with pytest.raises(EmbedError) as e:
        _llama(FakeServer(), cmdline=cmd).embed_text(["x"], "query")
    assert e.value.kind == "unsupported"


# --- 4. newlines the way WeMM's tokenizer.json normalises them ---------------------


@pytest.mark.parametrize(("raw", "normalised"), [
    # From the official tokenizer.json (sha256 40e444c7..., tokenizers 0.22.2):
    # Replace(Regex("\n$"), "") with Onig's `$` = end of line,
    # then NFC, applied to the text between special tokens.
    ("a\n\nb", "a\nb"),
    ("a\n", "a"),
    ("a\n\n\nb\n\n", "a\nb"),
    ("\nfoo", "\nfoo"),
    ("user\n\nfoo", "user\nfoo"),
    ("x\r\n", "x\r"),
    ("é", "é"),
])
def test_the_normaliser_matches_wemms_tokenizer(raw: str, normalised: str) -> None:
    assert adapters.wemm_normalise(raw) == normalised


@pytest.mark.parametrize(("text", "prompt"), [
    # The official tokenizer gives these pairs identical ids
    # ([248045, 846, 198, 64, 198, 65, 248046, 248077] for the first;
    # [248045, 846, 198, 64, 248046, 248077] for the second; 7724 is "foo").
    ("a\n\nb", "<|im_start|>user\na\nb<|im_end|>"),
    ("a\n", "<|im_start|>user\na<|im_end|>"),
    ("\nfoo", "<|im_start|>user\nfoo<|im_end|>"),
])
def test_the_embedded_prompt_is_normalised_like_the_official_tokenizer(
        text: str, prompt: str) -> None:
    server = FakeServer()
    _llama(server).embed_text([text], "query")
    assert server.embedded == [{"prompt_string": prompt}]


# --- 5. /tokenize: the full sequence of exactly what is embedded -------------------


def test_every_embedded_text_is_tokenized_first_exactly_as_sent() -> None:
    server = FakeServer()
    _llama(server).embed_text(["打風", "兩碗麵", "打風"], "document")
    sent = [b["prompt_string"] for b in server.embedded]
    assert set(sent) <= set(server.tokenized)
    # each distinct prompt once bare, once with add_special; the repeat is cached
    assert server.tokenized.count("<|im_start|>user\n打風<|im_end|>") == 2
    assert sent == ["<|im_start|>user\n打風<|im_end|>", "<|im_start|>user\n兩碗麵<|im_end|>",
                    "<|im_start|>user\n打風<|im_end|>"]


def test_the_image_wrapper_is_tokenized_once_exactly_as_sent(tmp_path: Path) -> None:
    server = FakeServer()
    emb = _llama(server)
    emb.embed_media([_img(tmp_path)])  # the harness sends one image per call
    emb.embed_media([_img(tmp_path)])
    prompt = "<|im_start|>user<__media_abc__><|im_end|>"
    assert [b["prompt_string"] for b in server.embedded] == [prompt, prompt]
    assert server.tokenized.count(prompt) == 2  # once bare, once with add_special
    assert emb.provenance()["template_check"]["media"] == ["<|im_start|>", "user",
                                                           "<__media_abc__>", "<|im_end|>",
                                                           "<embedding>"]


def test_a_server_that_reads_the_special_tokens_as_text_is_refused(tmp_path: Path) -> None:
    server = FakeServer(special=False)
    emb = _llama(server)
    for call in (lambda: emb.embed_text(["x"], "query"), lambda: emb.embed_media([_img(tmp_path)])):
        with pytest.raises(EmbedError) as e:
            call()
        assert e.value.kind == "unsupported"
    assert server.embedded == []


def test_a_server_whose_vocabulary_merges_the_role_and_its_newline_is_refused() -> None:
    """Not WeMM's tokenizer: the turn would embed as different tokens."""
    server = FakeServer(merge_role=True)
    with pytest.raises(EmbedError) as e:
        _llama(server).embed_text(["x"], "query")
    assert e.value.kind == "unsupported"
    assert server.embedded == []


@pytest.mark.parametrize(("tail", "head"), [
    ((), ()),                              # nothing appended: pools `<|im_end|>`
    (("<embedding>", "<embedding>"), ()),  # appended twice
    (("<embedding>",), (",",)),            # a BOS in front
    (("<embedding>", "\n"), ()),           # something after the embedding token
])
def test_a_server_sequence_other_than_the_prompt_plus_one_embedding_is_refused(
        tmp_path: Path, tail: tuple[str, ...], head: tuple[str, ...]) -> None:
    server = FakeServer(tail=tail, head=head)
    emb = _llama(server)
    for call in (lambda: emb.embed_text(["x"], "query"), lambda: emb.embed_media([_img(tmp_path)])):
        with pytest.raises(EmbedError) as e:
            call()
        assert e.value.kind == "unsupported"
    assert server.embedded == []


# --- 7. server memory and the --wemm path of run.main ------------------------------


def test_server_memory_reads_the_high_water_mark_from_proc(tmp_path: Path) -> None:
    (tmp_path / str(PID)).mkdir()
    (tmp_path / str(PID) / "status").write_text(
        "Name:\tllama-server\nVmPeak:\t 9999999 kB\nVmHWM:\t 3316516 kB\nVmRSS:\t 2794548 kB\n")
    emb = _llama(FakeServer(), proc=tmp_path)
    assert emb.server_memory() == {"peak_rss_kib": 3316516, "rss_kib": 2794548}


def test_server_memory_is_none_without_a_pid_or_a_status_file(tmp_path: Path) -> None:
    assert _llama(FakeServer(), pid=None, proc=tmp_path).server_memory() is None
    assert _llama(FakeServer(), proc=tmp_path).server_memory() is None


class _Dense:
    """A text embedder that answers one unit vector per text, hot in the first
    64 components so every Matryoshka head keeps it."""

    def __init__(self, name: str, dim: int) -> None:
        self.name, self.native_dim = name, dim

    def embed_text(self, texts, role):
        out = []
        for t in texts:
            v = [0.0] * self.native_dim
            v[sum(map(ord, t)) % 64] = 1.0
            out.append(v)
        return out

    def embed_media(self, paths):
        raise AssertionError("no media on a text slice")

    def provenance(self):
        return {"model": self.name}


def test_main_runs_wemm_through_llama_server_with_its_provenance(
        monkeypatch, tmp_path: Path) -> None:
    made: list[dict] = []

    class FakeLlama(_Dense):
        def __init__(self, name, **kw):
            super().__init__(name, kw["native_dim"])
            made.append({"name": name, **kw})

    monkeypatch.setattr(run, "LlamaServerEmbedder", FakeLlama)
    monkeypatch.setattr(run, "OllamaEmbedder", lambda name, **kw: _Dense(name, kw["native_dim"]))
    monkeypatch.setattr(run, "_trigram", lambda dsn, s: run.ArmRun({q: [] for q in s.queries}))
    monkeypatch.setattr(run, "_bm25", lambda s: run.ArmRun({q: [] for q in s.queries}))
    monkeypatch.setattr(run, "_unload", lambda m: True)
    monkeypatch.setattr(run, "read_meminfo", lambda: {"MemAvailable": 20 * run.GIB_KIB,
                                                      "SwapTotal": 0, "SwapFree": 0})
    rc = run.main(["--dsn", "postgresql+asyncpg://unused/bakeoff", "--out", str(tmp_path),
                   "--slices", "cantonese", "--wemm", "http://127.0.0.1:11439",
                   "--wemm-pid", "4242", "--wemm-build", "b1-x",
                   "--wemm-model-sha256", "a" * 64, "--wemm-mmproj-sha256", "b" * 64])
    assert rc == 0
    [written] = tmp_path.glob("*-stage2-wemm-cantonese.json")
    result = json.loads(written.read_text())
    assert {"dense:wemm@2048", "dense:wemm@256", "fused:trigram+wemm@2048",
            "fused:bm25+wemm@256"} <= set(result["arms"])
    assert "wemm-embedding-2b:Q4_K_M" in result["provenance"]["stage"]
    [kw] = made
    assert (kw["server_pid"], kw["build"], kw["model_sha256"], kw["mmproj_sha256"],
            kw["base_url"]) == (4242, "b1-x", "a" * 64, "b" * 64, "http://127.0.0.1:11439")


# --- 2. per-query evidence ---------------------------------------------------------


def test_every_arm_records_the_rank_of_each_querys_first_relevant_item() -> None:
    the_set = run.load_set(Path(run.SET_DIR), "cantonese")
    qids = list(the_set.queries)
    pool = list(the_set.pool)
    q0, q1, q2 = qids[:3]
    rel0 = sorted(the_set.relevant[q0])[0]
    irrelevant = [p for p in pool if p not in the_set.relevant[q0]][:3]
    rankings: run.Rankings = {q: [] for q in qids}
    rankings[q0] = [*irrelevant, rel0]
    rankings[q1] = run.Errored("timeout")
    arms = {"lexical:trigram": run.ArmRun(rankings), "lexical:bm25": run.ArmRun(rankings)}
    result = run.evaluate(the_set, arms, lexical_arms=("lexical:trigram", "lexical:bm25"),
                          provenance={})
    per = result["arms"]["lexical:trigram"]["per_query"]
    assert set(per) == set(qids)
    assert per[q0] == 4
    assert per[q1] == "ERROR:timeout"
    assert per[q2] is None
    blob = json.dumps(per, ensure_ascii=False)
    for q in the_set.queries.values():
        assert q.text not in blob


# --- the official tokenizer, the media marker and the result merge -----------------

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "wemm_normaliser_golden.json"


def test_the_prompt_matches_wemms_official_tokenizer_on_a_fuzz_corpus() -> None:
    """Goldens from tokenizer.json and the sentence-transformers template at the
    pinned revision (evals/retrieval_bakeoff/wemm_normaliser_golden.py): each
    expected prompt re-encodes to the official ids with the normaliser off, so
    matching it is matching the model's input."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["tokenizer_sha256"] == adapters.WEMM_TOKENIZER_SHA256
    wrong = [(c["text"], adapters.wemm_prompt(LlamaServerEmbedder.WRAP.format(x=c["text"])),
              c["prompt"]) for c in golden["cases"]
             if adapters.wemm_prompt(LlamaServerEmbedder.WRAP.format(x=c["text"])) != c["prompt"]]
    assert not wrong, f"{len(wrong)} of {len(golden['cases'])} differ, e.g. {wrong[:3]}"


def test_the_normaliser_splits_on_every_added_token_of_the_tokenizer() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(adapters.WEMM_ADDED_TOKENS) == sorted(golden["added_tokens"])
    # what lets the split try them in any order
    tokens = adapters.WEMM_ADDED_TOKENS
    assert not [(a, b) for a in tokens for b in tokens if a != b and a in b]


def test_an_image_prompt_with_a_newline_after_the_role_is_refused(tmp_path: Path) -> None:
    """WeMM's template puts no newline between the role and a leading image. A
    marker that starts with one would put it there."""
    server = FakeServer(marker="\n<__media_abc__>")
    with pytest.raises(EmbedError) as e:
        _llama(server).embed_media([_img(tmp_path)])
    assert e.value.kind == "unsupported"
    assert server.embedded == []


RESULTS = Path(run.SET_DIR) / "results"
FP32 = [RESULTS / "20261015T123946Z-stage2-wemm-fp32-cantonese.json",
        RESULTS / "20261015T123946Z-stage2-wemm-fp32-names.json"]
Q4 = {s: RESULTS / f"20261015T125057Z-stage2-wemm-{s}.json"
      for s in ("text_image", "cantonese", "names")}


def test_fp32_text_with_q4_images_re_derives_replace_from_the_committed_files() -> None:
    """Combination B, from the result files alone: fp32 on the text slices,
    Q4 on text_image (fp32 images were not run), one candidate by an explicit
    rename. Each arm's precision stays in its file's provenance."""
    from evals.retrieval_bakeoff.decide import Verdict, decide, scores_from_results

    r = scores_from_results([*FP32, Q4["text_image"]], rename={"wemm-fp32": "wemm"})
    assert not any("wemm-fp32" in a for a in r)
    assert set(r["dense:wemm@2048"]) == {"text_image", "cantonese", "names"}
    d = decide(r)
    assert (d.verdict, d.winner, d.wins) == (Verdict.REPLACE, "dense:wemm@2048", ("text_image",))


def test_combination_a_re_derives_replace_from_the_committed_files() -> None:
    from evals.retrieval_bakeoff.decide import Verdict, decide, scores_from_results

    d = decide(scores_from_results(Q4.values()))
    assert (d.verdict, d.winner) == (Verdict.REPLACE, "dense:wemm@2048")


def test_a_rename_onto_an_arm_already_measured_on_that_slice_is_refused() -> None:
    """fp32 and Q4 Cantonese under one name would overwrite each other."""
    from evals.retrieval_bakeoff.decide import scores_from_results

    with pytest.raises(ValueError, match="dense:wemm@2048"):
        scores_from_results([FP32[0], Q4["cantonese"]], rename={"wemm-fp32": "wemm"})
