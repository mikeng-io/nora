"""OpenAI's GPT Image wire, shared by every lane that reaches it.

`images/generations` and `images/edits` take the same JSON on the ChatGPT
(Codex) backend and on the platform API; a lane differs only in how it
authenticates (`_send`). Prompts go to the renderer without a text-model
rewrite. References remain custody bytes, never external URLs. Only decoded
PNG results are ok; missing, malformed or wrong-format results are
unavailable.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Sequence
from typing import Literal

from nora.domain.ports import (
    ImageReference,
    ImageResult,
    ImageSize,
    ReferenceResolver,
)

from .xai_adapter import _hex_handle, _reference_limit

log = logging.getLogger("nora.image_gen.gpt_image")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: OpenAI documents «up to 16 images» for GPT image models on `images/edits`.
GPT_IMAGE_MAX_REFERENCE_IMAGES = 16
#: The API takes `frame_count` 2..16 for GIF output (gpt-image-2.6 onward).
GPT_IMAGE_MAX_GIF_FRAMES = 16
_GIF_MAGIC = (b"GIF87a", b"GIF89a")

# ponytail: `ImageSize.tier` is not mapped;
# the output may ignore even the size asked for, so a tier knob would
# be decoration. Map it if a provider release starts honouring sizes.
_SIZE_BY_ASPECT = {
    "1:1": "1024x1024",
    "16:9": "1536x864",
    "3:2": "1536x1024",
    "9:16": "864x1536",
    "2:3": "1024x1536",
}


def _media_type(body: bytes) -> str:
    if body.startswith(_PNG_MAGIC):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


def _error_code(body: object) -> str:
    """The provider's error code/type, lowercased, wherever it sits."""
    if not isinstance(body, dict):
        return ""
    err = body.get("error") if isinstance(body.get("error"), dict) else body
    if not isinstance(err, dict):
        return ""
    parts = [err.get("code"), err.get("type"), body.get("detail")]
    return " ".join(str(p) for p in parts if p).lower()


def _refusal(code: str) -> bool:
    return any(word in code for word in ("moderation", "safety", "content_policy"))


class GptImageGenerator:
    """Satisfies `nora.domain.ports.ImageGenerator` once a lane supplies `_send`."""

    provider = ""

    def __init__(
        self,
        *,
        model: str,
        standard_quality: str,
        high_quality: str,
        timeout_seconds: float,
        max_reference_images: int | None,
        max_gif_frames: int = 0,
    ) -> None:
        self._max_refs = _reference_limit(max_reference_images, GPT_IMAGE_MAX_REFERENCE_IMAGES)
        # Below the API's minimum of two frames a GIF cannot be asked for at all.
        cap = min(max_gif_frames, GPT_IMAGE_MAX_GIF_FRAMES)
        self._max_gif_frames = cap if cap >= 2 else 0
        self._model = model
        self._standard_quality = standard_quality
        self._high_quality = high_quality
        self._timeout = timeout_seconds

    @property
    def max_reference_images(self) -> int:
        return self._max_refs

    @property
    def max_gif_frames(self) -> int:
        """0 when the configured model makes stills only."""
        return self._max_gif_frames

    def _unavailable(self, reason: str) -> ImageResult:
        return ImageResult(status="unavailable", reason=reason, provider=self.provider)

    async def _send(
        self, payload: dict[str, object], endpoint: str,
    ) -> tuple[int | None, bytes] | ImageResult:
        """`(status, body)`, `(None, b"")` if never completed, or a refusal."""
        raise NotImplementedError

    async def generate(
        self,
        *,
        prompt: str,
        reference_images: Sequence[ImageReference] | None = None,
        size: ImageSize | None = None,
        quality: Literal["standard", "quality"] | None = None,
        reference_resolver: ReferenceResolver | None = None,
        gif_frames: int | None = None,
    ) -> ImageResult:
        if not prompt.strip():
            return ImageResult(status="nothing_found", reason="empty_prompt")
        if gif_frames and not self._max_gif_frames:
            return self._unavailable("no_gif")

        images: list[dict[str, str]] = []
        refs = reference_images or ()
        if refs:
            if reference_resolver is None:
                return ImageResult(status="unavailable", reason="no_reference_resolver")
            for ref in refs:
                marker = _hex_handle(ref.marker_code)
                if len(marker) < 6:
                    return ImageResult(status="nothing_found", reason="bad_reference")
                try:
                    data = await reference_resolver(marker)
                except Exception:
                    log.exception("image_gen reference resolver raised")
                    data = None
                if not data:
                    return ImageResult(
                        status="nothing_found", reason="unknown_reference",
                        provider=self.provider,
                    )
                images.append({
                    "image_url": f"data:{_media_type(data)};base64,"
                    + base64.b64encode(data).decode("ascii"),
                })

        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "output_format": "png",
            "quality": self._high_quality if quality == "quality" else self._standard_quality,
            "size": _SIZE_BY_ASPECT.get(size.aspect_ratio, "auto") if size else "auto",
        }
        if gif_frames:
            # A GIF renders its frames on one sheet; "auto" picks a size the sheet fits.
            frames = min(max(gif_frames, 2), self._max_gif_frames)
            payload.update(output_format="gif", frame_count=frames, size="auto")
        if images:
            payload["images"] = images
        sent = await self._send(payload, "edits" if images else "generations")
        if isinstance(sent, ImageResult):
            return sent
        status, body = sent
        if status is None:
            return self._unavailable("down")
        if status == 200:
            return self._read_image(body, gif=bool(gif_frames))

        try:
            parsed: object = json.loads(body)
        except ValueError:
            parsed = None
        code = _error_code(parsed)
        if _refusal(code):
            return ImageResult(status="nothing_found", reason="refused", provider=self.provider)
        if status == 401:
            return self._unavailable("auth")
        if any(w in code for w in ("usage_limit", "quota", "spend_limit", "credit_balance")):
            # ChatGPT's plan limit is `usage_limit_reached`; the platform's billing refusals
            # are `insufficient_quota`, `*_spend_limit_exceeded`, `credit_balance_exhausted`.
            return self._unavailable("quota")
        if status == 429:
            return self._unavailable("rate_limited")
        if status == 403:
            return self._unavailable("auth" if "auth" in code or "entitle" in code
                                     else "unavailable")
        if status in (400, 422):
            log.warning("%s: request refused (HTTP %s %s)", self.provider, status, code)
            return self._unavailable("bad_request")
        return self._unavailable("down")

    def _read_image(self, body: bytes, *, gif: bool) -> ImageResult:
        try:
            response = json.loads(body)
        except ValueError:
            return self._unavailable("bad_response")
        if not isinstance(response, dict):
            return self._unavailable("bad_response")
        usage = response.get("usage")
        if isinstance(usage, dict):
            # The platform lane is billed per token; this is the per-call cost.
            log.info(
                "image_gen usage via %s: input_tokens=%s output_tokens=%s",
                self.provider, usage.get("input_tokens"), usage.get("output_tokens"),
            )
        data = response.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            return self._unavailable("no_image")
        b64 = data[0].get("b64_json")
        if not isinstance(b64, str) or not b64:
            return self._unavailable("no_image")
        try:
            raw = base64.b64decode(b64, validate=True)
        except ValueError:
            return self._unavailable("bad_response")
        if not raw.startswith(_GIF_MAGIC if gif else _PNG_MAGIC):
            return self._unavailable("bad_response")
        media_type = "image/gif" if gif else "image/png"
        return ImageResult(
            status="ok",
            artifact=f"data:{media_type};base64,{b64}",
            provider=self.provider,
            raw=raw,
            media_type=media_type,
        )
