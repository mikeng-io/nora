"""GPT Image through Codex's authenticated Images API.

Prompts go directly to the renderer without a text-model rewrite. References
remain custody bytes, never external URLs. Only decoded PNG results are ok;
missing, malformed or wrong-format results are unavailable (R-27).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Sequence
from typing import Literal

import aiohttp

from nora.domain.ports import (
    ImageReference,
    ImageResult,
    ImageSize,
    ReferenceResolver,
)

from .openai_codex_oauth import OpenAICodexOAuth
from .xai_adapter import _hex_handle, _reference_limit

log = logging.getLogger("nora.image_gen.openai_codex")

PROVIDER = "openai_codex"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: OpenAI documents «up to 16 images» for GPT image models on `images/edits`.
#: The Codex lane has no published limit of its own; the documented number is the
#: contract.
CODEX_MAX_REFERENCE_IMAGES = 16

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


class OpenAICodexImageGenerator:
    """Satisfies `nora.domain.ports.ImageGenerator` (see the module docstring)."""

    def __init__(
        self,
        *,
        oauth: OpenAICodexOAuth,
        model: str = "gpt-image-2.6-flare",
        responses_model: str = "gpt-5.5",
        standard_quality: str = "medium",
        high_quality: str = "high",
        timeout_seconds: float = 180.0,
        base_url: str = CODEX_BASE_URL,
        max_reference_images: int | None = None,
    ) -> None:
        self._max_refs = _reference_limit(max_reference_images, CODEX_MAX_REFERENCE_IMAGES)
        self._oauth = oauth
        self._model = model
        self._standard_quality = standard_quality
        self._high_quality = high_quality
        self._timeout = timeout_seconds
        self._url = f"{base_url.rstrip('/')}/images"

    @property
    def max_reference_images(self) -> int:
        return self._max_refs

    def _unavailable(self, reason: str) -> ImageResult:
        return ImageResult(status="unavailable", reason=reason, provider=PROVIDER)

    async def generate(
        self,
        *,
        prompt: str,
        reference_images: Sequence[ImageReference] | None = None,
        size: ImageSize | None = None,
        quality: Literal["standard", "quality"] | None = None,
        reference_resolver: ReferenceResolver | None = None,
    ) -> ImageResult:
        if not prompt.strip():
            return ImageResult(status="nothing_found", reason="empty_prompt")

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
                        status="nothing_found", reason="unknown_reference", provider=PROVIDER,
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
        if images:
            payload["images"] = images
        endpoint = "edits" if images else "generations"

        auth = await self._oauth.access_token()
        if auth.status != "ok":
            return self._unavailable(auth.reason or "auth")
        status, body = await self._post(payload, auth.access_token, auth.account_id, endpoint)
        if status == 401:
            # A token the issuer revoked early: refresh once and retry once.
            auth = await self._oauth.access_token(rejected=auth.access_token)
            if auth.status != "ok":
                return self._unavailable(auth.reason or "auth")
            status, body = await self._post(payload, auth.access_token, auth.account_id, endpoint)
        if status is None:
            return self._unavailable("down")
        if status == 200:
            return self._read_image(body)

        try:
            parsed: object = json.loads(body)
        except ValueError:
            parsed = None
        code = _error_code(parsed)
        if _refusal(code):
            return ImageResult(status="nothing_found", reason="refused", provider=PROVIDER)
        if status == 401:
            return self._unavailable("auth")
        if "usage_limit" in code or "quota" in code:
            # ChatGPT's plan limit answers `usage_limit_reached`.
            return self._unavailable("quota")
        if status == 429:
            return self._unavailable("rate_limited")
        if status == 403:
            return self._unavailable("auth" if "auth" in code or "entitle" in code
                                     else "unavailable")
        if status in (400, 422):
            log.warning("openai_codex: request refused (HTTP %s %s)", status, code)
            return self._unavailable("bad_request")
        return self._unavailable("down")

    async def _post(
        self, payload: dict[str, object], token: str, account_id: str, endpoint: str,
    ) -> tuple[int | None, bytes]:
        headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "originator": "codex_cli_rs",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(f"{self._url}/{endpoint}", headers=headers, json=payload) as resp,
            ):
                return resp.status, await resp.read()
        except (aiohttp.ClientError, TimeoutError):
            return None, b""

    def _read_image(self, body: bytes) -> ImageResult:
        try:
            response = json.loads(body)
        except ValueError:
            return self._unavailable("bad_response")
        if not isinstance(response, dict):
            return self._unavailable("bad_response")
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
        if not raw.startswith(_PNG_MAGIC):
            return self._unavailable("bad_response")
        return ImageResult(
            status="ok",
            artifact=f"data:image/png;base64,{b64}",
            provider=PROVIDER,
            raw=raw,
            media_type="image/png",
        )

    async def health(self) -> bool:
        state = await self._oauth.access_token()
        return state.status == "ok" and bool(state.access_token)
