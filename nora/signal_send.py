"""signal-cli JSON-RPC send client (DESIGN.md §5.1)."""

import logging

import aiohttp

log = logging.getLogger("nora.signal")


class SignalSender:
    def __init__(self, session: aiohttp.ClientSession, base_url: str):
        self.session = session
        self.url = base_url.rstrip("/") + "/api/v1/rpc"

    async def send_group(self, group_id: str, message: str) -> bool:
        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": {"groupId": group_id, "message": message},
            "id": 1,
        }
        try:
            async with self.session.post(self.url, json=payload) as r:
                body = await r.json()
        except Exception as e:  # noqa: BLE001 — never crash the listener on a send
            log.error("send failed: %s", e)
            return False
        if "error" in body:
            log.error("send rpc error: %s", body["error"])
            return False
        log.info("sent to group %s (ts=%s)", group_id, (body.get("result") or {}).get("timestamp"))
        return True
