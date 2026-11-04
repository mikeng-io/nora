"""Read Nora's public GitHub releases; running identity stays in VersionManifest."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp

REPOSITORY = "https://api.github.com/repos/mikeng-io/nora"


class GithubReleases:
    def __init__(self, *, base_url: str = REPOSITORY, timeout_seconds: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def _get(self, path: str) -> dict[str, Any]:
        url = self._base + path
        checked = datetime.now(UTC).isoformat()
        result: dict[str, Any] = {"source": "github", "observed_at": checked, "api_url": url}
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.get(url, headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "Nora-release-reader"},
                            allow_redirects=False) as response,
            ):
                if response.status == 404:
                    return {**result, "status": "nothing_found"}
                if response.status != 200:
                    return {**result, "status": "unavailable", "reason": "http_error"}
                payload = await response.json()
            return {**result, "status": "ok", "payload": payload}
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return {**result, "status": "unavailable", "reason": "read_failed"}

    @staticmethod
    def _entry(raw: object, *, notes: bool = False) -> dict[str, Any] | None:
        if not isinstance(raw, dict) or raw.get("draft") is not False:
            return None
        tag, published = raw.get("tag_name"), raw.get("published_at")
        if not isinstance(tag, str) or not tag or not isinstance(published, str) or not published:
            return None
        if not isinstance(raw.get("prerelease"), bool):
            return None
        entry: dict[str, Any] = {
            "version": tag, "published_at": published, "prerelease": raw["prerelease"],
            "url": "https://github.com/mikeng-io/nora/releases/tag/" + quote(tag, safe=""),
        }
        if notes:
            body = raw.get("body")
            if body is not None and not isinstance(body, str):
                return None
            entry.update(notes=(body or "")[:8000], notes_truncated=len(body or "") > 8000)
        return entry

    async def list(self, *, history: bool = False) -> dict[str, Any]:
        result = await self._get("/releases?per_page=20" if history else "/releases/latest")
        if result["status"] != "ok":
            if result["status"] == "nothing_found":
                return {**result, "status": "unavailable", "reason": "catalogue_missing"}
            return result
        raw = result.pop("payload")
        if history:
            if not isinstance(raw, list):
                return {**result, "status": "unavailable", "reason": "invalid_response"}
            entries = [self._entry(item) for item in raw[:20]]
            if any(item is None for item in entries):
                return {**result, "status": "unavailable", "reason": "invalid_response"}
            return {**result, "status": "ok" if entries else "nothing_found",
                    "releases": entries, "history_complete": len(raw) < 20}
        entry = self._entry(raw)
        if entry is None or entry["prerelease"]:
            return {**result, "status": "unavailable", "reason": "invalid_response"}
        return {**result, "latest": entry}

    async def read(self, version: str) -> dict[str, Any]:
        tag = version.strip()
        if re.fullmatch(r"[vV]?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)*", tag):
            tag = "v" + tag.lstrip("vV")
        elif not re.fullmatch(r"V\d+", tag):
            return {"status": "unavailable", "source": "github", "reason": "invalid_version"}
        result = await self._get("/releases/tags/" + quote(tag, safe=""))
        if result["status"] != "ok":
            return result
        entry = self._entry(result.pop("payload"), notes=True)
        if entry is None or entry["version"] != tag:
            return {**result, "status": "unavailable", "reason": "invalid_response"}
        return {**result, **entry}


def compare_versions(running: str, released: str) -> str:
    """Only stable semantic versions support an upgrade ordering."""
    pattern = r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    left, right = re.fullmatch(pattern, running), re.fullmatch(pattern, released)
    if left is None or right is None:
        return "unknown"
    a, b = tuple(map(int, left.groups())), tuple(map(int, right.groups()))
    return "same" if a == b else "behind" if a < b else "ahead"
