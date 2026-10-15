"""The bake-off's embedders: one adapter per serving path (design/unified-memory/20).

Every adapter has the same three calls: `embed_text(texts, role)`,
`embed_media(paths)` and `provenance()`. An adapter returns raw vectors or
raises; the harness (`run.embed_texts`, `run.embed_media`) turns every raise
and every bad vector into an ERROR for its items. What a model needs to be
compared fairly lives HERE, never in a caller:

- **Gemma 2's task prefixes** (`GEMMA2_PREFIX`): its card requires
  `task: search result | query: ` on a query and `title: none | text: ` on a
  document, and nothing on media. A caller that forgot one would lose the
  comparison without any error.
- **No float16**: the card says float16 "can silently produce NaN or degraded
  embeddings", so an adapter built with it refuses at construction.

The serving paths, as probed on the deploy host 2026-10-15:

- `OllamaEmbedder`: bge-m3 and any other tag Ollama serves. Text only:
  Ollama 0.35 answers an `images` field on /api/embed with the text-only
  vector and refuses an object input, so `embed_media` refuses.
- `LlamaServerEmbedder`: a native llama-server with `--embedding --pooling
  last` and a multimodal projector, in the request shape recorded in
  design/conversational-recall/30 (WeMM-2B's image path).
- `HFEmbedder`: a sentence-transformers model on CPU, for a model no runtime
  above serves yet (Gemma 2 before Ollama 0.36).
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "FLOAT16",
    "GEMMA2_PREFIX",
    "NO_PREFIX",
    "EmbedError",
    "Embedder",
    "HFEmbedder",
    "LlamaServerEmbedder",
    "OllamaEmbedder",
    "Prefix",
    "check",
    "matryoshka",
    "prefix_for",
    "sha256_file",
]

#: The spellings of float16 a runtime or a caller may use.
FLOAT16 = frozenset({"float16", "fp16", "f16", "half", "torch.float16"})

#: What every failure is called in the result (`run.Errored.kind`).
KINDS = (
    "exception", "timeout", "nonfinite", "wrong_dimension", "zero_vector",
    "count_mismatch", "hash_mismatch", "unsupported",
)


class EmbedError(Exception):
    """An item could not be embedded. `kind` is one of `KINDS`."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class Prefix:
    query: str = ""
    document: str = ""

    def record(self) -> dict[str, str]:
        return {"query": self.query, "document": self.document, "media": ""}

    def apply(self, text: str, role: str) -> str:
        if role == "query":
            return self.query + text
        if role == "document":
            return self.document + text
        raise ValueError(f"role must be 'query' or 'document', not {role!r}")


NO_PREFIX = Prefix()
GEMMA2_PREFIX = Prefix(query="task: search result | query: ", document="title: none | text: ")


def _is_gemma2(model: str) -> bool:
    m = model.lower()
    return "embeddinggemma-2" in m or "embeddinggemma2" in m


def prefix_for(model: str) -> Prefix:
    return GEMMA2_PREFIX if _is_gemma2(model) else NO_PREFIX


def _refuse_float16(model: str, dtype: str) -> None:
    if dtype.strip().lower() in FLOAT16:
        raise ValueError(
            f"{model}: float16 refused (EmbeddingGemma's card: it can silently produce "
            "NaN or degraded embeddings); use bfloat16 or float32"
        )


class Embedder(Protocol):
    name: str
    native_dim: int

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]: ...

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]: ...

    def provenance(self) -> dict[str, Any]: ...


def check(vec: Sequence[float], native_dim: int) -> list[float]:
    """A vector as served, or the EmbedError that makes its item ERROR."""
    out = [float(x) for x in vec]
    if len(out) != native_dim:
        raise EmbedError("wrong_dimension", f"{len(out)} != {native_dim}")
    if not all(math.isfinite(x) for x in out):
        raise EmbedError("nonfinite")
    if not any(out):
        raise EmbedError("zero_vector")
    return out


def matryoshka(vec: Sequence[float], dim: int) -> list[float]:
    """The first `dim` components, renormalised to unit length."""
    head = [float(x) for x in vec[:dim]]
    norm = math.sqrt(sum(x * x for x in head))
    if len(head) != dim or norm == 0.0 or not math.isfinite(norm):
        raise EmbedError("zero_vector", f"no unit vector at {dim} dimensions")
    return [x / norm for x in head]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: (method, url, json body or None, timeout seconds) -> parsed JSON.
Transport = Callable[[str, str, dict[str, Any] | None, float], Any]


