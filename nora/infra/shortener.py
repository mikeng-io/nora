"""`LinkShortener` — minting nora.example.net short links via `drop`'s internal
`POST /api/links` (Nora Drop spec §5, cutover 2026-08-12; was n.example.net's
Cloudflare Worker before that — same contract, this class did not change).

One POST against Nora's OWN service (self-hosted so no third party sees what
the group routes to), and NEVER load-bearing: every failure — transport,
non-200, a body that is not the minted shape — returns None and the caller
keeps the long URL. A link that stays long is a cosmetic miss; a reply that
fails over a shortener would be R-5 with extra steps.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger("nora.shortener")

__all__ = ["LinkShortener"]


class LinkShortener:
    """Constructed once per process by the composition root, held by
    `ToolRegistry` the way the route client is. `token` is the mint bearer —
    resolved from the env by NAME there (DEC-8), never read here."""

    def __init__(
        self, *, base_url: str, token: str, timeout_seconds: float = 5.0,
    ) -> None:
        self._mint_url = base_url.rstrip("/") + "/api/links"
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def shorten(self, url: str) -> str | None:
        """The short URL for `url`, or None — never raises, never retries (a
        mint is worth one attempt; the long URL is always a fine answer)."""
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    self._mint_url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"url": url},
                ) as response,
            ):
                if response.status != 200:
                    log.warning("shorten refused (http %s)", response.status)
                    return None
                body: Any = await response.json(content_type=None)
        except asyncio.CancelledError:
            raise                       # shutdown is not a mint failure
        except (TimeoutError, aiohttp.ClientError) as exc:
            log.warning("shorten unreachable (%s)", type(exc).__name__)
            return None
        except Exception:
            log.exception("shorten failed")
            return None
        short = body.get("short_url") if isinstance(body, dict) else None
        return short if isinstance(short, str) and short.startswith("https://") else None
