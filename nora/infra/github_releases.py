"""Public Nora release reads; deployment identity stays in VersionManifest.

Like the external-files HTTP adapters, each operation owns its HTTP context;
the process-lifetime adapter holds no open client that needs shutdown wiring.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

RELEASES_URL = "https://api.github.com/repos/mikeng-io/nora/releases"
PAGE_SIZE = 100
TIMEOUT_SECONDS = 8.0
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _metadata(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        raise ValueError("release is not an object")
    if not isinstance(raw.get("draft"), bool) or not isinstance(raw.get("prerelease"), bool):
        raise ValueError("release publication state missing")
    if raw["draft"] or raw["prerelease"]:
        return None
    if "published_at" not in raw:
        raise ValueError("release publication date missing")
    if raw["published_at"] is None:
        return None
    fields = {"version": raw.get("tag_name"), "date": raw.get("published_at"),
              "url": raw.get("html_url")}
    if not all(isinstance(value, str) and value for value in fields.values()):
        raise ValueError("release metadata missing")
    return {key: str(value) for key, value in fields.items()}


class GitHubReleases:
    """Fixed public repository, GET only, no credentials, cache or polling."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def list(self) -> dict[str, Any]:
        # ponytail: one API page, at most 100 recent releases; exact older tags
        # remain readable. Add model-facing pagination only if history needs it.
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS,
            ) as client:
                response = await client.get(RELEASES_URL, params={"per_page": PAGE_SIZE, "page": 1},
                                            headers=_HEADERS)
                response.raise_for_status()
                raw = response.json()
            if not isinstance(raw, list):
                raise ValueError("release list is not an array")
            releases = [item for row in raw if (item := _metadata(row)) is not None]
            releases.sort(key=lambda item: item["date"], reverse=True)
            more = "next" in response.links
            return {"status": "ok" if releases or more else "nothing_found",
                    "releases": releases, "more_available": more}
        except (httpx.HTTPError, ValueError):
            return {"status": "unavailable"}

    async def read(self, version: str) -> dict[str, Any]:
        tag = version.strip()
        if not tag:
            return {"status": "unavailable"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS,
            ) as client:
                response = await client.get(f"{RELEASES_URL}/tags/{quote(tag, safe='')}",
                                            headers=_HEADERS)
                if response.status_code == 404:
                    return {"status": "nothing_found"}
                response.raise_for_status()
                raw = response.json()
            metadata = _metadata(raw)
            if metadata is None:
                return {"status": "nothing_found"}
            body = raw.get("body")
            if body is not None and not isinstance(body, str):
                raise ValueError("release body is not text")
            return {"status": "ok", **metadata, "body": body or ""}
        except (httpx.HTTPError, ValueError):
            return {"status": "unavailable"}
