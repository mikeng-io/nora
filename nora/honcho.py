"""Honcho v3 REST client (DESIGN.md §5.2).

Honcho is best-effort: every method logs and returns a benign value on failure so a
Honcho outage never blocks a reply.
"""

import logging
import re

import aiohttp

log = logging.getLogger("nora.honcho")


def peer_id(sender_id: str) -> str:
    """Map a Signal sender ID to a Honcho peer ID.

    Honcho enforces ^[a-zA-Z0-9_-]+$ on peer IDs, so the '+' on a phone number is
    rejected (DESIGN.md §13 assumed it was accepted; the live API says otherwise).
    Strip the leading '+' and replace anything else disallowed — stable and 1:1.
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", sender_id.lstrip("+")) or "unknown"


class Honcho:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, workspace_id: str):
        self.session = session
        self.base = base_url.rstrip("/")
        self.ws = workspace_id

    async def ensure_workspace(self) -> bool:
        try:
            async with self.session.post(f"{self.base}/v3/workspaces", json={"id": self.ws}) as r:
                ok = r.status < 300
                if not ok:
                    log.warning("workspace create %s: %s", r.status, await r.text())
                return ok
        except Exception as e:  # noqa: BLE001
            log.warning("workspace create failed: %s", e)
            return False

    async def ensure_peer(self, sender_id: str) -> bool:
        try:
            url = f"{self.base}/v3/workspaces/{self.ws}/peers"
            async with self.session.post(url, json={"id": peer_id(sender_id)}) as r:
                ok = r.status < 300
                if not ok:
                    log.warning("peer create %s: %s", r.status, await r.text())
                return ok
        except Exception as e:  # noqa: BLE001
            log.warning("peer create failed: %s", e)
            return False

    async def peer_card(self, sender_id: str) -> list[str]:
        try:
            url = f"{self.base}/v3/workspaces/{self.ws}/peers/{peer_id(sender_id)}/card"
            async with self.session.get(url) as r:
                if r.status >= 300:
                    log.warning("peer card %s: %s", r.status, await r.text())
                    return []
                return (await r.json()).get("peer_card") or []
        except Exception as e:  # noqa: BLE001
            log.warning("peer card failed: %s", e)
            return []

    async def feed(self, sender_id: str, text: str) -> bool:
        """Fire-and-forget: hand the exchange to the Deriver. We ignore the response body."""
        pid = peer_id(sender_id)
        try:
            url = f"{self.base}/v3/workspaces/{self.ws}/peers/{pid}/chat"
            async with self.session.post(url, json={"query": text}) as r:
                ok = r.status < 300
                log.info("honcho feed peer=%s status=%s", pid, r.status)
                return ok
        except Exception as e:  # noqa: BLE001
            log.warning("honcho feed failed: %s", e)
            return False
