"""SignalAdapter — the one PlatformAdapter implementation in v2 (spec C.3)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from ...domain.models import ConversationTarget, NormalizedMessage
from ...domain.ports import AdapterHealth, PlatformIdentity, SendResult
from .account import derive_aliases
from .listener import SignalListener
from .parser import parse_event
from .send import SignalSender


class SignalAdapter:
    platform = "signal"

    def __init__(self, base_url: str, account: str) -> None:
        self._base_url = base_url
        self._account = account
        self._listener = SignalListener(base_url)
        self._sender = SignalSender(base_url)
        self._identity: PlatformIdentity | None = None

    async def resolve_identity(self) -> PlatformIdentity:
        """Memoized after the first successful call: `resolve_identity` is meant to
        be a stable, idempotent answer to "who am I" (see the port's own module
        docstring), not a fresh daemon round-trip every time — `nora.obs.health`
        calls this on every `/health` poll to fill in `sse.account` (Trap A), and a
        network hit on every poll would defeat the point of asking a question whose
        answer does not change while the process is alive.
        """
        if self._identity is None:
            aliases = await derive_aliases(self._base_url, self._account)
            self._identity = PlatformIdentity(
                platform=self.platform, primary=self._account, aliases=aliases,
            )
        return self._identity

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        await self._listener.listen(on_event)

    async def parse(self, raw_event: dict) -> NormalizedMessage | None:
        return parse_event(raw_event, self._account)

    async def send(self, *, target: ConversationTarget, text: str) -> SendResult:
        return await self._sender.send(target, text)

    async def health(self) -> AdapterHealth:
        last = self._listener.last_event_at
        return AdapterHealth(
            platform=self.platform,
            connected=self._listener.connected,
            last_event_age_seconds=None if last is None else time.monotonic() - last,
        )
