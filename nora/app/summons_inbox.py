"""`SummonsInbox` — the bounded in-process hand-off between the Instagram
adapter's SSE loop and the summons consumer.

It satisfies `nora.domain.summons.SummonsSink`, so the platform layer holds
only that port and never learns this module exists. Composition ALWAYS builds
one and threads it into the adapter: when `[social.instagram.summons]` is off
(or `[social.instagram]` is), `enabled` is False and every card is counted
`disabled` and dropped, so a summons Nora is not answering is still visible
on `nora_summons_intake_total` rather than silently absent.

**Bounded, and it drops the OLDEST.** The consumer takes one card at a time,
and a checkpointed session makes it back off for up to half an hour; cards
arriving meanwhile wait here. Past `maxsize` the oldest queued card is pushed
out and counted `overflow`: an old card is the one closest to `stale` anyway,
and the SSE loop must never block on a slow consumer (it carries her group
chats). A dropped card leaves no ledger row; the counter is its only trace.

`offer` and `malformed` never block and never raise: the SSE loop calls them
inline.
"""

from __future__ import annotations

import asyncio
import collections

from nora.domain.summons import SummonsCard
from nora.obs.metrics import Metrics

#: Cards held for the consumer. A summons is a person tagging her in a
#: comment, minutes apart at most; 32 is far past any burst the account sees.
DEFAULT_MAXSIZE = 32


class SummonsInbox:
    """Satisfies `nora.domain.summons.SummonsSink`."""

    def __init__(self, *, enabled: bool, metrics: Metrics,
                 maxsize: int = DEFAULT_MAXSIZE) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self._enabled = enabled
        self._metrics = metrics
        self._cards: collections.deque[SummonsCard] = collections.deque()
        self._maxsize = maxsize
        self._ready = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __len__(self) -> int:
        return len(self._cards)

    def offer(self, card: SummonsCard) -> None:
        if not self._enabled:
            self._metrics.inc("nora_summons_intake_total", outcome="disabled")
            return
        if len(self._cards) >= self._maxsize:
            self._cards.popleft()
            self._metrics.inc("nora_summons_intake_total", outcome="overflow")
        self._cards.append(card)
        self._metrics.inc("nora_summons_intake_total", outcome="queued")
        self._ready.set()

    def malformed(self) -> None:
        self._metrics.inc("nora_summons_intake_total", outcome="malformed")

    async def get(self, wait_seconds: float | None = None) -> SummonsCard | None:
        """The oldest queued card, waiting up to `wait_seconds` for one
        (forever when `None`); `None` when the wait ran out."""
        while not self._cards:
            self._ready.clear()
            try:
                await asyncio.wait_for(self._ready.wait(), wait_seconds)
            except TimeoutError:
                return None
        return self._cards.popleft()
