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

A LIVE row left `staged` (the bridge said `too_soon`, or answered a 503
before claiming) is that tick's one action: its flush is retried before any
new mention is read, and no model call is made for it.

**It backs off on `needs_human` and 503.** A checkpointed session is not
retried every two minutes (design 19 rule 4: retrying a challenge is how a
captcha becomes a ban); the interval doubles, up to 30 minutes, and returns
to `poll_seconds` after the first good poll.

**Recovery, at boot and at the top of every tick**: a row left `claimed` for
longer than `CLAIM_TIMEOUT` becomes `failed`, and one left `flushing` (its
POST in flight when the process died) becomes `ambiguous`. Neither is ever
retried: whether its comment went out is unknown, and a double public comment
is worse than a missing one. A younger row is left alone, because during a
recreate the old container may still be working it.

A failed step is logged by its exception TYPE only (`_guarded`): a database
error renders its bound parameters, which here are comment ids, igids and
reply text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from nora.app.summons import SummonsOccasion
from nora.domain.summons import SummonsLedger, SummonsOutcome
from nora.infra.social.summons_client import InstagramSummonsClient
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.summons_poller")

#: The longest the poller waits between two polls while backing off.
MAX_BACKOFF_SECONDS = 1800.0

#: How old a `claimed` or `flushing` row must be before the sweep closes it.
#: Far above one turn's timeout, so a row another process (an old container
#: overlapping a new one during a recreate) is still working is left alone.
CLAIM_TIMEOUT = timedelta(minutes=15)

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
        """Close every `claimed` row (-> `failed`) and `flushing` row (->
        `ambiguous`) older than `CLAIM_TIMEOUT`. Run at boot and at the top of
        every tick, so a row a dead process left is closed within minutes
        whether or not this process restarts. Never retried either way."""
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

    async def tick(self) -> None:
        await self.recover()
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

    async def _guarded(self, step: Callable[[], Awaitable[object]], what: str) -> None:
        """Run one step; a failure is logged by its TYPE and a fixed message.

        Never `str(exc)` and never a traceback: a SQLAlchemy error renders its
        statement's BOUND PARAMETERS — a comment id, an igid, a reply's text —
        into both, and this log line is where they would leak."""
        try:
            await step()
        except Exception as exc:  # noqa: BLE001 — a loop that must outlive any tick
            log.error("summons %s failed (%s)", what, type(exc).__name__)
            self._metrics.inc("nora_errors_total", stage="summons_poller")

    async def run(self) -> None:
        log.info("summons poller started: poll every %.0fs", self._poll_seconds)
        await self._guarded(self.recover, "boot recovery")
        while True:
            await asyncio.sleep(self.next_delay)
            await self._guarded(self.tick, "poller tick")
