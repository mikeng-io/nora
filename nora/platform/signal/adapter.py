"""SignalAdapter — the one PlatformAdapter implementation in v2 (spec C.3)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from ...models import ConversationTarget, NormalizedMessage
from .listener import SignalListener
from .parser import parse_event
from .send import SignalSender


class SignalAdapter:
    platform = "signal"

    def __init__(self, base_url: str, account: str) -> None:
        self._account = account
        self._listener = SignalListener(base_url)
        self._sender = SignalSender(base_url)

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        await self._listener.listen(on_event)

    async def parse(self, raw_event: dict) -> NormalizedMessage | None:
        return parse_event(raw_event, self._account)

    async def send(self, target: ConversationTarget, text: str, *, request_id: str = "") -> bool:
        return await self._sender.send(target, text, request_id=request_id)

    async def health(self) -> dict:
        last = self._listener.last_event_at
        return {
            "connected": self._listener.connected,
            "last_event_age_seconds": None if last is None else time.monotonic() - last,
            "account": self._account,
        }
