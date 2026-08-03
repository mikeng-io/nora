"""Best-effort Honcho v3 write path (spec §C.5's corrected session model).

Honcho is never load-bearing: every method here logs and returns a benign value
(`None`, `False`, or a no-op) on any failure so a Honcho outage never blocks the
ambient reply path.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("nora.honcho")


class HonchoClient:
    def __init__(
        self,
        base_url: str,
        workspace_id: str,
        *,
        feed_timeout: float = 2.0,
        card_timeout: float = 3.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._ws = workspace_id
        self._feed_timeout = feed_timeout
        self._card_timeout = card_timeout
        self._workspace_ready = False
        self._known_peers: set[str] = set()
        self._sessions: dict[str, str] = {}

    def _url(self, path: str) -> str:
        return f"{self._base}/v3/workspaces/{self._ws}{path}"

    async def _post(self, url: str, payload: dict, timeout: float) -> dict | None:
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
                session.post(url, json=payload) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho POST %s -> %s", url, r.status)
                    return None
                return await r.json()
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho POST %s failed: %s", url, e)
            return None

    async def _get(self, url: str, timeout: float) -> dict | None:
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
                session.get(url) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho GET %s -> %s", url, r.status)
                    return None
                return await r.json()
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho GET %s failed: %s", url, e)
            return None

    async def ensure_workspace(self) -> None:
        if self._workspace_ready:
            return
        result = await self._post(
            f"{self._base}/v3/workspaces", {"id": self._ws}, self._card_timeout
        )
        if result is not None:
            self._workspace_ready = True

    async def ensure_peer(self, peer_id: str) -> None:
        if peer_id in self._known_peers:
            return
        result = await self._post(self._url("/peers"), {"id": peer_id}, self._card_timeout)
        if result is not None:
            self._known_peers.add(peer_id)

    async def ensure_session(self, peer_id: str) -> str | None:
        cached = self._sessions.get(peer_id)
        if cached is not None:
            return cached
        result = await self._post(
            self._url(f"/peers/{peer_id}/sessions"), {}, self._feed_timeout
        )
        session_id = result.get("id") if result is not None else None
        if not session_id:
            return None
        self._sessions[peer_id] = session_id
        return session_id

    async def feed(self, peer_id: str, text: str) -> bool:
        session_id = await self.ensure_session(peer_id)
        if session_id is None:
            return False
        result = await self._post(
            self._url(f"/sessions/{session_id}/messages"),
            {"messages": [{"content": text, "peer_id": peer_id}]},
            self._feed_timeout,
        )
        return result is not None

    async def peer_card(self, peer_id: str) -> list[str]:
        """Durable recall about a peer. [] on any failure — a missing card is normal."""
        result = await self._get(self._url(f"/peers/{peer_id}/card"), self._card_timeout)
        content = (result or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            return []
        return [line.lstrip("-* ").strip() for line in content.splitlines() if line.strip()]

    async def health(self) -> dict:
        result = await self._get(f"{self._base}/health", self._card_timeout)
        return {"reachable": result is not None}
