"""InstagramAdapter — a `PlatformAdapter` implementation.

Mirrors `nora.platform.whatsapp.adapter.WhatsAppAdapter`'s composition
exactly: a thin class wiring a listener, parser, sender and reactor against the
instagram-bridge's HTTP/SSE surface (`deploy/instagram-bridge`, the Go/instameow
daemon). This adapter holds no normalization logic of its own — that lives in
`parser.py`/`ids.py`, tested directly against the bridge's wire contract.

## The one thing it has that no sibling does: a DEGRADED state

`nora.platform.registry.AdapterRegistry.from_config` is fail-fast by design,
and that is right for Signal and Discord, where "cannot resolve my own
identity" means a misconfiguration somebody typed. On Instagram it means **a
captcha happened overnight**, and under the unmodified rule one checkpoint
stops Nora starting at all — Signal and Discord included.

So an enabled Instagram adapter that cannot authenticate is BUILT and
REGISTERED in a degraded state rather than refused. Degraded means, precisely:

- **no sends.** `send` and `react` refuse without an HTTP call. Not retryable:
  a checkpoint needs a human, and retrying a challenged account is how a
  captcha becomes a ban.
- **no events.** `listen` blocks forever without opening the SSE stream. It
  never RETURNS, because `nora.app.runtime.supervise_adapter` re-calls
  `listen()` immediately on a clean return — a degraded adapter that returned
  would become a hot loop.
- **visible.** `degrade()` logs one ERROR line at startup naming the reason,
  and `health()` reports `degraded=True` with that reason as `detail`, which
  `nora.obs.health` renders per platform. A silently degraded adapter is
  indistinguishable from a quiet room, which is the whole failure this exists
  to prevent.

`health()` also reports degradation that appears AFTER startup — a session
checkpointed at 4am — by reading the listener's heartbeat state. Startup and
runtime are two different ways to lose the session and only one of them is
visible to `from_config`.

**This is an Instagram-shaped exception with a stated reason, not a general
loosening.** Signal and Discord stay fail-fast. WhatsApp carries the same
latent exposure, but QR pairing is stable and it is not treated this way.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ...domain.models import ConversationTarget, NormalizedMessage
from ...domain.ports import (
    AdapterHealth,
    LinkPreview,
    Mention,
    OutboundAttachment,
    OutboundQuote,
    PlatformIdentity,
    ReactionResult,
    SendResult,
    TextStyle,
)
from ...domain.summons import SummonsSink
from .listener import InstagramListener
from .parser import parse_event
from .react import InstagramReactor
from .self_id import derive_identity, read_username
from .send import InstagramSender
from .summons import SUMMONS_KIND, parse_summons

log = logging.getLogger("nora.instagram.adapter")


class InstagramAdapter:
    platform = "instagram"

    def __init__(self, base_url: str, *, summons: SummonsSink | None = None) -> None:
        """`summons` is where a `kind: "summons"` event goes
        (`nora.app.summons_inbox.SummonsInbox`, which counts and drops it when
        `[social.instagram.summons]` is off). `None` drops every one: nothing
        in a room is ever built from a summons event either way."""
        self._base_url = base_url
        self._summons = summons
        self._listener = InstagramListener(base_url)
        self._sender = InstagramSender(base_url)
        self._reactor = InstagramReactor(base_url)
        self._identity: PlatformIdentity | None = None
        self._degraded_reason: str = ""

    # --- the degraded state ------------------------------------------

    @property
    def degraded(self) -> bool:
        """True once `degrade()` has been called, or once a heartbeat has
        reported a session that cannot act.

        TWO sources because there are two ways to lose the session and only
        one of them happens at startup. The startup half is latched — a
        process that began degraded stays degraded until it restarts, because
        nothing re-runs `resolve_identity` — and the runtime half tracks the
        live heartbeat, so a session that recovers stops being reported as
        degraded without a restart.
        """
        return bool(self._degraded_reason) or self._listener.session_degraded

    @property
    def degraded_reason(self) -> str:
        if self._degraded_reason:
            return self._degraded_reason
        if self._listener.session_degraded:
            return f"instagram session is {self._listener.session_state!r}"
        return ""

    def degrade(self, reason: str) -> None:
        """Register this adapter as present-but-unable, with a stated reason.

        Called by `AdapterRegistry.from_config` when `resolve_identity()`
        failed. The ERROR line is the startup half of the visibility
        requirement; `health()` is the other half, and neither replaces the
        other — a log line scrolls away and a health field says nothing about
        when it started.
        """
        self._degraded_reason = reason
        log.error(
            "instagram is ENABLED but DEGRADED: %s. Nora is registered on instagram and "
            "will neither send nor hear anything there until this is cleared and she is "
            "restarted. Signal and Discord are unaffected — this platform is allowed to "
            "start degraded precisely so one Instagram checkpoint cannot take them down "
            "(design/instagram-adoption/01 §Degradation).",
            reason,
        )

    # --- the port ---------------------------------------------------------

    async def resolve_identity(self) -> PlatformIdentity:
        """Memoized after the first successful call — same rationale as
        `WhatsAppAdapter.resolve_identity`: a stable answer to "who am I" that
        `/health` can poll without a bridge round trip every time. Memoized on
        SUCCESS only: the bridge answers a checkpointed session with a 200 and
        a cached `self` block, and an identity taken from that would be an
        adapter reporting itself fully healthy while every send 409s — so
        `self_id.derive_identity` refuses any session that is not
        `authenticated` and the failure is raised and retried, never cached.
        """
        if self._identity is None:
            primary, identifiers = await derive_identity(self._base_url)
            self._identity = PlatformIdentity(
                platform=self.platform, primary=primary, identifiers=identifiers,
            )
        return self._identity

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None:
        """Satisfies the port's `listen(*, on_message)` — parsing happens here,
        internally, via `nora.platform.instagram.parser.parse_event`, and a
        `None` result (a non-message kind, `from_me`, an empty turn) is dropped
        before `on_message` is ever called. Trap B — the parse-and-drop step
        lives in the adapter, not the ingest pipeline — same as
        `WhatsAppAdapter.listen`.

        A DEGRADED adapter blocks here forever instead. It does not open the
        stream (the bridge would serve it and the events would be real, but
        Nora cannot answer any of them, so ingesting them would record a room
        she is silently absent from), and it never returns, because
        `supervise_adapter` treats a return as "listen finished, call it
        again" and would spin.
        """
        if self.degraded:
            log.error(
                "instagram listener not started: adapter is degraded (%s). No Instagram "
                "message will be ingested by this process.",
                self.degraded_reason,
            )
            # Park forever. Cancellation still propagates — this is what
            # `BackgroundTasks.cancel_all` uses to shut the task down.
            await asyncio.Event().wait()
            return

        async def _on_event(raw_event: dict) -> None:
            # A summons card goes to the summons occasion and NEVER to room
            # ingest: it is not a room message, and the ordinary message
            # event the bridge sends beside it is the room's copy.
            if isinstance(raw_event, dict) and raw_event.get("kind") == SUMMONS_KIND:
                self._hand_off_summons(raw_event)
                return
            message = parse_event(raw_event)
            if message is not None:
                await on_message(message)

        await self._listener.listen(_on_event)

    def _hand_off_summons(self, raw_event: dict) -> None:
        if self._summons is None:
            return
        card = parse_summons(raw_event)
        if card is None:
            self._summons.malformed()
            return
        self._summons.offer(card)

    async def send(
        self,
        *,
        target: ConversationTarget,
        text: str,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: OutboundQuote | None = None,
    ) -> SendResult:
        if self.degraded:
            log.error(
                "refusing to send on instagram: adapter is degraded (%s)",
                self.degraded_reason,
            )
            # NOT retryable. A checkpoint is cleared by a human, and a caller
            # that backed off and retried would hammer a challenged account.
            return SendResult(ok=False)
        # `quote` IS honoured here, unlike WhatsApp's, because this bridge's
        # text and media messages both take `reply_to` — see `send.py`.
        return await self._sender.send(
            target, text, mentions=mentions, styles=styles, preview=preview,
            attachments=attachments, quote=quote,
        )

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_author: str,
        to_timestamp: int,
        # Accepted and IGNORED: the bridge addresses a message by its platform
        # message id, which is what `to_message_id` below carries — Signal's
        # (author, timestamp) scheme cannot reconstruct one, exactly the
        # situation `nora.domain.ports.PlatformAdapter.react` added the
        # additive parameter for.
        to_message_id: str = "",
        remove: bool = False,
    ) -> ReactionResult:
        if self.degraded:
            # `supported=False`, not `ok=False`: `indicate` counts the two
            # differently, and a degraded adapter has not tried and failed —
            # it cannot try at all, which is the same shape as a wiring gap.
            log.debug("skipping instagram reaction: adapter is degraded")
            return ReactionResult(ok=False, supported=False)
        return await self._reactor.react(
            target=target, emoji=emoji, to_message_id=to_message_id, remove=remove,
        )

    async def own_handle(self) -> str | None:
        """Her handle on Instagram, read live from the bridge, or `None`.

        Read per call and never memoized, unlike `resolve_identity`: the
        identity is ids that never change, and this is a name the operator
        can change from a phone. Display text for
        `<presence>` only — it never enters `PlatformIdentity`, so it can
        never become a mention id.
        """
        return await read_username(self._base_url)

    async def health(self) -> AdapterHealth:
        last = self._listener.last_event_at
        return AdapterHealth(
            platform=self.platform,
            connected=self._listener.connected,
            last_event_age_seconds=None if last is None else time.monotonic() - last,
            degraded=self.degraded,
            detail=self.degraded_reason,
        )
