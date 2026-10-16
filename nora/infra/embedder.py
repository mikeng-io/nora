"""The one client for the memory index's embedder: Qwen3-VL-Embedding-2B
served by llama-server with `--embedding --pooling last` and its projector,
the `embedder` compose service, run with `-c 2048 -b 2048 -ub 2048 -t 4
-ngl 0 --parallel 1`.

Every prompt comes from `nora.domain.embedder_input`, the formatter the
bake-off adapter shares, so production embeds exactly what the bake-off
measured. A vector comes back as `DIMENSIONS` floats, L2-normalised, so a
cosine and an inner product rank the same.

**Two failures, kept apart** (an ERROR is never a PASS):

- `EmbedderUnavailableError`: the embedder did not answer usefully (refused,
  timed out, not healthy, an answer that is not one vector of the expected
  width, or a server that is not serving the configured model). Nothing is
  known about the input; the caller stops for this tick.
- `EmbedderRejectedError`: THIS input is at fault. A 4xx; or a 5xx while the
  server's /health still answers 200, which is how llama-server reports an
  input it cannot embed ("input is too large", "failed to load image", a
  media marker it does not know); or image bytes that do not decode. The
  caller backs that node off and moves on.

Neither ever carries the input: an exception message here names a status
or a class, never a text, because a message body is room content.

**A text fits the server's batch.** A pooled embedding is one batch, so a
prompt longer than `-ub` tokens is refused. A text up to `SAFE_TEXT_CHARS`
characters is sent as it is; a longer one is counted with the server's own
/tokenize and cut until its prompt fits `TOKEN_BUDGET`.

**An image is decoded here, every time**, by Pillow in a thread, and sent as
the PNG of its first frame: llama-server's decoder reads no WebP (most
stickers), and a crafted file must fail HERE, as this node's rejection,
never inside the shared server. A file claiming more than
`MAX_DECODE_PIXELS` is refused before it is decoded, and one larger than
`MAX_SEND_PIXELS` is downscaled first (module constants say why).
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import time
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any, NamedTuple

import aiohttp

from nora.domain.embedder_input import FORMAT_VERSION, image_prompt, text_prompt

__all__ = [
    "DIMENSIONS",
    "MAX_DECODE_PIXELS",
    "MAX_SEND_PIXELS",
    "QUERY_TIMEOUT_SECONDS",
    "SAFE_TEXT_CHARS",
    "SERVED_MODEL_TTL_SECONDS",
    "TOKEN_BUDGET",
    "EmbedderRejectedError",
    "EmbedderUnavailableError",
    "LlamaEmbedder",
    "TextVector",
    "fingerprint",
    "model_file",
]

#: Qwen3-VL-Embedding-2B's width (store 2048, index the first 256).
DIMENSIONS = 2048

#: The server's micro-batch (`-ub 2048`, the bake-off's): a pooled embedding
#: is computed in one, so no prompt may be longer. The budget leaves room for
#: the special tokens llama-server adds.
UBATCH = 2048
TOKEN_BUDGET = UBATCH - 48

#: A text this long or shorter cannot overflow the budget, so it is sent
#: without a /tokenize call: the worst measured rate on this tokenizer is
#: 2.11 tokens a character (3500 rare Hong Kong characters, 7389 tokens;
#: ordinary Cantonese 1.22, emoji 1.10), and 880 x 2.11 plus the ~40 tokens
#: of the prompt's own turns is under `TOKEN_BUDGET`.
SAFE_TEXT_CHARS = 880

#: Qwen3-VL merges 2x2 patches of 16 px, so one image token is 32 x 32 px.
#: 256 tokens' worth (512 x 512 px) is the largest image the bake-off measured
#: (its 224 photos: 0.18 Mpx median, 0.26 Mpx max), where text -> image recall
#: was 1.000 at 3.4 s an image. Live, 1920 tokens' worth took about 60 s an
#: image on 4 threads and peaked the server at 6.5 GiB, past its 4 GiB cap,
#: so the cap is what was measured, not what the micro-batch could hold.
MAX_SEND_PIXELS = 256 * 32 * 32
#: A file claiming more pixels than this is refused before decoding: a
#: decompression bomb is a few KB that inflates to gigabytes.
MAX_DECODE_PIXELS = 64_000_000

#: A search's whole query embedding, inside a turn: every call it
#: makes (/props, /tokenize, /embedding, /health) shares this one deadline.
#: A text query takes about 0.4 s on norty's CPU, but the server runs
#: one request at a time (`--parallel 1`), so a query can wait behind an
#: image the worker is embedding (about 3.4 s). Ten seconds covers both
#: twice; past it the search falls back to the lexical seed, never waits the
#: worker's 120. A per-call timeout would let the three calls take 25 s.
QUERY_TIMEOUT_SECONDS = 10.0
#: How long a query trusts the last /props that named the configured model,
#: so a search makes one call, not two. A model swap is an operator's
#: restart of the `embedder` service; for at most this long after it, a
#: question may be embedded by the new model and compared with the old one's
#: vectors, until the worker clears them.
SERVED_MODEL_TTL_SECONDS = 60.0

#: llama.cpp's default multimodal marker, for a server whose /props names none.
_DEFAULT_MARKER = "<__media__>"


class TextVector(NamedTuple):
    """A text's vector, and whether the text was cut to fit the server's
    micro-batch first, so the vector stands for its head only."""

    vector: list[float]
    truncated: bool


class EmbedderUnavailableError(RuntimeError):
    """The embedder did not answer usefully; nothing is known about the input."""


class EmbedderRejectedError(RuntimeError):
    """This input cannot be embedded: refused by the server, or undecodable."""


def fingerprint(model_id: str) -> str:
    """What `memory_node.embedding_model` records: the configured model id
    (`<gguf file name>@<sha256 prefix>`) plus the formatter's version. A
    vector whose fingerprint is not the current one is another model's."""
    return f"{model_id}+fmt{FORMAT_VERSION}"


