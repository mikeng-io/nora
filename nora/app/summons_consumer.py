"""`SummonsConsumer` — the public summons' one worker: it takes summons cards
from `SummonsInbox`, ONE AT A TIME, and hands each to `SummonsOccasion`.

**Events, not a poll.** The notification feed (`GET /mentions` over
`/api/v1/news/inbox/`) never carries a comment mention of her. A mention
reaches her as a DM card instead, and the bridge emits a `summons` event on
its SSE stream for it. The consumer keeps no clock and spends no requests on a
feed: a card arrives, one card is worked, the next waits.

**One at a time.** The same Instagram session carries her group chats, so what
this loop spends is requests on that session. A card that goes on to read its
post and comments, and perhaps write, is worked to its end before the next is
taken.

**A LIVE row left `staged`** (the bridge said `too_soon`, or answered a 503
before claiming) is retried before any new card is taken, by its flush alone
(no model call), and the loop pauses `RETRY_SECONDS` after a retry that is
still staged, so a `too_soon` is waited out rather than hammered.

**It backs off on `needs_human` and 503.** A checkpointed session is not
retried at once (retrying a challenge is how a captcha becomes a ban): after
such an answer the loop pauses, doubling from `2 * RETRY_SECONDS` up to 30
minutes, while cards wait in the inbox. The backoff clears on the first run that
reaches the bridge and is answered otherwise; a card filed without a bridge call
leaves it as it was.

**Recovery, at boot and at the top of every step**: a row left `claimed` for
longer than `CLAIM_TIMEOUT` becomes `failed`, and one left `flushing` (its
POST in flight when the process died) becomes `ambiguous`. Neither is ever
retried: whether its comment went out is unknown, and a double public comment
is worse than a missing one. A younger row is left alone, because during a
recreate the old container may still be working it. An idle consumer wakes
every `RETRY_SECONDS` to run it.

A failed step is logged by its exception TYPE only (`_guarded`): a database
error renders its bound parameters, which here are message ids, igids and
reply text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from nora.app.summons import SummonsOccasion
from nora.app.summons_inbox import SummonsInbox
from nora.domain.summons import SummonsLedger, SummonsOutcome
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.summons_consumer")

#: How long an idle consumer waits for a card before it runs the sweep again,
#: and how long it pauses before retrying a flush that is still `staged`. Above
#: the bridge's 30-second floor between two comments, so a `too_soon` retry
#: is not refused for the same reason again.
RETRY_SECONDS = 60.0

#: The longest the consumer pauses while backing off.
MAX_BACKOFF_SECONDS = 1800.0

#: How old a `claimed` or `flushing` row must be before the sweep closes it.
#: Far above one turn's timeout, so a row another process (an old container
#: overlapping a new one during a recreate) is still working is left alone.
CLAIM_TIMEOUT = timedelta(minutes=15)

#: Bridge codes that mean "the session needs a human" or "the bridge is
#: reconnecting". Every other failure is not a reason to wait longer.
_BACK_OFF = frozenset({"needs_human", "unavailable", "ledger_unavailable", "http_503"})


class SummonsConsumer:
    def __init__(
        self,
        *,
        inbox: SummonsInbox,
        occasion: SummonsOccasion,
        ledger: SummonsLedger,
        metrics: Metrics,
        clock: Callable[[], datetime],
    ) -> None:
        self._inbox = inbox
        self._occasion = occasion
        self._ledger = ledger
        self._metrics = metrics
        self._clock = clock
        self._backoff = 0.0
        self._pause = 0.0

    @property
    def pause_seconds(self) -> float:
        """Seconds the loop waits before its next step: 0, `RETRY_SECONDS`
        after a flush that stayed staged, or the backoff."""
        return self._pause

    def _note(self, outcome: SummonsOutcome) -> None:
        """Set the pause (and widen or clear the backoff) from one outcome."""
        if outcome.bridge_code in _BACK_OFF:
            self._backoff = min(
                MAX_BACKOFF_SECONDS, max(2 * RETRY_SECONDS, 2 * self._backoff),
            )
            self._pause = self._backoff
            log.warning(
                "summons consumer backing off %.0fs: the bridge answered %s",
                self._backoff, outcome.bridge_code,
            )
            return
        if outcome.reached_bridge:
            self._backoff = 0.0
        self._pause = (
            RETRY_SECONDS if outcome.state == "staged" and self._occasion.mode == "live"
            else 0.0
        )

    async def recover(self) -> int:
        """Close every `claimed` row (-> `failed`) and `flushing` row (->
        `ambiguous`) older than `CLAIM_TIMEOUT`. Run at boot and at the top of
        every step. Never retried either way."""
        swept = await self._ledger.sweep_stale_claims(
            now=self._clock(), older_than=CLAIM_TIMEOUT,
        )
        for state, n in swept.items():
            if n:
                log.warning(
                    "summons sweep: %d row(s) left open by a dead process are "
                    "now %s (never retried: whether a comment went out is "
                    "unknown)", n, state,
                )
                self._metrics.inc(
                    "nora_summons_total", n, outcome=state, mode=self._occasion.mode,
                )
        return sum(swept.values())

    async def step(self, *, wait_seconds: float | None = RETRY_SECONDS) -> None:
        """One action: the sweep, then EITHER one staged flush OR one card
        (waiting up to `wait_seconds` for it)."""
        self._pause = 0.0
        await self.recover()
        staged = await self._ledger.awaiting_flush()
        if staged is not None:
            self._note(await self._occasion.flush_staged(staged))
            return
        card = await self._inbox.get(wait_seconds=wait_seconds)
        if card is None:
            return
        self._note(await self._occasion.run(card))

    async def _guarded(self, step: Callable[[], Awaitable[object]], what: str) -> None:
        """Run one step; a failure is logged by its TYPE and a fixed message.

        Never `str(exc)` and never a traceback: a SQLAlchemy error renders its
        statement's BOUND PARAMETERS — a message id, an igid, a reply's text —
        into both, and this log line is where they would leak."""
        try:
            await step()
        except Exception as exc:  # noqa: BLE001 — a loop that must outlive any step
            log.error("summons %s failed (%s)", what, type(exc).__name__)
            self._metrics.inc("nora_errors_total", stage="summons_consumer")
            # A failing step must not become a hot loop.
            self._pause = RETRY_SECONDS

    async def run(self) -> None:
        log.info("summons consumer started (mode=%s)", self._occasion.mode)
        await self._guarded(self.recover, "boot recovery")
        while True:
            await self._guarded(self.step, "consumer step")
            if self._pause:
                await asyncio.sleep(self._pause)
