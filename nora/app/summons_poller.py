"""`SummonsPoller` — the public summons' clock (design/instagram-adoption/19),
shaped like `nora.app.compute_poller.ComputeJobPoller`: a background loop,
spawned by the composition root, that sleeps, ticks, and never lets a tick's
failure end the loop.

**At most ONE summons reaches the bridge per tick, oldest first.** The same
Instagram session carries her group chats (design 19 §"Account safety"), so
what this loop spends is requests on that session. A tick is one
`GET /mentions`, then at most one summons that goes on to read its post and
thread (and perhaps write). Mentions that end without another bridge call —
stale, a summoner nobody listed, a row another tick already holds — are
recorded in the same tick, so a backlog of old mentions on first enable is
filed in one pass rather than one per two minutes.

A LIVE row left `staged` (the bridge said `too_soon`, or the process died
mid-write) is that tick's one action: its flush is retried before any new
mention is read, and no model call is made for it.

**It backs off on `needs_human` and 503.** A checkpointed session is not
retried every two minutes (design 19 rule 4: retrying a challenge is how a
captcha becomes a ban); the interval doubles, up to 30 minutes, and returns
to `poll_seconds` after the first good poll.

**Boot recovery**: a row a dead process left `claimed` becomes `failed`,
never a retry. Whether its comment went out is unknown, and a double public
comment is worse than a missing one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from nora.app.summons import SummonsOccasion
from nora.domain.summons import SummonsLedger, SummonsOutcome
from nora.infra.social.summons_client import InstagramSummonsClient
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.summons_poller")

#: The longest the poller waits between two polls while backing off.
MAX_BACKOFF_SECONDS = 1800.0

#: Bridge codes that mean "the session needs a human" or "the bridge is
#: reconnecting". Every other failure is not a reason to wait longer.
_NEEDS_HUMAN = frozenset({"needs_human"})
_UNAVAILABLE = frozenset({"unavailable", "ledger_unavailable", "http_503"})


def _poll_status(code: str) -> str:
    if code == "ok":
        return "ok"
    if code in _NEEDS_HUMAN:
        return "needs_human"
    if code in _UNAVAILABLE:
        return "unavailable"
    return "error"


class SummonsPoller:
    def __init__(
        self,
        *,
        client: InstagramSummonsClient,
        occasion: SummonsOccasion,
        ledger: SummonsLedger,
        metrics: Metrics,
        poll_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._occasion = occasion
        self._ledger = ledger
        self._metrics = metrics
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._backoff = 0.0

    @property
    def next_delay(self) -> float:
        """Seconds until the next tick: `poll_seconds`, or the backoff."""
        return max(self._poll_seconds, self._backoff)

    def _note(self, code: str | None) -> None:
        """Widen or reset the backoff from one bridge answer."""
        if code is None or code == "ok":
            return
        if code in _NEEDS_HUMAN or code in _UNAVAILABLE:
            self._backoff = min(
                MAX_BACKOFF_SECONDS, max(2 * self._poll_seconds, 2 * self._backoff),
            )
            log.warning(
                "summons poller backing off to %.0fs: the bridge answered %s",
                self._backoff, code,
            )

    async def recover(self) -> int:
        swept = await self._ledger.sweep_stale_claims(now=self._clock())
        if swept:
            log.warning(
                "boot recovery: %d summons were left claimed by a dead process "
                "and are now failed (never retried: whether a comment went out "
                "is unknown)", swept,
            )
            self._metrics.inc(
                "nora_summons_total", swept, outcome="failed", mode=self._occasion.mode,
            )
        return swept

    async def tick(self) -> None:
        staged = await self._ledger.awaiting_flush()
        if staged is not None:
            outcome = await self._occasion.flush_staged(staged)
            self._after(outcome)
            return
        got = await self._client.mentions()
        self._metrics.inc("nora_summons_polls_total", status=_poll_status(got.code))
        if got.value is None:
            self._note(got.code)
            return
        self._backoff = 0.0
        unique = {m.comment_id: m for m in got.value}
        seen = await self._ledger.seen(tuple(unique))
        fresh = sorted(
            (m for cid, m in unique.items() if cid not in seen),
            # Oldest first; a mention with no time sorts first too, and is
            # recorded stale without a bridge call.
            key=lambda m: (m.at is not None, m.at or datetime.min.replace(tzinfo=UTC)),
        )
        for mention in fresh:
            outcome = await self._occasion.run(mention)
            if outcome.spent_tick:
                self._after(outcome)
                return

    def _after(self, outcome: SummonsOutcome) -> None:
        self._note(outcome.bridge_code)

    async def run(self) -> None:
        log.info("summons poller started: poll every %.0fs", self._poll_seconds)
        try:
            await self.recover()
        except Exception:
            log.exception("summons boot recovery failed")
            self._metrics.inc("nora_errors_total", stage="summons_poller")
        while True:
            await asyncio.sleep(self.next_delay)
            try:
                await self.tick()
            except Exception:
                log.exception("summons poller tick failed")
                self._metrics.inc("nora_errors_total", stage="summons_poller")
