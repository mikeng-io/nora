"""signal-cli JSON-RPC send (spec C.3). Returns bool; never raises to the caller."""

from __future__ import annotations

import logging
import os

import aiohttp

from ...models import ConversationTarget

log = logging.getLogger("nora.signal.send")

_PYTEST_GUARD = "PYTEST_CURRENT_TEST" not in os.environ


class SignalSender:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def send(self, target: ConversationTarget, text: str, *, request_id: str = "") -> bool:
        if target.conversation_type != "group":
            log.error("refusing to send to conversation_type=%r (v2 is groups only)",
                      target.conversation_type)
            return False
        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": {"groupId": target.conversation_id, "message": text},
            "id": request_id or "nora",
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(f"{self._base_url}/api/v1/rpc", json=payload) as resp:
                    if resp.status != 200:
                        log.error("send failed: HTTP %s (conversation=%s, id=%s)",
                                  resp.status, target.conversation_id, request_id)
                        return False
                    body = await resp.json()
        except Exception as exc:               # noqa: BLE001 — a send failure is never fatal
            log.error("send failed: %s (conversation=%s, id=%s)",
                      type(exc).__name__, target.conversation_id, request_id)
            return False
        if isinstance(body, dict) and body.get("error"):
            log.error("send rejected by signal-cli (conversation=%s, id=%s)",
                      target.conversation_id, request_id)
            return False
        log.info("sent %d chars to %s (id=%s)", len(text), target.conversation_id, request_id)
        return True
