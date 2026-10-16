"""Qwen3-VL-Embedding-2B as a Q4_K_M GGUF through llama-server.

A subclass of `adapters.LlamaServerEmbedder` (the server's command line,
its media marker, one pooled row per call), in the input format the model's
own files define (Qwen/Qwen3-VL-Embedding-2B at `9f2f7e71`): a system turn
holding the instruction, the user turn, the generation prompt, then
`<|endoftext|>`, the token that is pooled. QUERIES get the vendor's retrieval
instruction, documents and images the default (QwenLM/Qwen3-VL-Embedding's
retrieval usage). The text is NFC-normalised first: that is the normaliser
of the official tokenizer.json, and llama-server applies none.

llama-server appends `<|endoftext|>` itself (this GGUF), so the
prompt stops at `assistant\\n`; one written into the prompt as well would be
pooled from a doubled end token. Nothing about the sequence is assumed, and
anything else is an `unsupported` ERROR before a vector is asked for:

- the server's command line carries `--embedding --pooling last` on this
  port (the parent class), and the `-m` and `--mmproj` files it names hash
  to the pinned GGUF and projector (`QWEN3VL_Q4_GGUF`);
- every text prompt, exactly as sent, is tokenized by the server with
  `add_special`, and its token ids must EQUAL the official tokenizer's ids
  for the official sequence, with exactly one `<|endoftext|>`, last;
- the image prompt is the head, the server's media marker and the tail: its
  ids must be the official head's, the marker's, and the official tail's
  plus exactly one `<|endoftext|>`.

The official ids come from the pinned tokenizer.json (`official_tokenizer`,
which refuses a file that does not hash to `QWEN3VL_TOKENIZER_SHA256`).
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from evals.retrieval_bakeoff.adapters import EmbedError, LlamaServerEmbedder
from nora.domain.embedder_input import (
    DOCUMENT_INSTRUCTION,
    EOT,
    QUERY_INSTRUCTION,
    TAIL,
    head,
    image_prompt,
    official_sequence,
    text_prompt,
)

__all__ = [
    "EOT", "QWEN3VL_INSTRUCTION", "QWEN3VL_Q4_GGUF", "QWEN3VL_QUERY_INSTRUCTION",
    "QWEN3VL_REVISION", "QWEN3VL_TOKENIZER_SHA256", "QwenLlamaServerEmbedder", "head",
    "official_sequence", "official_tokenizer",
]

QWEN3VL_REPO = "Qwen/Qwen3-VL-Embedding-2B"
QWEN3VL_REVISION = "9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda"
#: tokenizer.json at that revision (tokenizers 0.22.2).
QWEN3VL_TOKENIZER_SHA256 = "def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a"
#: The instructions, from the one formatter production uses too
#: (`nora.domain.embedder_input`), so the bake-off measures what ships.
QWEN3VL_INSTRUCTION = DOCUMENT_INSTRUCTION
QWEN3VL_QUERY_INSTRUCTION = QUERY_INSTRUCTION
#: The GGUF measured, as downloaded and hashed on the deploy host.
QWEN3VL_Q4_GGUF = {
    "repo": "mradermacher/Qwen3-VL-Embedding-2B-GGUF",
    "revision": "ac21c8784ed86e50391e32ed945f21e42140b1f6",
    "model": "Qwen3-VL-Embedding-2B.Q4_K_M.gguf",
    "model_sha256": "7812c91cd1e03ab4d42fdc21264b9f9d116f8b57b350ac186bd9ec07546984da",
    "mmproj": "Qwen3-VL-Embedding-2B.mmproj-f16.gguf",
    "mmproj_sha256": "521c481325245b7cb043fd33cfd891abf31c1925f504235fb8ca9d6b1dd5eb9d",
}
#: A string -> its official token ids, `add_special_tokens=False`.
OfficialIds = Callable[[str], list[int]]


def _sha256_stream(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def official_tokenizer(path: Path) -> OfficialIds:
    """The pinned tokenizer.json's ids; a file with any other hash is refused."""
    if _sha256_stream(path) != QWEN3VL_TOKENIZER_SHA256:
        raise ValueError(f"{path} is not tokenizer.json at {QWEN3VL_REVISION[:8]}")
    # ponytail: imported on first use; the bake-off venv installs it, the gate never does.
    from tokenizers import Tokenizer  # type: ignore[import-not-found]

    tok = Tokenizer.from_file(str(path))
    return lambda s: list(tok.encode(s, add_special_tokens=False).ids)