def http_json(method: str, url: str, body: dict[str, Any] | None, timeout: float) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(  # noqa: S310 - a loopback URL the operator passes
        url, data=data, method=method, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.load(resp)


class OllamaEmbedder:
    """A tag served by Ollama's /api/embed. Media only for a model whose
    `/api/show` capabilities include `vision`, as `{"image": <base64>}`
    inputs: the one shape Ollama 0.40.4 embeds. Its `images` field and a
    data-URI string are silently ignored (every image gets one vector), so
    neither is ever sent, and the harness still errors identical vectors.

    Ollama adds no task prefix of its own (0.40.4, embeddinggemma-2: "two
    bowls of noodles" is 6 prompt tokens bare, 13 with the query prefix), so
    the adapter's prefix is the only one."""

    def __init__(self, model: str, *, native_dim: int, base_url: str = "http://127.0.0.1:11434",
                 dtype: str = "", timeout: float = 120.0, transport: Transport = http_json) -> None:
        if _is_gemma2(model):
            _refuse_float16(model, dtype)
        self.name = model
        self.native_dim = native_dim
        self.prefix = prefix_for(model)
        self._base = base_url.rstrip("/")
        self._dtype = dtype
        self._timeout = timeout
        self._call = transport
        self._show: dict[str, Any] | None = None

    def _shown(self) -> dict[str, Any]:
        if self._show is None:
            self._show = dict(self._call(
                "POST", f"{self._base}/api/show", {"model": self.name}, self._timeout))
        return self._show

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        body = {
            "model": self.name,
            "input": [self.prefix.apply(t, role) for t in texts],
            # An over-long input is an error, never a silently truncated vector.
            "truncate": False,
        }
        resp = self._call("POST", f"{self._base}/api/embed", body, self._timeout)
        return list(resp["embeddings"])

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]:
        if "vision" not in (self._shown().get("capabilities") or ()):
            raise EmbedError("unsupported", f"{self.name} declares no vision capability")
        body = {
            "model": self.name,
            "input": [{"image": base64.b64encode(p.read_bytes()).decode()} for p in paths],
            "truncate": False,
        }
        resp = self._call("POST", f"{self._base}/api/embed", body, self._timeout)
        return list(resp["embeddings"])

    def provenance(self) -> dict[str, Any]:
        version = self._call("GET", f"{self._base}/api/version", None, self._timeout)
        tags = self._call("GET", f"{self._base}/api/tags", None, self._timeout)
        want = self.name if ":" in self.name else f"{self.name}:latest"
        digest = next(
            (m.get("digest") for m in tags.get("models", []) if m.get("name") == want), None,
        )
        show = self._shown()
        details = show.get("details", {})
        info = show.get("model_info") or {}
        return {
            "runtime": "ollama", "runtime_version": version.get("version"),
            "model": self.name, "digest": digest,
            "family": details.get("family"), "quantization": details.get("quantization_level"),
            "dtype": self._dtype or details.get("quantization_level"),
            "capabilities": show.get("capabilities"),
            # What `ollama show` says, beside what the API returns (`native_dim`,
            # checked on every vector): they differ for embeddinggemma-2.
            "show_embedding_length": next(
                (v for k, v in info.items() if k.endswith(".embedding_length")), None),
            "native_dim": self.native_dim,
            "prefix": self.prefix.record(),
        }


class LlamaServerEmbedder:
    """A native llama-server started with `--embedding --pooling last` and,
    for media, `--mmproj` (design/conversational-recall/30)."""

    #: llama.cpp's default multimodal marker (mtmd), where the image goes.
    MEDIA_MARKER = "<__media__>"
    #: WeMM's wrapper: the user turn, then the embedding token.
    WRAP = "<|im_start|>user\n{x}<|im_end|>\n<embedding>"

    def __init__(self, name: str, *, native_dim: int, base_url: str = "http://127.0.0.1:11439",
                 model_sha256: str = "", mmproj_sha256: str = "", build: str = "",
                 dtype: str = "", timeout: float = 120.0, transport: Transport = http_json) -> None:
        if _is_gemma2(name):
            _refuse_float16(name, dtype)
        self.name = name
        self.native_dim = native_dim
        self.prefix = prefix_for(name)
        self._base = base_url.rstrip("/")
        self._prov = {"model_sha256": model_sha256, "mmproj_sha256": mmproj_sha256,
                      "build": build, "dtype": dtype}
        self._timeout = timeout
        self._call = transport

    def _one(self, content: dict[str, Any]) -> list[float]:
        resp = self._call("POST", f"{self._base}/embedding", {"content": content}, self._timeout)
        vec = resp[0]["embedding"] if isinstance(resp, list) else resp["embedding"]
        if vec and isinstance(vec[0], list):  # pooled: one row
            if len(vec) != 1:
                raise EmbedError("count_mismatch", f"{len(vec)} pooled rows")
            vec = vec[0]
        return list(vec)

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        return [self._one({"prompt_string": self.WRAP.format(x=self.prefix.apply(t, role))})
                for t in texts]

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]:
        return [self._one({
            "prompt_string": self.WRAP.format(x=self.MEDIA_MARKER),
            "multimodal_data": [base64.b64encode(p.read_bytes()).decode()],
        }) for p in paths]

    def provenance(self) -> dict[str, Any]:
        return {"runtime": "llama-server", "model": self.name, "native_dim": self.native_dim,
                **self._prov, "wrap": self.WRAP, "media_marker": self.MEDIA_MARKER,
                "prefix": self.prefix.record()}