def model_file(model_id: str) -> str:
    """The GGUF file name a configured model id names (before the `@`)."""
    return model_id.partition("@")[0]


def _normalised(raw: Any) -> list[float]:
    """One pooled row as `DIMENSIONS` unit-length floats, or unavailable."""
    vec = raw
    if isinstance(vec, list) and vec and isinstance(vec[0], list):
        if len(vec) != 1:
            raise EmbedderUnavailableError(f"{len(vec)} pooled rows, not one")
        vec = vec[0]
    if not isinstance(vec, list) or len(vec) != DIMENSIONS:
        width = len(vec) if isinstance(vec, list) else type(vec).__name__
        raise EmbedderUnavailableError(f"a vector of {width}, not {DIMENSIONS}")
    try:
        floats = [float(x) for x in vec]
    except (TypeError, ValueError) as exc:
        raise EmbedderUnavailableError("a vector that is not numbers") from exc
    norm = math.sqrt(math.fsum(x * x for x in floats))
    if not math.isfinite(norm) or norm == 0.0:
        raise EmbedderUnavailableError("a vector with no direction")
    return [x / norm for x in floats]


def _as_sendable(data: bytes) -> bytes:
    """`data` decoded and re-encoded as the PNG of its first frame, at most
    `MAX_SEND_PIXELS`. ANY failure is this input's rejection: Pillow raises
    far more than `OSError` on a crafted file (`DecompressionBombError`,
    `SyntaxError`, `struct.error`, ...), and one that escaped would leave the
    node unrecorded, first in every pick, wedging the worker."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_DECODE_PIXELS:
                raise EmbedderRejectedError(f"image of {width}x{height} pixels refused")
            img.seek(0)
            frame = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        if width * height > MAX_SEND_PIXELS:
            scale = math.sqrt(MAX_SEND_PIXELS / (width * height))
            frame = frame.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.BICUBIC,
            )
        out = io.BytesIO()
        frame.save(out, format="PNG")
        return out.getvalue()
    except EmbedderRejectedError:
        raise
    except Exception as exc:  # any decode failure is this input's
        raise EmbedderRejectedError(f"undecodable image ({type(exc).__name__})") from exc


class LlamaEmbedder:
    """llama-server's `/embedding`, one input per call (the bake-off's
    shape). `base_url` is the compose service (`http://embedder:8080`).

    `query_model_file`, when given, is the GGUF file a QUERY must be embedded
    by: `embed_query` reads /props first and is unavailable while the server
    serves any other file, the worker's own rule (`nora.infra.sql.
    memory_embed`), so a search never compares a question from one model
    with vectors from another; a match is trusted for
    `SERVED_MODEL_TTL_SECONDS`. `timeout_seconds` bounds each call, and
    `query_timeout_seconds` a whole `embed_query`."""

    def __init__(
        self, base_url: str, *, timeout_seconds: float = 120.0,
        query_model_file: str | None = None,
        query_timeout_seconds: float = QUERY_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._marker: str | None = None
        self._query_model_file = query_model_file
        self._query_timeout = query_timeout_seconds
        self._clock = clock
        self._served_ok_at: float | None = None

    async def served_model(self) -> str:
        """The file name of the model the server loaded (/props
        `model_path`), so the worker can refuse to stamp vectors with a
        fingerprint the server is not serving."""
        props = await self._call("GET", "/props", None)
        path = props.get("model_path") if isinstance(props, dict) else None
        if not path:
            raise EmbedderUnavailableError("the server names no model")
        return PurePosixPath(str(path)).name

    async def embed_query(self, text: str) -> list[float]:
        """The question's vector, within one `query_timeout_seconds`
        deadline for every call it makes, or unavailable."""
        try:
            return await asyncio.wait_for(self._query(text), self._query_timeout)
        except TimeoutError as exc:
            raise EmbedderUnavailableError("the query timed out") from exc

    async def _query(self, text: str) -> list[float]:
        if self._query_model_file is not None:
            now = self._clock()
            fresh = (
                self._served_ok_at is not None
                and now - self._served_ok_at < SERVED_MODEL_TTL_SECONDS
            )
            if not fresh:
                self._served_ok_at = None
                served = await self.served_model()
                if served != self._query_model_file:
                    raise EmbedderUnavailableError("the server serves another model")
                self._served_ok_at = now
        return (await self._embed_text(text, "query")).vector

    async def embed_document(self, text: str) -> TextVector:
        """`text`'s vector, and whether only its head fitted (`truncated`):
        the worker counts that, which is the signal for chunking."""
        return await self._embed_text(text, "document")

    async def embed_image(self, data: bytes) -> list[float]:
        sendable = await asyncio.to_thread(_as_sendable, data)
        payload = [base64.b64encode(sendable).decode()]
        marker = await self._media_marker()
        try:
            return await self._embed({"prompt_string": image_prompt(marker),
                                      "multimodal_data": payload})
        except (EmbedderRejectedError, EmbedderUnavailableError):
            # A restarted server may know another marker: read it again, and
            # retry once if it did change.
            self._marker = None
            fresh = await self._media_marker()
            if fresh == marker:
                raise
        return await self._embed({"prompt_string": image_prompt(fresh),
                                  "multimodal_data": payload})

    async def _embed_text(self, text: str, role: str) -> TextVector:
        prompt, truncated = await self._fitted(text, role)
        return TextVector(await self._embed({"prompt_string": prompt}), truncated)

    async def _fitted(self, text: str, role: str) -> tuple[str, bool]:
        """`text`'s prompt, cut until it fits `TOKEN_BUDGET` tokens by the
        server's own count, and whether it was cut."""
        if len(text) <= SAFE_TEXT_CHARS:
            return text_prompt(text, role), False
        whole = len(text)
        for _ in range(4):
            prompt = text_prompt(text, role)
            resp = await self._call("POST", "/tokenize", {
                "content": prompt, "add_special": True, "parse_special": True,
            })
            tokens = resp.get("tokens") if isinstance(resp, dict) else None
            if not isinstance(tokens, list):
                raise EmbedderUnavailableError("a /tokenize answer with no tokens")
            if len(tokens) <= TOKEN_BUDGET:
                return prompt, len(text) < whole
            text = text[:max(SAFE_TEXT_CHARS, int(len(text) * TOKEN_BUDGET / len(tokens) * 0.95))]
            if len(text) <= SAFE_TEXT_CHARS:
                break
        return text_prompt(text[:SAFE_TEXT_CHARS], role), True

    async def _media_marker(self) -> str:
        """The server's multimodal marker (/props `media_marker`). A server
        not pinned by LLAMA_MEDIA_MARKER picks one per start; it is read once
        and read again after any failed image call."""
        if self._marker is None:
            props = await self._call("GET", "/props", None)
            named = props.get("media_marker") if isinstance(props, dict) else None
            self._marker = str(named) if named else _DEFAULT_MARKER
        return self._marker

    async def _embed(self, content: dict[str, Any]) -> list[float]:
        resp = await self._call("POST", "/embedding", {"content": content})
        row = resp[0] if isinstance(resp, list) and resp else resp
        if not isinstance(row, dict) or "embedding" not in row:
            raise EmbedderUnavailableError("an answer with no embedding")
        return _normalised(row["embedding"])

    async def _healthy(self) -> bool:
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session,
                session.get(self._base + "/health") as resp,
            ):
                return resp.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

    async def _call(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        status: int | None = None
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.request(method, self._base + path, json=body) as resp,
            ):
                status = resp.status
                if status == 200:
                    return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            # ValueError: an answer that is not JSON.
            raise EmbedderUnavailableError(type(exc).__name__) from exc
        if 400 <= status < 500:
            raise EmbedderRejectedError(f"HTTP {status}")
        # A 5xx from a server that is still healthy is about this input.
        if status >= 500 and await self._healthy():
            raise EmbedderRejectedError(f"HTTP {status}")
        raise EmbedderUnavailableError(f"HTTP {status}")