class QwenLlamaServerEmbedder(LlamaServerEmbedder):
    """See the module docstring. `official_ids` is the pinned tokenizer's
    (`official_tokenizer`); `hash_file` hashes a model file the
    server's command line names."""

    def __init__(self, name: str, *, official_ids: OfficialIds,
                 hash_file: Callable[[Path], str] = _sha256_stream, **kw: Any) -> None:
        super().__init__(name, dtype="Q4_K_M (projector F16)", **kw)
        self._official = official_ids
        self._hash_file = hash_file
        self._files: dict[str, str] | None = None
        self._image_prompt: str | None = None

    def _server_checked(self) -> None:
        super()._server_checked()
        if self._files is not None:
            return
        argv = self._cmdline or []

        def value(*flags: str) -> str | None:
            for i, a in enumerate(argv):
                if a in flags and i + 1 < len(argv):
                    return argv[i + 1]
            return None

        files = {}
        for key, flags in (("model", ("-m", "--model")), ("mmproj", ("--mmproj",))):
            named = value(*flags)
            if not named or not Path(named).is_absolute():
                raise EmbedError("unsupported", f"the server names no absolute {key} file")
            digest = self._hash_file(Path(named))
            if digest != QWEN3VL_Q4_GGUF[f"{key}_sha256"]:
                raise EmbedError("unsupported", f"{named} hashes to {digest[:12]}, "
                                                f"not the pinned {key}")
            files[f"{key}_sha256"] = digest
        self._files = files

    def _ids(self, prompt: str, add_special: bool) -> list[int]:
        resp = self._call("POST", f"{self._base}/tokenize", {
            "content": prompt, "add_special": add_special, "parse_special": True,
        }, self._timeout)
        return [t if isinstance(t, int) else t["id"] for t in resp["tokens"]]

    def prompt_for(self, text: str, role: str) -> str:
        return text_prompt(text, role)

    def _text_prompt_checked(self, text: str, role: str) -> str:
        prompt = self.prompt_for(text, role)
        if prompt not in self._checked:
            full = self._ids(prompt, True)
            want = self._official(official_sequence(text, role))
            if full != want:
                raise EmbedError("unsupported", f"server ids are not the official sequence's "
                                                f"({len(full)} vs {len(want)} tokens)")
            self._checked.add(prompt)
        return prompt

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        self._server_checked()
        prompts = [self._text_prompt_checked(t, role) for t in texts]
        return [self._one({"prompt_string": p}) for p in prompts]

    def image_prompt(self) -> str:
        if self._image_prompt is None:
            marker = self.media_marker()
            prompt = image_prompt(marker)
            full = self._ids(prompt, True)
            pre, post = self._official(head("image")), self._official(TAIL + EOT)
            if full != [*pre, *self._ids(marker, False), *post]:
                raise EmbedError("unsupported", "image prompt is not the official head, "
                                                "the marker and the tail with one end token")
            self._media_pieces = [str(len(pre)), "<marker>", str(len(post))]
            self._image_prompt = prompt
        return self._image_prompt

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]:
        self._server_checked()
        prompt = self.image_prompt()
        return [self._one({"prompt_string": prompt,
                           "multimodal_data": [base64.b64encode(p.read_bytes()).decode()]})
                for p in paths]

    def provenance(self) -> dict[str, Any]:
        out = super().provenance()
        out.update({
            "official": {"repo": QWEN3VL_REPO, "revision": QWEN3VL_REVISION,
                         "tokenizer_sha256": QWEN3VL_TOKENIZER_SHA256},
            "gguf": dict(QWEN3VL_Q4_GGUF), "served_files_sha256": self._files,
            "model_sha256": (self._files or {}).get("model_sha256"),
            "mmproj_sha256": (self._files or {}).get("mmproj_sha256"),
            "wrap": {r: official_sequence("{x}", r) for r in ("query", "document")},
            "media_wrap": head("image") + "<marker>" + TAIL + EOT,
            "server_appends": EOT, "normaliser": "NFC (the official tokenizer.json's)",
            "template_check": {"checked": "/tokenize ids == official tokenizer.json ids, every "
                                          "text prompt as sent; image: head, marker, tail",
                               "texts": len(self._checked), "media": self._media_pieces},
        })
        return out
