"""The bake-off's embedders: one adapter per serving path.

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

The serving paths:

- `OllamaEmbedder`: bge-m3 and any other tag Ollama serves. Text only:
  Ollama 0.35 answers an `images` field on /api/embed with the text-only
  vector and refuses an object input, so `embed_media` refuses.
- `LlamaServerEmbedder`: a native llama-server with `--embedding --pooling
  last` and a multimodal projector, in the request shape of
  WeMM-2B's image path, with WeMM's
  wrapper as its own template files render it (the class says how).
- `HFEmbedder`: a sentence-transformers model on CPU, for a model no runtime
  above serves yet (Gemma 2).
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import math
import re
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "FLOAT16",
    "GEMMA2_PREFIX",
    "JINA_V5_OMNI_SMALL",
    "NO_PREFIX",
    "OFFICIAL",
    "QWEN3VL_DEFAULT_INSTRUCTION_VARIANT",
    "QWEN3VL_EMBEDDING_2B",
    "EmbedError",
    "Embedder",
    "HFEmbedder",
    "LlamaServerEmbedder",
    "OfficialEmbedder",
    "OfficialFormat",
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


def _is_wemm(model: str) -> bool:
    return "wemm-embedding" in model.lower()


#: WeMM's text input, as llama-server's /tokenize renders it (ids equal to the HF
#: processor's under transformers 5.2.0): the HF path must render the same
#: pieces, or it is not the model's embedding.
WEMM_PROBE = "two bowls of noodles"
WEMM_PIECES = ("<|im_start|>", "user", "\n", "two", " bowls", " of", " noodles", "<|im_end|>",
               "<embedding>")


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


#: WeMM's tokenizer.json normaliser, `Replace(Regex("\\n$"), "")` then NFC,
#: with Onig's `$` matching at the end of every line: a newline followed by
#: another newline, or by the end of the text, is dropped. It runs on the text
#: between special tokens. Checked against the official tokenizer.json
#: (sha256 40e444c744512f423da4c8443c47c21e22ff76056ba4e9796a81c04c13a9daf0,
#: tokenizers 0.22.2); llama-server applies neither step.
_WEMM_NEWLINE = re.compile(r"\n(?=\n|\Z)")


#: tokenizer.json at the pinned revision, and the tokens it adds (all
#: `normalized: false`, no lstrip/rstrip): the official tokenizer splits on
#: every one of them before it normalises.
WEMM_TOKENIZER_SHA256 = "40e444c744512f423da4c8443c47c21e22ff76056ba4e9796a81c04c13a9daf0"
WEMM_ADDED_TOKENS = (
    "<|endoftext|>",
    "<|im_start|>",
    "<|im_end|>",
    "<|object_ref_start|>",
    "<|object_ref_end|>",
    "<|box_start|>",
    "<|box_end|>",
    "<|quad_start|>",
    "<|quad_end|>",
    "<|vision_start|>",
    "<|vision_end|>",
    "<|vision_pad|>",
    "<|image_pad|>",
    "<|video_pad|>",
    "<tool_call>",
    "</tool_call>",
    "<|fim_prefix|>",
    "<|fim_middle|>",
    "<|fim_suffix|>",
    "<|fim_pad|>",
    "<|repo_name|>",
    "<|file_sep|>",
    "<tool_response>",
    "</tool_response>",
    "<think>",
    "</think>",
    "<|audio_start|>",
    "<|audio_end|>",
    "<tts_pad>",
    "<tts_text_bos>",
    "<tts_text_eod>",
    "<tts_text_bos_single>",
    "<|audio_pad|>",
    "<embedding>",
)
#: No added token contains another, so the alternation's order cannot matter.
_WEMM_SPECIAL = re.compile("(" + "|".join(re.escape(t) for t in WEMM_ADDED_TOKENS) + ")")


def wemm_normalise(text: str) -> str:
    return unicodedata.normalize("NFC", _WEMM_NEWLINE.sub("", text))


def wemm_prompt(wrapped: str) -> str:
    """A wrapped prompt with every run between added tokens normalised, as
    the tokenizer does: `user\\n` plus the text is one such run, cut again at
    any added token the text itself contains. Matches the official tokenizer
    on tests/fixtures/wemm_normaliser_golden.json."""
    return "".join(part if _WEMM_SPECIAL.fullmatch(part) else wemm_normalise(part)
                   for part in _WEMM_SPECIAL.split(wrapped))


def _proc_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


class LlamaServerEmbedder:
    """A native llama-server started with `--embedding --pooling last` and,
    for media, `--mmproj`.

    WeMM-Embedding-2B's input is fixed by its own files (tencent/WeMM-Embedding-2B
    at `bbd6cd4b`: `embedding_chat_template.jinja`, the sentence-transformers
    template and `tokenizer.json`, tokenized by the transformers 5.2.0 the card
    pins): one user turn, then the special token `<embedding>` (id 248077) with
    NO newline before it, because the tokenizer's normalizer strips the newline
    after `<|im_end|>` and its post-processor appends the token. Before an
    image the role's newline is dropped as well. Last-token pooling then reads
    the `<embedding>` position. The model has no query or document instruction
    (`prompts` is `{}`), so the prefix is empty on both roles.

    The GGUF's EOS is `<embedding>` and llama-server appends it as a special
    token, so the prompt here stops at `<|im_end|>`: writing `<embedding>` too
    embeds it twice and moves the vector away from the HF reference.

    What the server would embed is checked, never assumed, and anything else
    is an `unsupported` ERROR before a vector is asked for:

    - its command line (`/proc/<pid>/cmdline`, recorded) must carry
      `--embedding` and `--pooling last`, and listen on this URL's port;
    - every text prompt, exactly as sent, and the image prompt, once, are
      tokenized twice: with `add_special` the sequence must be the bare one
      plus exactly one `<embedding>`, the bare one must start
      `<|im_start|>`, `user` (then `\\n` for text, never for an image) and
      end `<|im_end|>`;
    - a text is normalised the way WeMM's tokenizer.json does (`wemm_normalise`)
      before it is wrapped, because llama-server does not."""

    #: llama.cpp's default multimodal marker (mtmd), where the image goes. mtmd
    #: wraps a Qwen-VL image in `<|vision_start|>`...`<|vision_end|>` itself.
    #: A newer server randomises it per start and publishes it as `media_marker`
    #: in /props (the default then fails to tokenize), so
    #: the marker is read from the server, and the default is only for a server
    #: whose /props does not name one.
    MEDIA_MARKER = "<__media__>"
    #: A text input: the user turn; the server appends `<embedding>` right after.
    WRAP = "<|im_start|>user\n{x}<|im_end|>"
    #: An image alone: no newline after the role (the official template).
    MEDIA_WRAP = "<|im_start|>user{x}<|im_end|>"
    #: The special token the server must append, last, exactly once.
    APPENDED = "<embedding>"

    def __init__(self, name: str, *, native_dim: int, base_url: str = "http://127.0.0.1:11439",
                 model_sha256: str = "", mmproj_sha256: str = "", build: str = "",
                 dtype: str = "", server_pid: int | None = None, timeout: float = 120.0,
                 transport: Transport = http_json,
                 read_cmdline: Callable[[int], list[str] | None] = _proc_cmdline,
                 proc: Path = Path("/proc")) -> None:
        if _is_gemma2(name):
            _refuse_float16(name, dtype)
        self.name = name
        self.native_dim = native_dim
        self.prefix = prefix_for(name)
        self._base = base_url.rstrip("/")
        self._prov = {"model_sha256": model_sha256, "mmproj_sha256": mmproj_sha256,
                      "build": build, "dtype": dtype}
        self._pid = server_pid
        self._timeout = timeout
        self._call = transport
        self._read_cmdline = read_cmdline
        self._proc = proc
        self._marker: str | None = None
        self._marker_source = ""
        self._cmdline: list[str] | None = None
        self._checked: set[str] = set()
        self._media_pieces: list[str] | None = None

    def media_marker(self) -> str:
        if self._marker is None:
            props = self._call("GET", f"{self._base}/props", None, self._timeout)
            named = props.get("media_marker") if isinstance(props, dict) else None
            self._marker = named or self.MEDIA_MARKER
            self._marker_source = "/props" if named else "default"
        return self._marker

    def _server_checked(self) -> None:
        """The server's own command line: an embedding run, pooling the last
        token, on this URL's port. Read once."""
        if self._cmdline is not None:
            return
        if self._pid is None:
            raise EmbedError("unsupported", "no server pid: its pooling cannot be checked")
        argv = self._read_cmdline(self._pid)
        if not argv:
            raise EmbedError("unsupported", f"cannot read the command line of pid {self._pid}")

        def value(flag: str) -> str | None:
            for i, a in enumerate(argv):
                if a == flag and i + 1 < len(argv):
                    return argv[i + 1]
                if a.startswith(flag + "="):
                    return a.split("=", 1)[1]
            return None

        port = urllib.parse.urlsplit(self._base).port
        problems = []
        if not ({"--embedding", "--embeddings"} & set(argv)):
            problems.append("no --embedding")
        if value("--pooling") != "last":
            problems.append(f"--pooling {value('--pooling')!r}, not 'last'")
        if port is not None and (value("--port") or "8080") != str(port):
            problems.append(f"--port {value('--port')!r}, not this URL's {port}")
        if problems:
            raise EmbedError("unsupported", f"pid {self._pid}: " + "; ".join(problems))
        self._cmdline = argv

    def _pieces(self, prompt: str, add_special: bool) -> list[str]:
        resp = self._call("POST", f"{self._base}/tokenize", {
            "content": prompt, "add_special": add_special, "with_pieces": True,
        }, self._timeout)
        return [t.get("piece") if isinstance(t, dict) else None for t in resp.get("tokens", [])]

    def _sequence_checked(self, prompt: str, *, media: bool) -> list[str]:
        """`prompt`, exactly as it will be embedded: the server's full sequence
        must be its bare tokens plus one `<embedding>`, and the bare tokens
        WeMM's turn. Returns the full sequence."""
        bare = self._pieces(prompt, False)
        full = self._pieces(prompt, True)
        head_ok = bare[:2] == ["<|im_start|>", "user"] and (
            (len(bare) > 2 and bare[2] != "\n") if media else bare[2:3] == ["\n"])
        if not (full == [*bare, self.APPENDED] and head_ok and bare[-1:] == ["<|im_end|>"]):
            raise EmbedError("unsupported", f"server sequence {full[:4]}...{full[-3:]} "
                                            f"is not WeMM's turn plus one {self.APPENDED}")
        return full

    def _text_prompt(self, text: str) -> str:
        prompt = wemm_prompt(self.WRAP.format(x=text))
        if prompt not in self._checked:
            self._sequence_checked(prompt, media=False)
            self._checked.add(prompt)
        return prompt

    def _one(self, content: dict[str, Any]) -> list[float]:
        resp = self._call("POST", f"{self._base}/embedding", {"content": content}, self._timeout)
        vec = resp[0]["embedding"] if isinstance(resp, list) else resp["embedding"]
        if vec and isinstance(vec[0], list):  # pooled: one row
            if len(vec) != 1:
                raise EmbedError("count_mismatch", f"{len(vec)} pooled rows")
            vec = vec[0]
        return list(vec)

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        self._server_checked()
        prompts = [self._text_prompt(self.prefix.apply(t, role)) for t in texts]
        return [self._one({"prompt_string": p}) for p in prompts]

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]:
        self._server_checked()
        prompt = self.MEDIA_WRAP.format(x=self.media_marker())
        if self._media_pieces is None:
            self._media_pieces = self._sequence_checked(prompt, media=True)
        return [self._one({
            "prompt_string": prompt,
            "multimodal_data": [base64.b64encode(p.read_bytes()).decode()],
        }) for p in paths]

    def server_memory(self) -> dict[str, int] | None:
        """The server's lifetime peak and current RSS (kB, `VmHWM`, `VmRSS`),
        read when called, or None without a pid or a readable status file."""
        if self._pid is None:
            return None
        try:
            lines = (self._proc / str(self._pid) / "status").read_text().splitlines()
        except OSError:
            return None
        out = {k: int(v.split()[0]) for k, _, v in (ln.partition(":") for ln in lines)
               if k in ("VmHWM", "VmRSS")}
        return {"peak_rss_kib": out.get("VmHWM", 0), "rss_kib": out.get("VmRSS", 0)}

    def provenance(self) -> dict[str, Any]:
        return {"runtime": "llama-server", "model": self.name, "native_dim": self.native_dim,
                **self._prov, "wrap": self.WRAP, "media_wrap": self.MEDIA_WRAP,
                "media_marker_source": self._marker_source or None,
                "server_appends": self.APPENDED, "server_cmdline": self._cmdline,
                "template_check": {"checked": "/tokenize, every prompt as sent",
                                   "texts": len(self._checked), "media": self._media_pieces},
                "normaliser": "wemm_normalise (tokenizer.json sha256 40e444c7...)",
                "prefix": self.prefix.record(), "server_memory": self.server_memory()}


