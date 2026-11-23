"""`BridgeAdapter` — the PlatformAdapter whose transport is the bridges'
`platform` contract (`nora.domain.service_api_platform`) instead of a
platform SDK or listener.

Inbound never flows through `listen` here: events arrive via Redis Streams and
the elected stream reader; `listen` idles so existing supervision semantics
stay intact. Outbound `send`/`react` are platform operations carrying a
durable `ActionOwner` when one is bound via `bind_owner` — the binding is how
a turn work row's fenced identity reaches the action reservation without
changing the port's signature.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Set
from dataclasses import dataclass
from typing import Any, TypeGuard, get_args

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
from nora.domain.service_api import (
    Family,
    OperationConflictError,
    OperationOutcome,
    Outcome,
    parse_response,
)
from nora.domain.service_api_platform import SELF_ACCOUNT_ID

log = logging.getLogger("nora.platform.bridge.client")


class BridgeUnavailableError(RuntimeError):
    """Transport failure reaching the bridge — distinct from a platform
    rejection, and never reinterpreted as a delivered or refused send."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        #: The HTTP status the bridge answered, when it answered at all —
        #: the account read's 503-until-login is what `bridge_not_ready` reads.
        self.status = status


def bridge_not_ready(exc: BaseException) -> bool:
    """True only while a bridge is up but still starting: the account read
    answering 503 (the platform login has not landed yet) or the connection
    refused (its HTTP surface is not bound). Anything else — another
    status, a malformed body, a timeout — is a real startup failure."""
    if not isinstance(exc, BridgeUnavailableError):
        return False
    if exc.status == 503:
        return True
    cause = exc.__cause__
    return (isinstance(cause, aiohttp.ClientConnectorError)
            and isinstance(cause.os_error, ConnectionRefusedError))


@dataclass(frozen=True)
class BridgeEndpoint:
    base_url: str
    token: str


