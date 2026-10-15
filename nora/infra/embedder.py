"""The one client for the memory index's embedder (design/unified-memory/
65-build-step-3-vectors.md §0, V1): Qwen3-VL-Embedding-2B served by
llama-server with `--embedding --pooling last` and its projector, the
`embedder` compose service.

Every prompt comes from `nora.domain.embedder_input`, the formatter the
bake-off adapter uses too, so production embeds exactly what the bake-off
measured. A vector comes back as `DIMENSIONS` floats, L2-normalised, so a
cosine and an inner product rank the same.

**Two failures, kept apart** (R-27's spirit, an ERROR is never a PASS):

- `EmbedderUnavailableError`: the embedder did not answer usefully (refused,
  timed out, a 5xx, an answer that is not one vector of the expected width).
  Nothing is known about the input; the caller stops for this tick.
- `EmbedderRejectedError`: the embedder refused THIS input (a 4xx), or an
  image's bytes could not be decoded. The caller backs that node off and
  moves on.

Neither ever carries the input: an exception message here names a status
or a class, never a text, because a message body is room content.

An image goes as its own bytes. llama-server decodes with stb_image, which
reads JPEG and PNG (and a GIF's first frame) but not WebP, the shape most
stickers arrive in; anything that is not JPEG or PNG is re-encoded as the PNG
of its first frame first (Pillow, in a thread).
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
from typing import Any

import aiohttp

from nora.domain.embedder_input import FORMAT_VERSION, image_prompt, text_prompt

__all__ = [
    "DIMENSIONS",
    "MAX_TEXT_CHARS",
    "EmbedderRejectedError",
    "EmbedderUnavailableError",
    "LlamaEmbedder",
    "fingerprint",
]

#: Qwen3-VL-Embedding-2B's width (65 §0: store 2048, index the first 256).
DIMENSIONS = 2048

#: The longest text sent, in characters. The server's context is 4096 tokens
#: (`docker-compose.yml`); a CJK character is about a token, and the prompt's
#: own turns take about 40.
# ponytail: a longer text is embedded from its head; chunking a node is a
# later step if a search measurably misses the tail of a long note.
MAX_TEXT_CHARS = 3500

#: The image types llama-server reads as they are (module docstring).
_SENT_AS_IS = frozenset({"image/jpeg", "image/jpg", "image/png"})

#: llama.cpp's default multimodal marker, for a server whose /props names none.
_DEFAULT_MARKER = "<__media__>"


class EmbedderUnavailableError(RuntimeError):
    """The embedder did not answer usefully; nothing is known about the input."""


class EmbedderRejectedError(RuntimeError):
    """The embedder refused this input, or its bytes could not be decoded."""


def fingerprint(model_id: str) -> str:
    """What `memory_node.embedding_model` records: the configured model id
    (the GGUF and its hash) plus the formatter's version. A vector whose
    fingerprint is not the current one is another model's (65 V1)."""
    return f"{model_id}+fmt{FORMAT_VERSION}"


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


def _as_sendable(data: bytes, media_type: str) -> bytes:
    """`data` as bytes llama-server decodes: JPEG and PNG as they are, any
    other image as the PNG of its first frame."""
    if media_type.strip().lower() in _SENT_AS_IS:
        return data
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)
            frame = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            out = io.BytesIO()
            frame.save(out, format="PNG")
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise EmbedderRejectedError(f"undecodable image ({type(exc).__name__})") from exc


class LlamaEmbedder:
    """llama-server's `/embedding`, one input per call (the bake-off's
    shape). `base_url` is the compose service (`http://embedder:8080`)."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._marker: str | None = None

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed({"prompt_string": text_prompt(text[:MAX_TEXT_CHARS], "query")})

    async def embed_document(self, text: str) -> list[float]:
        return await self._embed(
            {"prompt_string": text_prompt(text[:MAX_TEXT_CHARS], "document")},
        )

    async def embed_image(self, data: bytes, media_type: str) -> list[float]:
        sendable = await asyncio.to_thread(_as_sendable, data, media_type)
        marker = await self._media_marker()
        return await self._embed({
            "prompt_string": image_prompt(marker),
            "multimodal_data": [base64.b64encode(sendable).decode()],
        })

    async def _media_marker(self) -> str:
        """The server's multimodal marker: a newer llama-server picks one per
        start and publishes it in /props, and the default then does not
        tokenize as a marker. Read once per process; a restarted server is a
        new marker, so a rejected image clears it (`_embed`)."""
        if self._marker is None:
            props = await self._call("GET", "/props", None)
            named = props.get("media_marker") if isinstance(props, dict) else None
            self._marker = str(named) if named else _DEFAULT_MARKER
        return self._marker

    async def _embed(self, content: dict[str, Any]) -> list[float]:
        try:
            resp = await self._call("POST", "/embedding", {"content": content})
        except EmbedderRejectedError:
            if "multimodal_data" in content:
                self._marker = None
            raise
        row = resp[0] if isinstance(resp, list) and resp else resp
        if not isinstance(row, dict) or "embedding" not in row:
            raise EmbedderUnavailableError("an answer with no embedding")
        return _normalised(row["embedding"])

    async def _call(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.request(method, self._base + path, json=body) as resp,
            ):
                if 400 <= resp.status < 500:
                    raise EmbedderRejectedError(f"HTTP {resp.status}")
                if resp.status != 200:
                    raise EmbedderUnavailableError(f"HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            # ValueError: an answer that is not JSON.
            raise EmbedderUnavailableError(type(exc).__name__) from exc
