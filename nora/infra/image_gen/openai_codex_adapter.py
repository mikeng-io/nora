"""GPT Image through Codex's authenticated Images API, on Nora's own
ChatGPT login. The wire is `gpt_image`; this lane adds the OAuth headers and
one refresh-and-retry on a 401.
"""

from __future__ import annotations

import aiohttp

from nora.domain.ports import ImageResult

from .gpt_image import GptImageGenerator
from .openai_codex_oauth import OpenAICodexOAuth

PROVIDER = "openai_codex"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class OpenAICodexImageGenerator(GptImageGenerator):
    """Satisfies `nora.domain.ports.ImageGenerator` (see the module docstring)."""

    provider = PROVIDER

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
        super().__init__(
            model=model,
            standard_quality=standard_quality,
            high_quality=high_quality,
            timeout_seconds=timeout_seconds,
            max_reference_images=max_reference_images,
        )
        self._oauth = oauth
        self._url = f"{base_url.rstrip('/')}/images"

    async def _send(
        self, payload: dict[str, object], endpoint: str,
    ) -> tuple[int | None, bytes] | ImageResult:
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
        return status, body

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

    async def health(self) -> bool:
        state = await self._oauth.access_token()
        return state.status == "ok" and bool(state.access_token)