class BridgeAdapter:
    """One per platform account in bridge mode."""

    def __init__(self, platform: str, endpoint: BridgeEndpoint,
                 owner: ActionOwner | None = None,
                 dm_recipients: Set[str] = frozenset(),
                 dm_gate: Callable[[str], Awaitable[bool]] | None = None) -> None:
        self.platform = platform
        self._endpoint = endpoint
        self._owner = owner
        self._dm_recipients = dm_recipients
        self._dm_gate = dm_gate
        self._degraded_reason = ""
        self._session: aiohttp.ClientSession | None = None
        self._transport_owner: BridgeAdapter | None = None
        self._ordinal = 0

    def bind_owner(self, owner: ActionOwner | None) -> BridgeAdapter:
        """A facade with a durable action owner — how a fenced work identity
        reaches its sends. Owns its ordinal; borrows the root's transport."""
        bound = BridgeAdapter(self.platform, self._endpoint, owner,
                              dm_recipients=self._dm_recipients,
                              dm_gate=self._dm_gate)
        bound._transport_owner = self._transport_owner or self
        return bound

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
        if self._transport_owner is not None:
            return await self._transport_owner._client()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "Authorization": f"Bearer {self._endpoint.token}"})
        return self._session

    async def close(self) -> None:
        if self._transport_owner is not None:
            return
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _call(self, method: str, path: str, family: Family, *,
                    json: object = None, data: bytes | None = None,
                    timeout_s: float = 10) -> BridgeAnswer:
        return await bridge_call(await self._client(), method,
                                 f"{self._endpoint.base_url}{path}", family,
                                 json=json, data=data, timeout_s=timeout_s)

    async def _operation(self, kind: str, effect: dict[str, Any],
                         operation_id: str) -> BridgeAnswer:
        owner = (self._owner.to_dict() if self._owner is not None
                 else {"kind": "live_invocation", "id": "app"})
        answer = await self._call("POST", "/v1/platform/operations", "operation", json={
            "operation_id": operation_id, "owner": owner, "kind": kind,
            "effect": effect}, timeout_s=30)
        if not valid_operation(answer.body, message_id=self._bound_message_id()):
            raise BridgeUnavailableError(
                f"operation {operation_id}: not an envelope of this operation",
                status=answer.status)
        return answer

    def _bound_message_id(self) -> int | None:
        """The audit row a `live_invocation` owner names: what its record must carry."""
        if self._owner is not None and self._owner.kind == "live_invocation" \
                and self._owner.id.isdigit():
            return int(self._owner.id)
        return None

    # ---- PlatformAdapter -------------------------------------------------

    async def resolve_identity(self) -> PlatformIdentity:
        answer = await self._call("GET", ACCOUNT_PATH, "read")
        if answer.outcome != "ok":
            raise BridgeUnavailableError(
                f"{ACCOUNT_PATH} -> {answer.outcome}", status=answer.status)
        data = answer.payload if isinstance(answer.payload, dict) else {}
        primary = data.get("primary")
        if not isinstance(primary, str) or not primary.strip():
            raise BridgeUnavailableError(f"{ACCOUNT_PATH} returned no usable primary identity")
        return PlatformIdentity(
            platform=self.platform, primary=primary,
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
        if target.conversation_type == "dm":
            if self._dm_gate is not None:
                if not await self._dm_gate(target.conversation_id):
                    return SendResult(ok=False)
            elif self._dm_recipients \
                    and target.conversation_id not in self._dm_recipients:
                return SendResult(ok=False)
        # Attachment bytes ride through custody first — the action request
        # references keys, never inline bytes. Each key consumes a `send`
        # ordinal BEFORE the action's own id is minted below, so a key
        # embeds `send:N` while its action is `send:N+k` — the binding is
        # still deterministic per bound-owner retry, and a re-PUT with
        # different bytes is `rejected` (fail-closed), never a silent swap.
        keys = []
        for i, a in enumerate(attachments):
            key = f"out-{self._next_action_id('send')}-{i}"
            put = await self._call("PUT", f"/v1/platform/blobs/{key}", "operation",
                                   data=a.content, timeout_s=60)
            if not valid_operation(put.body, message_id=None):
                raise BridgeUnavailableError(f"blob {key}: not an operation envelope",
                                             status=put.status)
            if put.outcome == "rejected":
                # A refused attachment is a failed send, never a sent one.
                return SendResult(ok=False)
            if put.outcome != "complete":
                raise BridgeUnavailableError(f"blob {key} -> {put.outcome}", status=put.status)
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
        operation_id = self._next_action_id("send")
        try:
            answer = await self._operation("send", request, operation_id)
            outcome: str = answer.outcome
            envelope: Mapping[str, Any] = answer.body
        except BridgeUnavailableError:
            outcome, envelope = "unavailable", {}
        if outcome in ("pending", "unavailable"):
            # The answer was lost or is not settled — the reservation may already
            # have run upstream. Read the record back once, never resend.
            outcome, envelope = await self._read_back(operation_id)
        return _send_result(outcome, envelope)

    async def _read_back(self, operation_id: str) -> tuple[str, Mapping[str, Any]]:
        """One read of the operation's record; anything short of a valid record
        that is provably ours is `uncertain`: an absent record can belong to a
        POST still in flight, and a failed read (a 409 included) proves nothing."""
        try:
            answer = await self._call(
                "GET", f"/v1/platform/operations/{operation_id}", "read")
        except Exception:  # noqa: BLE001 — any failed read-back leaves the send uncertain
            return "uncertain", {}
        record = answer.payload if answer.outcome == "ok" else None
        if not valid_operation(record, message_id=self._bound_message_id()):
            return "uncertain", {}
        outcome = str(record["outcome"])
        return (outcome if outcome != "pending" else "uncertain"), record

    async def react(self, *, target: ConversationTarget, emoji: str,
                    to_author: str, to_timestamp: int,
                    to_message_id: str = "", remove: bool = False
                    ) -> ReactionResult:
        request = {"target": {"kind": "dm" if target.conversation_type == "dm" else "group",
                              "id": target.conversation_id, "thread": target.thread},
                   "emoji": emoji, "to_author": to_author,
                   "to_timestamp": to_timestamp, "to_message_id": to_message_id,
                   "remove": remove}
        answer = await self._operation("react", request, self._next_action_id("react"))
        if answer.outcome == "complete":
            return ReactionResult(ok=True)
        if answer.outcome == "unsupported":
            return ReactionResult(ok=False, supported=False)
        if answer.outcome in ("rejected", "denied", "uncertain", "incomplete"):
            return ReactionResult(ok=False)
        raise BridgeUnavailableError(f"react left {answer.outcome}", status=answer.status)

    async def health(self) -> AdapterHealth:
        try:
            answer = await self._call("GET", "/v1/operations/status", "read")
            data = answer.payload if answer.outcome == "ok" else None
            if not isinstance(data, dict):
                raise BridgeUnavailableError(
                    f"status -> {answer.outcome}", status=answer.status)
            return AdapterHealth(
                platform=self.platform, connected=bool(data.get("ok")),
                degraded=bool(data.get("degraded", False)),
                detail=str(data.get("detail") or ""))
        except BridgeUnavailableError as exc:
            return AdapterHealth(platform=self.platform, connected=False,
                                 detail=str(exc))


class BridgeMediaSource:
    """The `MediaSource` whose bytes live in bridge custody — `ref.platform_id`
    is the blob key the bridge stored at capture time."""

    def __init__(self, platform: str, endpoint: BridgeEndpoint) -> None:
        self.platform = platform
        self._endpoint = endpoint

    async def fetch(self, *, ref: Any) -> Any:
        from nora.domain.media import MediaBytes
        try:
            async with _session(self._endpoint) as session:
                answer = await bridge_call(
                    session, "GET",
                    f"{self._endpoint.base_url}/v1/platform/blobs/{ref.platform_id}",
                    "read", timeout_s=60, answers_bytes=True)
        except BridgeUnavailableError:
            return MediaBytes(available=False, reason="bridge_unreachable")
        if answer.data is not None:
            return MediaBytes(available=True, data=answer.data)
        if answer.outcome == "nothing_found":
            return MediaBytes(available=False, reason="absent")
        return MediaBytes(available=False, reason=f"bridge_{answer.outcome}")


class BridgeRoster:
    """`RosterSource` over the bridge's `groups` query — the platform's own
    membership list. Follows `SignalRoster`'s fail-closed rule exactly: a
    group the bridge does not list is `available=False`, never an empty roster.
    """

    def __init__(self, platform: str, endpoint: BridgeEndpoint) -> None:
        self.platform = platform
        self._endpoint = endpoint

    async def roster(self, *, conversation_id: str) -> Roster:
        answer = await bridge_query(self._endpoint, "groups", {}, timeout_s=30)
        groups = answer.payload if answer is not None else None
        if not isinstance(groups, list):
            return Roster(available=False)
        for g in groups:
            if not isinstance(g, dict):
                return Roster(available=False)
            if g.get("id") == conversation_id:
                members = g.get("members")
                if not isinstance(members, list) or any(
                    not isinstance(account, list) or not account
                    or any(not isinstance(alias, str) or not alias for alias in account)
                    for account in members
                ):
                    return Roster(available=False)
                return Roster(available=True,
                              members=tuple(frozenset(account) for account in members))
        return Roster(available=False)


# ---- the one reader of the platform contract --------------------------------

ACCOUNT_PATH = f"/v1/platform/accounts/{SELF_ACCOUNT_ID}"
QUERIES_PATH = "/v1/platform/queries"


@dataclass(frozen=True)
class BridgeAnswer:
    """A bridge answer read by its outcome; `data` is a blob's bytes."""

    outcome: Outcome
    status: int
    body: Mapping[str, Any]
    data: bytes | None = None

    @property
    def payload(self) -> Any:
        return self.body.get("payload")

    @property
    def observed_ms(self) -> int | None:
        stamp = (self.body.get("when") or {}).get("observed_at_ms")
        return stamp if isinstance(stamp, int) and stamp > 0 else None


def _session(endpoint: BridgeEndpoint) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(headers={"Authorization": f"Bearer {endpoint.token}"})


def _trace_headers() -> dict[str, str]:
    """The ambient span's W3C context; headers only, so no request hash sees it."""
    carrier: dict[str, str] = {}
    try:
        TraceContextTextMapPropagator().inject(carrier)
    except Exception:  # noqa: BLE001 — tracing is never load-bearing
        return {}
    return carrier


async def bridge_call(session: aiohttp.ClientSession, method: str, url: str,
                      family: Family, *, json: object = None, data: bytes | None = None,
                      timeout_s: float = 10, answers_bytes: bool = False,
                      headers: Mapping[str, str] | None = None) -> BridgeAnswer:
    """One call on the platform contract, read through `parse_response`.

    A redirect is never followed: it reaches the parser and reads `unavailable`.
    A 409 raises `OperationConflictError`; any other answer with no envelope,
    or a transport failure, raises `BridgeUnavailableError`.
    """
    try:
        async with session.request(
                method, url, json=json, data=data, allow_redirects=False,
                headers={**_trace_headers(), **(headers or {})},
                timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
            if answers_bytes and resp.status == 200 \
                    and resp.content_type != "application/json":
                return BridgeAnswer("ok", 200, {}, data=await resp.read())
            try:
                body = await resp.json(content_type=None)
            except ValueError:
                body = None
            status = resp.status
    except (TimeoutError, aiohttp.ClientError) as exc:
        raise BridgeUnavailableError(f"{url}: {type(exc).__name__}") from exc
    parsed = parse_response(family, status, body)
    if parsed.caller_fault == 409:
        raise OperationConflictError(url)
    if parsed.outcome is None:
        raise BridgeUnavailableError(f"{url} -> {status}", status=status)
    return BridgeAnswer(parsed.outcome, status, body if isinstance(body, Mapping) else {})


async def bridge_query(endpoint: BridgeEndpoint, kind: str, params: Mapping[str, Any], *,
                       session: aiohttp.ClientSession | None = None,
                       timeout_s: float = 30) -> BridgeAnswer | None:
    """A platform query's `ok` answer, or `None` for every other outcome or failure."""
    try:
        if session is not None:
            answer = await bridge_call(
                session, "POST", f"{endpoint.base_url}{QUERIES_PATH}", "read",
                json={"kind": kind, "params": dict(params)}, timeout_s=timeout_s)
        else:
            async with _session(endpoint) as own:
                answer = await bridge_call(
                    own, "POST", f"{endpoint.base_url}{QUERIES_PATH}", "read",
                    json={"kind": kind, "params": dict(params)}, timeout_s=timeout_s)
    except BridgeUnavailableError:
        return None
    return answer if answer.outcome == "ok" else None


_PARTS = frozenset({"outcome", "payload", "when", "from", "scope", "trace"})
_OPERATION_OUTCOMES = frozenset(get_args(OperationOutcome))


def _int_or_none(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _str_or_none(value: object) -> bool:
    return value is None or isinstance(value, str)


def valid_operation(body: object, *,
                    message_id: int | None) -> TypeGuard[Mapping[str, Any]]:
    """Whether `body` is a well-formed operation envelope, and of the audit row
    `message_id` when the operation bound one: six parts, an operation outcome,
    `settled` for every outcome but `pending`. Its status is `parse_response`'s."""
    if not isinstance(body, Mapping) or set(body) != _PARTS:
        return False
    when, origin, scope, payload = body["when"], body["from"], body["scope"], body["payload"]
    if body["outcome"] not in _OPERATION_OUTCOMES or not isinstance(body["trace"], str):
        return False
    if not (isinstance(when, Mapping) and isinstance(origin, Mapping)
            and isinstance(scope, Mapping) and isinstance(scope.get("level"), str)):
        return False
    if when.get("state") not in ("prepared", "started", "settled") \
            or (when["state"] == "settled") == (body["outcome"] == "pending") \
            or not (_int_or_none(when.get("started_at_ms"))
                    and _int_or_none(when.get("settled_at_ms"))):
        return False
    if not isinstance(origin.get("target"), str) or not _str_or_none(origin.get("platform_ref")):
        return False
    if payload is None:
        return True
    refs = payload.get("platform_refs", []) if isinstance(payload, Mapping) else None
    if not (isinstance(refs, list) and all(isinstance(r, str) for r in refs)
            and _int_or_none(payload.get("platform_ms"))
            and _int_or_none(payload.get("message_id"))
            and isinstance(payload.get("error"), Mapping | None)):
        return False
    stated = payload.get("message_id")
    return message_id is None or stated is None or stated == message_id


def _send_result(outcome: str, envelope: Mapping[str, Any]) -> SendResult:
    """An operation's outcome as the port's `SendResult` («What `send` returns»)."""
    if outcome == "complete":
        payload = envelope.get("payload") or {}
        ref = (envelope.get("from") or {}).get("platform_ref") or None
        refs = tuple(str(r) for r in (payload.get("platform_refs") or ()) if r)
        return SendResult(ok=True, timestamp=payload.get("platform_ms"),
                          platform_message_id=ref,
                          platform_message_ids=refs or ((str(ref),) if ref else ()))
    if outcome in ("rejected", "unsupported", "denied"):
        return SendResult(ok=False)
    # `uncertain`, `incomplete`, and a record that never settled: the send may
    # have landed — reachable as such, and nothing retries it blindly.
    return SendResult(ok=False, uncertain=True)
