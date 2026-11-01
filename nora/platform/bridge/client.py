"""`BridgeAdapter` — the PlatformAdapter whose transport is the shared bridge
wire API instead of a platform SDK or listener.

Inbound never flows through `listen` here: events arrive via Redis Streams and
the elected stream reader; `listen` idles so existing supervision semantics
stay intact. Outbound `send`/`react` are POST /v1/actions calls carrying a
durable `ActionOwner` when one is bound via `bind_owner` — the binding is how
a turn work row's fenced identity reaches the action reservation without
changing the port's signature.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Set
from dataclasses import dataclass
from typing import Any

import aiohttp
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

from nora.domain.actions import ActionOwner
from nora.domain.identity import PlatformIdentity
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import (
    AdapterHealth,
    LinkPreview,
    Mention,
    OutboundAttachment,
    OutboundQuote,
    ReactionResult,
    Roster,
    SendResult,
    TextStyle,
)

log = logging.getLogger("nora.platform.bridge.client")


class BridgeUnavailableError(RuntimeError):
    """Transport failure reaching the bridge — distinct from a platform
    rejection, and never reinterpreted as a delivered or refused send."""


@dataclass(frozen=True)
class BridgeEndpoint:
    base_url: str
    token: str


class BridgeAdapter:
    """One per platform account in bridge mode."""

    def __init__(self, platform: str, endpoint: BridgeEndpoint,
                 owner: ActionOwner | None = None,
                 dm_recipients: Set[str] = frozenset()) -> None:
        self.platform = platform
        self._endpoint = endpoint
        self._owner = owner
        self._dm_recipients = dm_recipients
        self._degraded_reason = ""
        self._session: aiohttp.ClientSession | None = None
        self._ordinal = 0

    def bind_owner(self, owner: ActionOwner | None) -> BridgeAdapter:
        """A facade with a durable action owner — how a fenced work identity
        reaches its sends. Shares nothing mutable with the parent adapter."""
        return BridgeAdapter(self.platform, self._endpoint, owner,
                             dm_recipients=self._dm_recipients)

    @property
    def action_owner(self) -> ActionOwner | None:
        """The bound durable owner, or None when unbound — `Deliverer` reads
        this to decide whether its record-first append must mint the
        `live_invocation` owner (a bound `work`/`otp_challenge` owner is
        never replaced)."""
        return self._owner

    def degrade(self, reason: str) -> None:
        """Present-but-unable, latched — the same word `InstagramAdapter`
        uses for the same state, so a registry caller can degrade either."""
        self._degraded_reason = reason
        log.error("%s bridge is ENABLED but DEGRADED: %s", self.platform, reason)

    @property
    def degraded(self) -> bool:
        return bool(self._degraded_reason)

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "Authorization": f"Bearer {self._endpoint.token}"})
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        # The ambient span's W3C context rides the POST headers — the
        # bridge's deliver span links to it instead of opening an orphan
        # root. Standard OTel propagation only: headers, never the body, so
        # the request hash and idempotence never see it.
        carrier: dict[str, str] = {}
        try:
            TraceContextTextMapPropagator().inject(carrier)
        except Exception:  # noqa: BLE001 — tracing is never load-bearing
            carrier = {}
        try:
            session = await self._client()
            async with session.post(
                    f"{self._endpoint.base_url}{path}", json=body,
                    headers=carrier or None,
                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise BridgeUnavailableError(f"{path} -> {resp.status}")
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise BridgeUnavailableError(f"{path}: {type(exc).__name__}") from exc

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            session = await self._client()
            async with session.get(
                    f"{self._endpoint.base_url}{path}",
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    raise BridgeUnavailableError(f"{path} -> {resp.status}")
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise BridgeUnavailableError(f"{path}: {type(exc).__name__}") from exc

    async def _put_bytes(self, path: str, data: bytes) -> None:
        try:
            session = await self._client()
            async with session.put(
                    f"{self._endpoint.base_url}{path}", data=data,
                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status not in (200, 201):
                    raise BridgeUnavailableError(f"{path} -> {resp.status}")
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise BridgeUnavailableError(f"{path}: {type(exc).__name__}") from exc

    async def _action(self, op: str, request: dict[str, Any],
                      action_id: str) -> dict[str, Any]:
        owner = (self._owner.to_dict() if self._owner is not None
                 else {"kind": "live_invocation", "id": "app"})
        return await self._post("/v1/actions", {
            "action_id": action_id, "owner": owner, "op": op,
            "request": request})

    # ---- PlatformAdapter -------------------------------------------------

    async def resolve_identity(self) -> PlatformIdentity:
        data = await self._get("/v1/self")
        return PlatformIdentity(
            platform=self.platform, primary=str(data["primary"]),
            aliases=frozenset(data.get("aliases") or ()))

    async def listen(self, *, on_message: Callable[[NormalizedMessage],
                                                   Awaitable[None]]) -> None:
        # Inbound is owned by the stream reader, not the adapter; idle so
        # adapter supervision has a task to keep alive.
        await asyncio.Event().wait()

    def _next_action_id(self, op: str) -> str:
        # Work-bound owners mint deterministic ids so a fenced retry replays
        # the SAME reservation; unbound live sends get unique ids.
        self._ordinal += 1
        if self._owner is not None:
            return f"{self._owner.id}:{op}:{self._ordinal - 1}"
        return f"live:{self.platform}:{id(self)}:{self._ordinal - 1}"

    async def send(self, *, target: ConversationTarget, text: str,
                   mentions: tuple[Mention, ...] = (),
                   styles: tuple[TextStyle, ...] = (),
                   preview: LinkPreview | None = None,
                   attachments: tuple[OutboundAttachment, ...] = (),
                   quote: OutboundQuote | None = None) -> SendResult:
        return await self.send_via_bridge(
            target, text, mentions=mentions, styles=styles, preview=preview,
            attachments=attachments, quote=quote)

    async def send_via_bridge(self, target: ConversationTarget, text: str,
                              *, mentions: tuple[Mention, ...] = (),
                              styles: tuple[TextStyle, ...] = (),
                              preview: LinkPreview | None = None,
                              attachments: tuple[OutboundAttachment, ...] = (),
                              quote: OutboundQuote | None = None) -> SendResult:
        """The send body, with `target`/`text` positional so an adapter
        wrapping this one (`BridgeDiscordAdapter`'s voice branch) delegates
        the same way `SignalAdapter` delegates to its sender — an
        adapter-internal hop, not a new caller of the port."""
        # The same DM-recipient guard `SignalSender`/`WhatsAppSender` run
        # app-side: a 1:1 to an address `identity.toml` does not know is
        # refused here, not delegated to the bridge (it has no policy file).
        if target.conversation_type == "dm" and self._dm_recipients \
                and target.conversation_id not in self._dm_recipients:
            return SendResult(ok=False)
        # Attachment bytes ride through custody first — the action request
        # references keys, never inline bytes.
        keys = []
        for i, a in enumerate(attachments):
            key = f"out-{self._next_action_id('send')}-{i}"
            await self._put_bytes(f"/v1/attachments/{key}", a.content)
            keys.append({"key": key, "file_name": a.filename,
                         "media_type": a.media_type})
        request: dict[str, Any] = {
            "target": {"kind": "dm" if target.conversation_type == "dm" else "group",
                       "id": target.conversation_id, "thread": target.thread},
            "text": text,
            "mentions": [{"start": m.start, "length": m.length,
                          "recipient": m.recipient, "handle": m.handle}
                         for m in mentions],
            "styles": [{"start": s.start, "length": s.length,
                        "style": s.style.value}
                       for s in styles],
            "preview": vars(preview) if preview else None,
            "attachments": keys,
            "quote": vars(quote) if quote else None,
        }
        action_id = self._next_action_id("send")
        try:
            data = await self._action("send", request, action_id)
        except BridgeUnavailableError:
            # The POST answer was lost — the reservation may already have
            # executed upstream. Reconcile through the persisted truth
            # (GET /v1/actions/{id}) instead of guessing: the deterministic
            # action id makes the read idempotent, and a lost `confirmed`
            # resolves the SAME audit row, never a second send.
            data = await self._reconcile(action_id)
        state = data["state"]
        if state == "confirmed":
            return SendResult(ok=True, timestamp=data.get("platform_ms"))
        if state == "rejected":
            return SendResult(ok=False)
        if state == "uncertain":
            # The send may have landed. The flag keeps the ambiguity REACHABLE
            # — `Deliverer` records it as such and nothing retries blindly.
            return SendResult(ok=False, uncertain=True)
        raise BridgeUnavailableError(f"send left {state}")

    async def _reconcile(self, action_id: str) -> dict[str, Any]:
        """One `GET /v1/actions/{id}` after a lost POST answer — the honest
        recovery, not a retry. An absent reservation can belong to a POST
        still in flight, so a 404 remains `uncertain`, like a failed read.
        A bound `live_invocation` owner's `message_id`
        must match the audit row it bound; a mismatch means the row that
        answered is not ours, which is `uncertain`, not a claim."""
        try:
            session = await self._client()
            async with session.get(
                    f"{self._endpoint.base_url}/v1/actions/{action_id}",
                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 404:
                    return {"state": "uncertain"}
                if resp.status != 200:
                    return {"state": "uncertain"}
                row = await resp.json()
        except (TimeoutError, aiohttp.ClientError):
            return {"state": "uncertain"}
        if (self._owner is not None and self._owner.kind == "live_invocation"
                and row.get("message_id") is not None
                and str(row["message_id"]) != self._owner.id):
            return {"state": "uncertain"}
        return row

    async def react(self, *, target: ConversationTarget, emoji: str,
                    to_author: str, to_timestamp: int,
                    to_message_id: str = "", remove: bool = False
                    ) -> ReactionResult:
        request = {"target": {"kind": "dm" if target.conversation_type == "dm" else "group",
                              "id": target.conversation_id, "thread": target.thread},
                   "emoji": emoji, "to_author": to_author,
                   "to_timestamp": to_timestamp, "to_message_id": to_message_id,
                   "remove": remove}
        data = await self._action("react", request, self._next_action_id("react"))
        state = data["state"]
        if state == "confirmed":
            return ReactionResult(ok=True)
        if state == "unsupported":
            return ReactionResult(ok=False, supported=False)
        if state in ("rejected", "uncertain"):
            return ReactionResult(ok=False)
        raise BridgeUnavailableError(f"react left {state}")

    async def health(self) -> AdapterHealth:
        try:
            data = await self._get("/v1/health")
            return AdapterHealth(
                platform=self.platform, connected=bool(data.get("ok")),
                degraded=bool(data.get("degraded", False)),
                detail=str(data.get("detail") or ""))
        except BridgeUnavailableError as exc:
            return AdapterHealth(platform=self.platform, connected=False,
                                 detail=str(exc))


class BridgeMediaSource:
    """The `MediaSource` whose bytes live in bridge custody — `ref.platform_id`
    is the attachment custody key the bridge stored under PUT
    /v1/attachments at capture time."""

    def __init__(self, platform: str, endpoint: BridgeEndpoint) -> None:
        self.platform = platform
        self._endpoint = endpoint

    async def fetch(self, *, ref: Any) -> Any:
        from nora.domain.media import MediaBytes
        try:
            async with aiohttp.ClientSession(headers={
                    "Authorization": f"Bearer {self._endpoint.token}"}) as s, s.get(
                    f"{self._endpoint.base_url}/v1/attachments/{ref.platform_id}",
                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    return MediaBytes(available=True, data=await resp.read())
                if resp.status == 404:
                    return MediaBytes(available=False, reason="absent")
                return MediaBytes(available=False,
                                  reason=f"bridge_{resp.status}")
        except (TimeoutError, aiohttp.ClientError):
            return MediaBytes(available=False, reason="bridge_unreachable")


class BridgeRoster:
    """`RosterSource` over the bridge's `GET /v1/groups` — the bridge proxies
    the platform's own membership list (signal-cli `listGroups`). Follows
    `SignalRoster`'s fail-closed rule exactly: a group the bridge does not
    list is `available=False`, never an empty roster.
    """

    def __init__(self, platform: str, endpoint: BridgeEndpoint) -> None:
        self.platform = platform
        self._endpoint = endpoint

    async def roster(self, *, conversation_id: str) -> Roster:
        try:
            async with aiohttp.ClientSession(headers={
                    "Authorization": f"Bearer {self._endpoint.token}"}) as s, s.get(
                    f"{self._endpoint.base_url}/v1/groups",
                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return Roster(available=False)
                groups = await resp.json()
        except (TimeoutError, aiohttp.ClientError):
            return Roster(available=False)
        for g in groups:
            if g.get("id") == conversation_id:
                return Roster(available=True,
                              members=tuple(g.get("members") or ()))
        return Roster(available=False)
