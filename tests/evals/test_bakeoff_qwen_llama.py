"""Qwen3-VL-Embedding-2B Q4_K_M through llama-server: every prompt's token ids
are the official tokenizer's for the official sequence, or the item is an
`unsupported` ERROR before any vector is asked for.

The official ids are golden: `tests/fixtures/qwen3vl_token_golden.json` was
generated from the pinned tokenizer.json (its sha256 is in the file) on the
deploy host, per run of text between special tokens, and the generator
asserted that those runs concatenate to each whole sequence's ids.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pytest

from evals.retrieval_bakeoff import qwen_llama
from evals.retrieval_bakeoff.adapters import EmbedError
from evals.retrieval_bakeoff.qwen_llama import (
    EOT,
    QWEN3VL_Q4_GGUF,
    QWEN3VL_TOKENIZER_SHA256,
    QwenLlamaServerEmbedder,
    official_sequence,
)

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                     / "qwen3vl_token_golden.json").read_text(encoding="utf-8"))
SPECIAL = GOLDEN["special"]
EOT_ID = SPECIAL[EOT]
MARKER = "<__media_abc__>"
MARKER_ID = 999_999
_SPLIT = re.compile("(" + "|".join(re.escape(s) for s in [*SPECIAL, MARKER]) + ")")
PID = 4242
MODEL = "/models/" + QWEN3VL_Q4_GGUF["model"]
MMPROJ = "/models/" + QWEN3VL_Q4_GGUF["mmproj"]
CMDLINE = ["/usr/local/lib/ollama/llama-server", "-m", MODEL, "--mmproj", MMPROJ,
           "--embedding", "--pooling", "last", "--port", "11441"]
HASHES = {MODEL: QWEN3VL_Q4_GGUF["model_sha256"], MMPROJ: QWEN3VL_Q4_GGUF["mmproj_sha256"]}


def _ids(s: str, *, nfc: bool) -> list[int]:
    """Tokenize the way the official tokenizer does (`nfc`: its normaliser
    runs on each run between special tokens) or the way llama-server does (no
    normaliser). A run the golden file does not hold gets ids no real run
    has, so it can never pass for an official one."""
    out: list[int] = []
    for part in _SPLIT.split(s):
        if part in SPECIAL:
            out.append(SPECIAL[part])
        elif part == MARKER:
            out.append(MARKER_ID)
        elif part:
            run = unicodedata.normalize("NFC", part) if nfc else part
            out.extend(GOLDEN["segments"].get(run) or [10**7 + b for b in run.encode()])
    return out


def official_ids(s: str) -> list[int]:
    return _ids(s, nfc=True)


class FakeServer:
    """llama-server's /props, /tokenize and /embedding. With `add_special` it
    appends `appends` end tokens (this GGUF on b42b7e6d3: one). Records every
    prompt it embedded."""

    def __init__(self, *, appends: int = 1) -> None:
        self.appends = appends
        self.embedded: list[dict] = []

    def __call__(self, method, url, body, timeout):
        if url.endswith("/props"):
            return {"media_marker": MARKER}
        if url.endswith("/tokenize"):
            ids = _ids(body["content"], nfc=False)
            if body["add_special"]:
                ids += [EOT_ID] * self.appends
            return {"tokens": ids}
        if url.endswith("/embedding"):
            self.embedded.append(body["content"])
            return [{"embedding": [[1.0, 0.0, 0.0, 0.0]]}]
        raise AssertionError(url)


def embedder(server: FakeServer, *, cmdline=CMDLINE, hashes=HASHES, cls=QwenLlamaServerEmbedder):
    return cls("qwen3-vl-embedding-2b:Q4_K_M", native_dim=4, base_url="http://127.0.0.1:11441",
               server_pid=PID, transport=server, read_cmdline=lambda pid: list(cmdline),
               official_ids=official_ids, hash_file=lambda p: hashes[str(p)])


def test_the_golden_runs_rebuild_every_official_sequence():
    """The fake tokenizer IS the official one on these inputs: its ids for
    each whole sequence equal the ids the real tokenizer gave."""
    assert GOLDEN["tokenizer_sha256"] == QWEN3VL_TOKENIZER_SHA256
    assert GOLDEN["sequences"]
    for s in GOLDEN["sequences"]:
        assert official_ids(official_sequence(s["text"], s["role"])) == s["ids"]


@pytest.mark.parametrize("role", ["query", "document"])
def test_every_golden_text_embeds_as_the_official_sequence(role):
    server = FakeServer()
    texts = [s["text"] for s in GOLDEN["sequences"] if s["role"] == role]
    assert len(embedder(server).embed_text(texts, role)) == len(texts)
    for sent in server.embedded:
        assert sent["prompt_string"].endswith("<|im_start|>assistant\n")
        assert EOT not in sent["prompt_string"]
    assert all(qwen_llama.QWEN3VL_QUERY_INSTRUCTION in e["prompt_string"]
               for e in server.embedded) == (role == "query")


def test_a_text_is_nfc_normalised_before_the_server_sees_it():
    server = FakeServer()
    embedder(server).embed_text(["café at noon"], "document")
    assert "café at noon" in server.embedded[0]["prompt_string"]


def test_a_server_that_appends_the_end_token_twice_is_an_error():
    server = FakeServer(appends=2)
    with pytest.raises(EmbedError) as e:
        embedder(server).embed_text(["two bowls of noodles"], "query")
    assert e.value.kind == "unsupported" and not server.embedded


def test_a_prompt_that_writes_the_end_token_itself_is_an_error():
    """The server appends one: writing it too would pool a doubled end token."""

    class Doubled(QwenLlamaServerEmbedder):
        def prompt_for(self, text, role):
            return super().prompt_for(text, role) + EOT

    server = FakeServer()
    with pytest.raises(EmbedError, match="unsupported"):
        embedder(server, cls=Doubled).embed_text(["two bowls of noodles"], "query")
    assert not server.embedded


def test_a_server_that_appends_no_end_token_is_an_error():
    server = FakeServer(appends=0)
    with pytest.raises(EmbedError, match="unsupported"):
        embedder(server).embed_text(["two bowls of noodles"], "document")


def test_a_query_with_the_document_instruction_is_an_error():
    class WrongRole(QwenLlamaServerEmbedder):
        def prompt_for(self, text, role):
            return super().prompt_for(text, "document")

    with pytest.raises(EmbedError, match="unsupported"):
        embedder(FakeServer(), cls=WrongRole).embed_text(["two bowls of noodles"], "query")


def test_the_image_prompt_is_head_marker_tail_with_one_end_token(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpeg")
    server = FakeServer()
    embedder(server).embed_media([img])
    prompt = server.embedded[0]["prompt_string"]
    assert prompt == qwen_llama.head("image") + MARKER + qwen_llama.TAIL
    with pytest.raises(EmbedError, match="unsupported"):
        embedder(FakeServer(appends=2)).embed_media([img])


@pytest.mark.parametrize(("cmdline", "hashes"), [
    ([a for a in CMDLINE if a not in ("--pooling", "last")], HASHES),
    (CMDLINE, {**HASHES, MODEL: "0" * 64}),
    (CMDLINE, {**HASHES, MMPROJ: "0" * 64}),
    ([("models/x.gguf" if a == MODEL else a) for a in CMDLINE], HASHES),
], ids=["no-pooling-last", "other-model", "other-mmproj", "relative-model-path"])
def test_a_server_not_serving_the_pinned_files_last_pooled_is_an_error(cmdline, hashes):
    server = FakeServer()
    with pytest.raises(EmbedError, match="unsupported"):
        embedder(server, cmdline=cmdline, hashes=hashes).embed_text(["x"], "query")
    assert not server.embedded


def test_provenance_records_the_hashes_of_the_files_served():
    e = embedder(FakeServer())
    e.embed_text(["two bowls of noodles"], "query")
    p = e.provenance()
    assert p["model_sha256"] == QWEN3VL_Q4_GGUF["model_sha256"]
    assert p["mmproj_sha256"] == QWEN3VL_Q4_GGUF["mmproj_sha256"]
    assert p["gguf"]["revision"].startswith("ac21c878")
    assert p["template_check"]["texts"] == 1


def test_a_tokenizer_file_with_another_hash_is_refused(tmp_path):
    f = tmp_path / "tokenizer.json"
    f.write_text("{}")
    with pytest.raises(ValueError, match=r"is not tokenizer\.json"):
        qwen_llama.official_tokenizer(f)
