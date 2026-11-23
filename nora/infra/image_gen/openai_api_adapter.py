"""GPT Image through the OpenAI platform API, billed per token to the
project that owns the API key. The wire is `gpt_image`; this lane adds the
key. A project budget, not this code, is what caps the spend.
"""

from __future__ import annotations

import aiohttp

from nora.domain.ports import ImageResult

from .gpt_image import GptImageGenerator

PROVIDER = "openai"
OPENAI_API_BASE_URL = "https://api.openai.com/v1"


class OpenAIImageGenerator(GptImageGenerator):
    """Satisfies `nora.domain.ports.ImageGenerator` (see the module docstring)."""

    provider = PROVIDER

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-image-2.6-flare",
        standard_quality: str = "medium",
        high_quality: str = "high",
        timeout_seconds: float = 180.0,
        base_url: str = OPENAI_API_BASE_URL,
        max_reference_images: int | None = None,
    ) -> None:
        super().__init__(
            model=model,
            standard_quality=standard_quality,
            high_quality=high_quality,
            timeout_seconds=timeout_seconds,
            max_reference_images=max_reference_images,
        )
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/images"

    async def _send(
        self, payload: dict[str, object], endpoint: str,
    ) -> tuple[int | None, bytes] | ImageResult:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
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

    async def health(self) -> bool:
        """A key is present; whether it is live is only known from a call."""
        return bool(self._api_key)
