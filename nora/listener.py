"""SSE client for signal-cli /api/v1/events, with reconnect backoff (DESIGN.md §10)."""

import asyncio
import json
import logging

import aiohttp

log = logging.getLogger("nora.listener")


async def listen(session: aiohttp.ClientSession, base_url: str, on_event) -> None:
    """Consume the SSE stream forever, calling on_event(dict) per JSON payload."""
    url = base_url.rstrip("/") + "/api/v1/events"
    backoff = 1
    while True:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=None)) as r:
                r.raise_for_status()
                log.info("SSE connected to %s", url)
                backoff = 1
                buf: list[str] = []
                async for raw in r.content:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        buf.append(line[5:].lstrip())
                    elif not line and buf:
                        payload, buf = "\n".join(buf), []
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            log.warning("unparseable SSE payload: %.200s", payload)
                            continue
                        try:
                            await on_event(event)
                        except Exception:  # noqa: BLE001 — one bad message must not kill the loop
                            log.exception("handler error")
            log.warning("SSE stream ended")
        except Exception as e:  # noqa: BLE001
            log.error("SSE error: %s", e)
        log.info("reconnecting in %ss", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)