class HFEmbedder:
    """A sentence-transformers model on CPU, at a pinned commit. What loaded is
    checked, not assumed: every parameter at the requested dtype, and the
    revision the library records equal to the pinned one.

    WeMM-Embedding-2B also has its input checked: the loaded processor must
    render `WEMM_PROBE` as exactly `WEMM_PIECES`. `text_only` loads it without
    the two parts a text embedding never reaches (`lm_head`: `embedding()`
    pools hidden states; `visual`: only image inputs use it), each replaced by
    a module that raises if called, so its fp32 weights fit on the deploy host;
    a text vector is unchanged,
    and media refuses."""

    #: A Hugging Face commit; a branch or tag name can move under a result.
    REVISION = re.compile(r"[0-9a-f]{40}")

    def __init__(self, model: str, *, revision: str, dtype: str, native_dim: int,
                 encode: Callable[[list[Any]], Sequence[Sequence[float]]] | None = None,
                 load: Callable[[str, str, str], Any] | None = None, text_only: bool = False,
                 pieces: Callable[[Any], list[str]] | None = None) -> None:
        _refuse_float16(model, dtype)
        if not self.REVISION.fullmatch(revision):
            raise ValueError(f"{model}: revision must be a 40-hex commit sha, not {revision!r}")
        self.name = model
        self.native_dim = native_dim
        self.prefix = prefix_for(model)
        self._revision = revision
        self._dtype = dtype
        self._encode = encode
        self._text_only = text_only
        self._load = load or (lambda m, r, d: _load_sentence_transformer(m, r, d, text_only))
        self._pieces = pieces or _st_pieces
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
            template: list[str] | None = None
            if _is_wemm(self.name):
                template = self._pieces(st)
                if tuple(template) != WEMM_PIECES:
                    raise ValueError(f"{self.name}: renders {WEMM_PROBE!r} as {template}, "
                                     f"not WeMM's {list(WEMM_PIECES)}")
            first = st[0] if hasattr(st, "__getitem__") else None
            image = getattr(getattr(first, "processor", None), "image_processor", None)
            self._loaded = {
                **_library_versions(),
                "param_dtype": dtypes.pop(),
                "loaded_revision": loaded,
                "template_pieces": template,
                "text_only": self._text_only,
                "dropped": getattr(st, "_bakeoff_dropped", None),
                # The vision budget, as loaded: Ollama's image path is not this one.
                "image_max_soft_tokens": getattr(image, "max_soft_tokens", None),
            }
            self._encode = lambda xs: st.encode(xs, normalize_embeddings=False).tolist()
        return self._encode

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        return self._encoder()([self.prefix.apply(t, role) for t in texts])

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]:
        if self._text_only:
            raise EmbedError("unsupported", f"{self.name} was loaded text-only")
        from PIL import Image

        return self._encoder()([Image.open(p).convert("RGB") for p in paths])

    def provenance(self) -> dict[str, Any]:
        return {"runtime": "sentence-transformers", "model": self.name, "revision": self._revision,
                "dtype": self._dtype, "native_dim": self.native_dim, "prefix": self.prefix.record(),
                **self._loaded}


