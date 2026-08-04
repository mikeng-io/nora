"""The adapter seam (spec C.3, design/01). Everything platform-facing goes through this."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from ..domain.models import ConversationTarget, NormalizedMessage


@runtime_checkable
class PlatformAdapter(Protocol):
    platform: str

    async def listen(self, on_event: Callable[[dict], Awaitable[None]]) -> None: ...
    async def parse(self, raw_event: dict) -> NormalizedMessage | None: ...
    async def send(self, target: ConversationTarget, text: str) -> bool: ...
    async def health(self) -> dict: ...