class HFEmbedder:
    """A sentence-transformers model on CPU, at a pinned commit. What loaded is
    checked, not assumed: every parameter at the requested dtype, and the
    revision the library records equal to the pinned one."""

    #: A Hugging Face commit; a branch or tag name can move under a result.
    REVISION = re.compile(r"[0-9a-f]{40}")

    def __init__(self, model: str, *, revision: str, dtype: str, native_dim: int,
                 encode: Callable[[list[Any]], Sequence[Sequence[float]]] | None = None,
                 load: Callable[[str, str, str], Any] | None = None) -> None:
        _refuse_float16(model, dtype)
        if not self.REVISION.fullmatch(revision):
            raise ValueError(f"{model}: revision must be a 40-hex commit sha, not {revision!r}")
        self.name = model
        self.native_dim = native_dim
        self.prefix = prefix_for(model)
        self._revision = revision
        self._dtype = dtype
        self._encode = encode
        self._load = load or _load_sentence_transformer
        self._loaded: dict[str, Any] = {}

    def _encoder(self) -> Callable[[list[Any]], Sequence[Sequence[float]]]:
        if self._encode is None:
            st = self._load(self.name, self._revision, self._dtype)
            dtypes = {str(p.dtype) for p in st.parameters()}
            if dtypes != {f"torch.{self._dtype}"}:
                raise ValueError(
                    f"{self.name}: asked for dtype {self._dtype}, loaded {sorted(dtypes)}")
            card = getattr(st, "model_card_data", None)
            loaded = getattr(card, "base_model_revision", None)
            if loaded != self._revision:
                raise ValueError(
                    f"{self.name}: pinned revision {self._revision}, loaded {loaded!r}")
            first = st[0] if hasattr(st, "__getitem__") else None
            image = getattr(getattr(first, "processor", None), "image_processor", None)
            self._loaded = {
                **_library_versions(),
                "param_dtype": dtypes.pop(),
                "loaded_revision": loaded,
                # The vision budget, as loaded: Ollama's image path is not this one.
                "image_max_soft_tokens": getattr(image, "max_soft_tokens", None),
            }
            self._encode = lambda xs: st.encode(xs, normalize_embeddings=False).tolist()
        return self._encode

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        return self._encoder()([self.prefix.apply(t, role) for t in texts])

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]:
        from PIL import Image

        return self._encoder()([Image.open(p).convert("RGB") for p in paths])

    def provenance(self) -> dict[str, Any]:
        return {"runtime": "sentence-transformers", "model": self.name, "revision": self._revision,
                "dtype": self._dtype, "native_dim": self.native_dim, "prefix": self.prefix.record(),
                **self._loaded}


def _load_sentence_transformer(model: str, revision: str, dtype: str) -> Any:
    # ponytail: imported on first use; the bake-off venv installs it, the gate
    # never does.
    import sentence_transformers  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]

    return sentence_transformers.SentenceTransformer(
        model, revision=revision, device="cpu",
        model_kwargs={"torch_dtype": getattr(torch, dtype)},
    )


def _library_versions() -> dict[str, Any]:
    try:
        import sentence_transformers
        import torch
        import transformers  # type: ignore[import-not-found]
    except ImportError:
        return {}
    return {"torch": torch.__version__, "transformers": transformers.__version__,
            "sentence_transformers": sentence_transformers.__version__,
            "torch_threads": torch.get_num_threads()}
