"""OpenAI gpt-image through the Codex (ChatGPT subscription) lane.

DEC-304: the second `ImageGenerator`. It speaks the
wire the `codex` CLI speaks: a streamed Responses call to
`chatgpt.com/backend-api/codex/responses`, where a text model (`gpt-5.5`)
hosts the `image_generation` tool (`gpt-image-2`). The image arrives as the
`result` (base64) of the `response.output_item.done` event whose item is an
`image_generation_call`.

**A 200 is not an image.** The host model can answer in text and the stream
still ends `response.completed`. `tool_choice` forces the tool, and the adapter still counts only a
completed
`image_generation_call` with decodable PNG bytes as `ok`; anything else is
`unavailable` (`no_image`), never a silent success.

**Size and quality are requests, not guarantees**: the output can differ in
dimensions and quality from what was asked. The host model sets the tool's
parameters itself, so the aspect also goes into its instructions. Nothing
downstream reads the dimensions.

NEVER raises (R-27). Reference images are custody handles resolved only
through the injected `reference_resolver`, sent as `input_image` data URIs;
there is no URL path. `allow_reference_images` is enforced by the tool
(`nora.agent.tools`), the same as for xAI.
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

# gpt-image takes three fixed sizes. ponytail: `ImageSize.tier` is not mapped;
# the output may ignore even the size asked for, so a tier knob would
# be decoration. Map it if a provider release starts honouring sizes.
_SIZE_BY_ASPECT = {
    "1:1": "1024x1024",
    "16:9": "1536x1024",
    "3:2": "1536x1024",
    "9:16": "1024x1536",
    "2:3": "1024x1536",
}

_INSTRUCTIONS = (
    "You are an image renderer. Call the image_generation tool exactly once, "
    "with the user's prompt passed through as written: do not shorten it, add "
    "to it or answer in text instead."
)


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
        model: str = "gpt-image-2",
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
        self._responses_model = responses_model
        self._standard_quality = standard_quality
        self._high_quality = high_quality
        self._timeout = timeout_seconds
        self._url = f"{base_url.rstrip('/')}/responses"

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

        content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
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
                content.append({
                    "type": "input_image",
                    "image_url": f"data:{_media_type(data)};base64,"
                    + base64.b64encode(data).decode("ascii"),
                })

        tool: dict[str, str] = {
            "type": "image_generation",
            "model": self._model,
            "output_format": "png",
            "quality": self._high_quality if quality == "quality" else self._standard_quality,
            "size": _SIZE_BY_ASPECT.get(size.aspect_ratio, "auto") if size else "auto",
        }
        instructions = _INSTRUCTIONS
        if size is not None and size.aspect_ratio in _SIZE_BY_ASPECT:
            instructions += f" Use the aspect ratio {size.aspect_ratio}."
        payload = {
            "model": self._responses_model,
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            "tools": [tool],
            "tool_choice": {"type": "image_generation"},
            "store": False,
            "stream": True,
        }

        auth = await self._oauth.access_token()
        if auth.status != "ok":
            return self._unavailable(auth.reason or "auth")
        status, body = await self._post(payload, auth.access_token, auth.account_id)
        if status == 401:
            # A token the issuer revoked early: refresh once and retry once.
            auth = await self._oauth.access_token(rejected=auth.access_token)
            if auth.status != "ok":
                return self._unavailable(auth.reason or "auth")
            status, body = await self._post(payload, auth.access_token, auth.account_id)
        if status is None:
            return self._unavailable("down")
        if status == 200:
            return self._read_stream(body)

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
        self, payload: dict[str, object], token: str, account_id: str,
    ) -> tuple[int | None, bytes]:
        headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(self._url, headers=headers, json=payload) as resp,
            ):
                # The whole stream, not readline(): the image is ONE
                # `data:` line of ~2-3 MB, past aiohttp's line limit.
                return resp.status, await resp.read()
        except (aiohttp.ClientError, TimeoutError):
            return None, b""

    def _read_stream(self, body: bytes) -> ImageResult:
        image: dict[str, object] | None = None
        failure = ""
        for line in body.split(b"\n"):
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            item = event.get("item")
            if (
                kind == "response.output_item.done"
                and isinstance(item, dict)
                and item.get("type") == "image_generation_call"
            ):
                image = item
            elif kind in ("response.failed", "error", "response.incomplete"):
                response = event.get("response")
                source = response if isinstance(response, dict) else event
                failure = _error_code(source) or str(kind)

        if image is None:
            if _refusal(failure):
                return ImageResult(status="nothing_found", reason="refused", provider=PROVIDER)
            if failure:
                log.warning("openai_codex: stream ended without an image (%s)", failure)
            return self._unavailable("no_image")
        if image.get("status") != "completed":
            if _refusal(failure) or _refusal(str(image.get("status") or "")):
                return ImageResult(status="nothing_found", reason="refused", provider=PROVIDER)
            return self._unavailable("no_image")
        b64 = image.get("result")
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