def _load_sentence_transformer(model: str, revision: str, dtype: str,
                               text_only: bool = False) -> Any:
    # ponytail: imported on first use; the bake-off venv installs it, the gate
    # never does.
    import sentence_transformers  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]

    if not text_only:
        return sentence_transformers.SentenceTransformer(
            model, revision=revision, device="cpu", trust_remote_code=_is_wemm(model),
            model_kwargs={"torch_dtype": getattr(torch, dtype)},
        )
    # The checkpoint's own dtype first (bf16 -> fp32 is exact), then the two
    # unreached parts out, then the cast: the whole model never exists in fp32.
    st = sentence_transformers.SentenceTransformer(
        model, revision=revision, device="cpu", trust_remote_code=_is_wemm(model),
        model_kwargs={"torch_dtype": torch.bfloat16},
    )

    class Unreached(torch.nn.Module):  # type: ignore[misc]
        def __init__(self, part: str) -> None:
            super().__init__()
            self.part = part

        def forward(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError(f"text-only load: {self.part} was dropped and must not run")

    inner = st[0].model
    dropped = {}
    for owner, attr, label in ((inner, "lm_head", "lm_head"), (inner.model, "visual", "visual")):
        part = getattr(owner, attr)
        dropped[label] = sum(p.numel() for p in part.parameters())
        setattr(owner, attr, Unreached(label))
    st.to(getattr(torch, dtype))
    st._bakeoff_dropped = dropped
    return st


def _st_pieces(st: Any) -> list[str]:
    """How the loaded processor renders `WEMM_PROBE`, one decoded piece per id."""
    first = st[0]
    ids = first.preprocess([WEMM_PROBE])["input_ids"][0].tolist()
    tok = first.processor.tokenizer
    return [tok.decode([i]) for i in ids]


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


# --- Qwen3-VL-Embedding-2B and jina-embeddings-v5-omni-small: the official formats ----
#
# Each model's input is fixed by its own files at the pinned commit, never by
# a guess, and every sequence that reaches the model is checked against it
# (`OfficialEmbedder`). Read from:
#
# - Qwen/Qwen3-VL-Embedding-2B @ 9f2f7e71: `config_sentence_transformers.json`
#   (`prompts.default` = "Represent the user's input."), `chat_template.jinja`
#   (the same string as `default_system_message`), `scripts/qwen3_vl_embedding.py`
#   (`default_instruction`, the same again; last-token pooling, then L2),
#   `1_Pooling/config.json` (`lasttoken`) and `tokenizer.json`, whose
#   post-processor appends `<|endoftext|>`, the token that is pooled. The
#   instruction is a system turn. The Hugging Face card names no query
#   instruction, but the vendor's own retrieval usage does: the README of
#   QwenLM/Qwen3-VL-Embedding (@393e2978, its Transformers example) and its
#   MMEB text-to-image eval give a QUERY "Retrieve images or text relevant to
#   the user's query." and a document or image the default. That is the
#   scored format; the default on every role is kept as a recorded variant
#   (`QWEN3VL_DEFAULT_INSTRUCTION_VARIANT`). Matryoshka 64 to
#   2048 (card).
# - jinaai/jina-embeddings-v5-omni-small @ 950200ab: `config_sentence_transformers.json`
#   (`Query: ` / `Document: `), `custom_st.py` (text is the prompt plus the
#   text, no chat template and no special token; an image is the chat
#   template over the prompt and the vision run, `_build_eval_image_prompt`;
#   last-token pooling at `attention_mask.sum() - 1`, so right padding only;
#   L2), the `retrieval` LoRA adapter (`default_task`). The card: "The
#   Query:/Document: distinction applies to EVERY modality", so a pool image
#   is a document. Matryoshka 32 to 1024 (`config.json`).

#: Qwen3-VL-Embedding's default instruction: documents and images.
QWEN3VL_INSTRUCTION = "Represent the user's input."
#: Its retrieval query instruction (the vendor's README example and MMEB t2i eval).
QWEN3VL_QUERY_INSTRUCTION = "Retrieve images or text relevant to the user's query."
#: The run of image tokens collapses to one in a checked sequence: its length
#: is the image's patch count, which the processor derives from the pixels.
IMAGE_RUN = "<|vision_start|><|image_pad|><|vision_end|>"

def _qwen3vl_turn(instruction: str) -> str:
    return ("<|im_start|>system\n" + instruction + "<|im_end|>\n"
            "<|im_start|>user\n{x}<|im_end|>\n<|im_start|>assistant\n<|endoftext|>")


_QWEN3VL_TURN = _qwen3vl_turn(QWEN3VL_INSTRUCTION)
_IMAGE_PAD_RUN = re.compile(r"(?:<\|image_pad\|>)+")


@dataclass(frozen=True, slots=True)
class OfficialFormat:
    """One model's input as its own files define it, and how
    sentence-transformers is asked to produce it."""

    label: str
    repo: str
    revision: str
    licence: str
    native_dim: int
    #: role -> the whole sequence the model reads, `{x}` for the text.
    text: dict[str, str]
    #: The whole sequence for one image, its token run collapsed (`IMAGE_RUN`).
    image: str
    #: role ("query", "document", "image") -> `SentenceTransformer.encode` kwargs.
    encode: dict[str, dict[str, str]]
    #: Beyond the dtype, what `model_kwargs` the card loads it with.
    model_kwargs: dict[str, str]
    trust_remote_code: bool
    #: The pool reads `attention_mask.sum() - 1`: a left-padded row is wrong.
    right_padding_only: bool

    def sent(self, role: str, text: str = "") -> str:
        """The exact sequence the model must read for this input."""
        if role == "image":
            return self.image
        if role not in self.text:
            raise ValueError(f"role must be 'query', 'document' or 'image', not {role!r}")
        return self.text[role].replace("{x}", text)

    def record(self) -> dict[str, Any]:
        return {"label": self.label, "repo": self.repo, "revision": self.revision,
                "licence": self.licence, "text": dict(self.text), "image": self.image,
                "encode": {k: dict(v) for k, v in self.encode.items()},
                "model_kwargs": dict(self.model_kwargs),
                "trust_remote_code": self.trust_remote_code,
                "pooling": "last token, then L2; Matryoshka: truncate, then L2"}


QWEN3VL_EMBEDDING_2B = OfficialFormat(
    label="qwen3vl",
    repo="Qwen/Qwen3-VL-Embedding-2B",
    revision="9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda",
    licence="apache-2.0",
    native_dim=2048,
    text={"query": _qwen3vl_turn(QWEN3VL_QUERY_INSTRUCTION), "document": _QWEN3VL_TURN},
    image=_QWEN3VL_TURN.replace("{x}", IMAGE_RUN),
    encode={"query": {"prompt": QWEN3VL_QUERY_INSTRUCTION},
            "document": {"prompt": QWEN3VL_INSTRUCTION}, "image": {"prompt": QWEN3VL_INSTRUCTION}},
    model_kwargs={},
    trust_remote_code=False,
    right_padding_only=False,
)

#: The default instruction on queries too. A recorded variant, never the scored arm.
QWEN3VL_DEFAULT_INSTRUCTION_VARIANT = dataclasses.replace(
    QWEN3VL_EMBEDDING_2B, label="qwen3vl-defaultinst",
    text={"query": _QWEN3VL_TURN, "document": _QWEN3VL_TURN},
    encode={r: {"prompt": QWEN3VL_INSTRUCTION} for r in ("query", "document", "image")},
)

JINA_V5_OMNI_SMALL = OfficialFormat(
    label="jina5omni",
    repo="jinaai/jina-embeddings-v5-omni-small",
    revision="950200abde3a3bcf3d5bdfe5d28fb5bb5e7dfbb0",
    # Non-commercial: measurement only; shipping it would need another licence.
    licence="cc-by-nc-4.0 (non-commercial; measurement only)",
    native_dim=1024,
    text={"query": "Query: {x}", "document": "Document: {x}"},
    image="<|im_start|>user\nDocument: " + IMAGE_RUN + "<|im_end|>\n",
    encode={"query": {"prompt_name": "query"}, "document": {"prompt_name": "document"},
            "image": {"prompt_name": "document"}},
    # `vision` skips the audio tower at load; text and image paths are unchanged.
    model_kwargs={"default_task": "retrieval", "modality": "vision"},
    trust_remote_code=True,
    right_padding_only=True,
)

OFFICIAL = {f.label: f for f in (QWEN3VL_EMBEDDING_2B, JINA_V5_OMNI_SMALL)}


def fed_sequence(pieces: str, mask: Sequence[int] | None, *, right_padding_only: bool) -> str:
    """What a check compares: the decoded unpadded row with its image run
    collapsed, or a marker no format equals when the padding breaks the pool."""
    if right_padding_only and mask is not None and list(mask) != sorted(mask, reverse=True):
        return "<left-padded: the last-token pool reads a pad>"
    return _IMAGE_PAD_RUN.sub("<|image_pad|>", pieces)


class OfficialEmbedder:
    """A model in its official format (`OfficialFormat`) through
    sentence-transformers on CPU, at a pinned commit and an explicit dtype.

    Checked, not assumed, before a vector counts:

    - the snapshot is the pinned commit (its directory is the 40-hex sha),
      loaded from disk with the hub offline, so `trust_remote_code` runs only
      that commit's code;
    - every parameter is at the requested dtype. Under
      transformers 5.4.0, jina's model, loaded through sentence-transformers,
      through `AutoModel.from_pretrained` or through its remote base class's
      own `from_pretrained`, each given `dtype` or `torch_dtype` float32,
      returns its base (902M parameters) in bf16 beside fp32 LoRA weights.
      So the loaded model is cast, and the
      check is equality over every parameter. Both checkpoints are stored
      bf16, so the cast to float32 is exact;
    - every sequence the model's `forward` receives, decoded, equals
      `OfficialFormat.sent` for its input, as a multiset per call (the
      library sorts a batch by length). Anything else is an `unsupported`
      ERROR for the batch, never a vector."""

    def __init__(self, fmt: OfficialFormat, *, dtype: str,
                 load: Callable[[str, OfficialFormat, str, list[str]], Any] | None = None,
                 snapshot: Callable[[str, str], str] | None = None) -> None:
        _refuse_float16(fmt.repo, dtype)
        if not HFEmbedder.REVISION.fullmatch(fmt.revision):
            raise ValueError(f"{fmt.repo}: revision must be a 40-hex commit sha")
        self.fmt = fmt
        self.name = fmt.repo
        self.native_dim = fmt.native_dim
        self._dtype = dtype
        self._load = load or _load_official
        self._snapshot = snapshot or _pinned_snapshot
        self._st: Any = None
        self._fed: list[str] = []
        self._loaded: dict[str, Any] = {}
        self._checked = {"query": 0, "document": 0, "image": 0}

    def _model(self) -> Any:
        if self._st is None:
            path = self._snapshot(self.fmt.repo, self.fmt.revision)
            if Path(path).name != self.fmt.revision:
                raise ValueError(f"{self.name}: pinned {self.fmt.revision}, snapshot {path}")
            st = self._load(path, self.fmt, self._dtype, self._fed)
            dtypes = {str(p.dtype) for p in st.parameters()}
            if dtypes != {f"torch.{self._dtype}"}:
                raise ValueError(f"{self.name}: asked for {self._dtype}, loaded {sorted(dtypes)}")
            self._loaded = {**_library_versions(), "param_dtype": dtypes.pop(),
                            "loaded_revision": Path(path).name,
                            "parameters": sum(p.numel() for p in st.parameters())}
            self._st = st
        return self._st

    def _encode(self, inputs: list[Any], role: str, expected: list[str]) -> list[list[float]]:
        st = self._model()
        self._fed.clear()
        out = st.encode(inputs, batch_size=len(inputs), normalize_embeddings=False,
                        convert_to_numpy=True, **self.fmt.encode[role])
        fed = sorted(self._fed)
        self._fed.clear()
        if fed != sorted(expected):
            raise EmbedError("unsupported", f"{self.fmt.label}: {len(fed)} sequences fed, not "
                                            f"the {len(expected)} of its official {role} format")
        self._checked[role] += len(expected)
        return [list(map(float, v)) for v in out]

    def embed_text(self, texts: Sequence[str], role: str) -> Sequence[Sequence[float]]:
        return self._encode(list(texts), role, [self.fmt.sent(role, t) for t in texts])

    def embed_media(self, paths: Sequence[Path]) -> Sequence[Sequence[float]]:
        from PIL import Image

        images = [Image.open(p).convert("RGB") for p in paths]
        return self._encode(images, "image", [self.fmt.image] * len(images))

    def provenance(self) -> dict[str, Any]:
        return {"runtime": "sentence-transformers", "model": self.name,
                "revision": self.fmt.revision, "licence": self.fmt.licence, "dtype": self._dtype,
                "native_dim": self.native_dim, "format": self.fmt.record(),
                "format_checked": {"how": "every sequence forward() received, decoded",
                                   **self._checked},
                **self._loaded}


def _pinned_snapshot(repo: str, revision: str) -> str:
    """The local snapshot of `repo` at `revision`, never a download: the run
    reads only what was fetched and hashed beforehand."""
    from huggingface_hub import snapshot_download  # type: ignore[import-not-found]

    return str(snapshot_download(repo_id=repo, revision=revision, local_files_only=True,
                                 ignore_patterns=["*.png", "*.gguf"]))


def _load_official(path: str, fmt: OfficialFormat, dtype: str, fed: list[str]) -> Any:
    # ponytail: imported on first use; the bake-off venv installs it, the gate
    # never does.
    import os

    import sentence_transformers
    import torch

    os.environ["HF_HUB_OFFLINE"] = "1"
    st = sentence_transformers.SentenceTransformer(
        path, device="cpu", trust_remote_code=fmt.trust_remote_code,
        model_kwargs={**fmt.model_kwargs, "dtype": getattr(torch, dtype)},
    )
    st.to(getattr(torch, dtype))
    first = st[0]
    model = first.model
    tok = getattr(first, "tokenizer", None) or first.processor.tokenizer
    forward = model.forward

    # sentence-transformers calls `model.forward` directly (no hooks run), and
    # jina's module calls the model; an instance attribute sees both.
    def checked_forward(*args: Any, **kwargs: Any) -> Any:
        ids = kwargs["input_ids"] if "input_ids" in kwargs else args[0]
        mask = kwargs.get("attention_mask")
        for r in range(ids.shape[0]):
            row_mask = None if mask is None else mask[r].tolist()
            row = ids[r] if mask is None else ids[r][mask[r] == 1]
            fed.append(fed_sequence(tok.decode(row.tolist(), skip_special_tokens=False),
                                    row_mask, right_padding_only=fmt.right_padding_only))
        return forward(*args, **kwargs)

    model.forward = checked_forward
    return st
