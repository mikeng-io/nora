"""signal-bridge — the durable Signal boundary (design/platform-bridges).

Wraps an UNCHANGED signal-cli child: preflights the capture journal BEFORE the
CLI may start receiving (on every restart, not once), captures the daemon's
SSE, normalizes each envelope once via nora.platform.signal.parser, durably
journals it, dedupe-enqueues to Redis Streams, and serves the shared /v1
actions + custody API. Every outbound effect is a `bridge_action` row:
reserve (prepared→started CAS) → effect → terminal CAS, so a restart after
`started` retains uncertainty instead of blind-repeating, and a replayed
request returns the persisted outcome.

Env: read once by `BridgeConfig.from_env` (tests construct the dataclass
directly — no module-level singletons).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import aiohttp
import redis.asyncio as aioredis
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine

from nora.domain.actions import ActionOwner
from nora.domain.models import ConversationTarget
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.obs.metrics import Metrics
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.envelope import (
    Envelope,
    canonical_source_key,
    event_id,
)
from nora.platform.bridge.journal import Journal, JournalError
from nora.platform.bridge.queue import StreamProducer
from nora.platform.bridge.rehydrate import message_from_dict, message_to_dict
from nora.platform.bridge.service import (
    ActionReply,
    DurableActionService,
    JournalCaptureSink,
    JournalPublisher,
    _reply_from_row,
    action_http_status,
    action_response,
    admitted,
    receipt_intent_ids,
    receipt_via,
)
from nora.platform.signal.parser import parse_event
from nora.platform.signal.react import SignalReactor
from nora.platform.signal.send import SignalSender

log = logging.getLogger("signal-bridge")
PLATFORM = "signal"


@dataclass(frozen=True)
class BridgeConfig:
    """Everything the bridge reads — one place, importable for tests."""

    account_key: str
    token: str
    signal_http: str
    signal_account: str
    port: int
    redis_url: str
    database_url: str
    journal_path: Path
    attach_dir: Path
    projection_dir: Path
    correlation_key: bytes
    key_version: int

    @property
    def stream(self) -> str:
        return f"nq:in:{PLATFORM}:{self.account_key}"

    @classmethod
    def from_env(cls) -> BridgeConfig:
        account_key = os.environ.get("NORA_BRIDGE_ACCOUNT_KEY", "")
        if (not account_key or account_key == "default"
                or not account_key.replace("-", "").replace("_", "").isalnum()):
            raise SystemExit(
                "NORA_BRIDGE_ACCOUNT_KEY must be a set, opaque [a-z0-9_-] value")
        token = (os.environ.get("NORA_BRIDGE_API_TOKEN")
                 or os.environ.get("NORA_BRIDGE_TOKEN") or "")
        if not token:
            raise SystemExit("NORA_BRIDGE_API_TOKEN is required")
        return cls(
            account_key=account_key,
            token=token,
            signal_http=os.environ.get(
                "SIGNAL_CLI_HTTP", "http://127.0.0.1:18080"),
            signal_account=os.environ["SIGNAL_ACCOUNT"],
            port=int(os.environ.get("NORA_BRIDGE_PORT", "18090")),
            redis_url=os.environ["NORA_REDIS_URL"],
            database_url=os.environ["DATABASE_URL"],
            journal_path=Path(os.environ["NORA_BRIDGE_JOURNAL"]),
            attach_dir=Path(
                os.environ.get("NORA_BRIDGE_ATTACH_DIR", "/data/attachments")),
            projection_dir=Path(os.environ.get(
                "NORA_BRIDGE_PROJECTION_DIR", "/bridge-projection")),
            correlation_key=bytes.fromhex(
                os.environ["NORA_BRIDGE_CORRELATION_KEY"]),
            key_version=int(os.environ.get(
                "NORA_BRIDGE_CORRELATION_KEY_VERSION", "1")),
        )


def _valid_account_key(cfg: BridgeConfig) -> None:
    key = cfg.account_key
    if not key or key == "default" or not key.replace(
            "-", "").replace("_", "").isalnum():
        raise ValueError("account_key must be a set, opaque [a-z0-9_-] value")


class _SignalExecutor:
    """Signal's half of the durable action contract — the `DurableActionService`
    executor the /v1/actions route and the receipt lane share. The ledger
    decides WHEN an effect may run; this only performs it, with Signal's own
    honesty: `SignalReactor`/`SignalSender` never raise, so every non-`ok`
    answer that is not a definitive `retryable` refusal is `uncertain` — the
    dropped-answer case may still have landed."""

    def __init__(self, sender: SignalSender, reactor: SignalReactor) -> None:
        self._sender = sender
        self._reactor = reactor

    async def __call__(self, action: dict[str, Any]) -> ActionReply:
        aid = str(action.get("action_id") or "")
        op = str(action.get("op") or "")
        r = action.get("request") or {}
        target = ConversationTarget(
            PLATFORM, str((r.get("target") or {}).get("id") or ""),
            str((r.get("target") or {}).get("kind") or ""))
        try:
            if op == "send":
                res = await self._sender.send(
                    target, str(r.get("text") or ""), request_id=aid)
                # `retryable` marks a definitive upstream refusal (429);
                # every other ok=False includes the dropped-answer/timeout
                # case the sender itself says may still have landed.
                state = ("confirmed" if res.ok
                         else "rejected" if getattr(res, "retryable", False)
                         else "uncertain")
                return ActionReply(state, platform_ms=res.timestamp)
            if op == "react":
                res = await self._reactor.react(
                    target, str(r.get("emoji") or ""),
                    to_author=str(r.get("to_author") or ""),
                    to_timestamp=r.get("to_timestamp"),
                    remove=bool(r.get("remove", False)), request_id=aid)
                return ActionReply("confirmed" if res.ok else "uncertain")
            return ActionReply("rejected", kind="unsupported", message=op)
        except Exception as exc:  # noqa: BLE001 — effect boundary; logged by type, outcome stays honest
            log.error("effect error: %s", type(exc).__name__)
            return ActionReply(
                "uncertain", kind="transport", message=type(exc).__name__)


def _attach_key_ok(key: str) -> bool:
    return bool(key) and key.isalnum() and len(key) <= 128


class Bridge:
    """Capture + durable /v1 actions. `store` is the action ledger; an effect
    may only run after the CAS reserves it, and a terminal CAS only moves a
    row THIS request started."""

    def __init__(
        self,
        cfg: BridgeConfig,
        *,
        sender: Any | None = None,
        reactor: Any | None = None,
        rpc_client: Callable[..., Any] | None = None,
        redis_client: Any | None = None,
        store: SqlBridgeStore | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        _valid_account_key(cfg)
        self.cfg = cfg
        self._metrics = metrics
        self.redis = redis_client or aioredis.from_url(cfg.redis_url)
        self.producer = StreamProducer(self.redis, cfg.stream)
        self.store = store or SqlBridgeStore(
            create_async_engine(cfg.database_url))
        self.journal = Journal(cfg.journal_path)
        self.sender = sender or SignalSender(cfg.signal_http)
        self.reactor = reactor or SignalReactor(cfg.signal_http)
        self._rpc = rpc_client or self._rpc_http
        self._received: asyncio.Event = asyncio.Event()
        # canonical_id → env whose receipt reserve failed while enqueued;
        # retried by the publisher loop without a restart.
        self._pending_receipts: dict[str, Envelope] = {}
        # The shared durable halves (nora.platform.bridge.service): the
        # ONE journal→stream writer, the capture sink that freezes receipt
        # intent + appends + wakes it, and the ledger-fenced action service
        # the /v1/actions route and the receipt lane both run through.
        self._executor = _SignalExecutor(self.sender, self.reactor)
        self._publisher = JournalPublisher(
            journal=self.journal, producer=self.producer,
            on_enqueued=self._receipt_step,
            retry_pending=self._retry_pending_receipts,
            has_pending=lambda: bool(self._pending_receipts),
            metrics=metrics)
        self._sink = JournalCaptureSink(
            journal=self.journal, publisher=self._publisher,
            intent_for=self._capture_intent, metrics=metrics)
        self._actions = DurableActionService(
            store=self.store, platform=PLATFORM,
            account_key=self.cfg.account_key, execute=self._executor,
            receipt_intents=self._receipt_intent_ids,
            ops=frozenset({"send", "react"}), metrics=metrics)
        # Kept as the object tests poke (`bridge._dirty.set()` is "_handle
        # appended"): it IS the publisher's wake flag.
        self._dirty = self._publisher._dirty

    @property
    def _replay_offset(self) -> int:
        return self._publisher.offset

    async def _rpc_http(self, method: str, params: dict[str, Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params,
                   "id": "1"}
        async with aiohttp.ClientSession() as s, s.post(
                f"{self.cfg.signal_http}/api/v1/rpc", json=payload) as r:
            body = await r.json()
            if r.status != 200 or "error" in body:
                raise RuntimeError(f"{method}: {body.get('error') or r.status}")
            return body.get("result")

    def load_projection(self) -> dict[str, Any]:
        try:
            return json.loads(
                (self.cfg.projection_dir / f"{PLATFORM}.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {"revision": 0, "enabled": False}

    # ---- capture ----------------------------------------------------------

    async def capture(self) -> None:
        self.journal.probe()  # preflight before every receive window
        url = f"{self.cfg.signal_http}/api/v1/events"
        timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
        # The wrapper owns the LOCAL reconnect to signal-cli's HTTP — the
        # daemon binds it a beat after launch and drops it on a restart, and
        # neither may take the journal or the /v1 site down with it. Journal
        # failures still propagate: capture without a durable spool must stop
        # upstream (`supervise`'s JournalError branch), not reconnect.
        backoff = 1.0
        while True:
            try:
                async with (aiohttp.ClientSession(timeout=timeout) as session,
                            session.get(url) as resp):
                    resp.raise_for_status()
                    backoff = 1.0
                    async for raw in resp.content:
                        if not raw.startswith(b"data:"):
                            continue
                        event = json.loads(raw[5:])
                        await self._handle(event)
            except aiohttp.ClientError:
                log.warning(
                    "signal-cli event stream down; reconnecting in %.0fs",
                    backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _handle(self, event: dict) -> None:
        msg = parse_event(event, own_account=self.cfg.signal_account)
        if msg is None:
            return
        proj = self.load_projection()
        if not _admitted(proj, msg.conversation_id, msg.sender_id,
                         msg.conversation_type):
            return  # denied room: no body, no attachment bytes, no capture
        kind = ("message" if msg.edit_of is None and msg.touch is None
                else ("edit" if msg.edit_of else msg.touch.kind))
        # The ONE short producer root for this capture — its context is
        # frozen onto the envelope BEFORE the journal append inside
        # `_sink.capture` (design 04 §producer).
        with bt.producer_span(PLATFORM, kind=kind) as carrier:
            if msg.media:
                with bt.operation_span(
                        "custody", platform=PLATFORM,
                        links=bt.carrier_links(carrier)):
                    msg, attachments = await self._custody(msg)
            else:
                attachments = []
            now = int(time.time() * 1000)
            upstream = msg.timestamp or None
            sk = canonical_source_key(
                PLATFORM, self.cfg.account_key, msg.conversation_id, kind,
                msg.message_id or f"{msg.sender_id}:{msg.timestamp}")
            env = Envelope(
                platform=PLATFORM, account_key=self.cfg.account_key,
                kind=kind,
                source_key=sk,
                event_id=event_id(sk, key=self.cfg.correlation_key,
                                  key_version=self.cfg.key_version),
                conversation_id=msg.conversation_id,
                conversation_type=msg.conversation_type,
                sender_id=msg.sender_id,
                sender_name=msg.sender_name, sent_ms=upstream,
                received_ms=now,
                queued_ms=now,
                time_quality="upstream" if upstream else "bridge_local",
                message=message_to_dict(msg),
                attachments=tuple(attachments),
                trace=dict(carrier),
                admission_rev=int(proj.get("revision") or 0),
            )
            # Journal FIRST — the durable spool precedes any queue call. The
            # sink freezes the receipt INTENT into the envelope at capture
            # (never re-derived on replay), appends the record, and wakes
            # the publisher loop — the ONE writer to Redis, so a recovered
            # head can never be outrun by a new event.
            await self._sink.capture(env)
        self._received.set()

    def _capture_intent(self, env: Envelope) -> dict[str, str] | None:
        """The frozen receipt intent for one captured envelope — the CURRENT
        projection's eligibility answer, computed once at capture and riding
        the journal record verbatim."""
        proj = self.load_projection()
        msg = env.message
        if not receipt_via(
                proj, conversation_id=env.conversation_id,
                conversation_type=env.conversation_type,
                sender_id=env.sender_id,
                mentions=set(msg.get("mentions") or ()),
                quote_author_id=(msg.get("quote") or {}).get("author_id"),
                body=str(msg.get("body") or ""),
                self_ids={self.cfg.signal_account}):
            return None
        return {"emoji": str(proj["receipt_emoji"])}

    async def _custody(self, msg: Any) -> tuple[Any, list[dict[str, Any]]]:
        """Download each attachment into custody AND rebind the serialized
        `MediaRef.platform_id` to the custody key — the app's
        `BridgeMediaSource` fetches `GET /v1/attachments/{platform_id}`, so
        the rehydrated reference must name the key bytes answer to, never
        the upstream handle that no byte answers to (same binding the
        WhatsApp bridge makes: `ref.platform_id = key`).

        Index-stable: `refs[i]` is the custody-bound twin of `msg.media[i]`,
        so a failed one keeps its upstream handle (custody absent — the
        app's fetch is an honest `unavailable`, not a key that never
        existed) and a custody-free ref passes through untouched."""
        out: list[dict[str, Any]] = []
        refs = []
        for ref in msg.media:
            if not ref.platform_id:
                refs.append(ref)
                continue
            try:
                data = await self._rpc("getAttachment", {"id": ref.platform_id})
                raw = base64.b64decode(data["data"]) if isinstance(data, dict) else b""
                key = hashlib.sha256(raw).hexdigest()[:32]
                (self.cfg.attach_dir / key).write_bytes(raw)
            except Exception as exc:  # noqa: BLE001 — custody boundary; logged by type
                log.error("attachment custody failed: %s",
                          type(exc).__name__)
                refs.append(ref)
                continue
            refs.append(replace(ref, platform_id=key))
            out.append({"key": key, "media_type": ref.media_type,
                        "file_name": ref.filename, "size": len(raw)})
        return replace(msg, media=tuple(refs)), out

    # ---- queued receipt (durable, one per canonical event) -----------------

    def _receipt_action_id(self, canonical_id: str) -> str:
        # The action is bound to the canonical event id — rotation replays
        # resolve to this SAME id, so no second action ever exists.
        return f"receipt:{canonical_id}"

    async def _receipt(self, canonical_id: str, msg: Any, emoji: str,
                       *, env: Envelope) -> str:
        """Queued-receipt with durable intent: reserve → effect → terminal —
        the shared `run_owned` lane bound to the canonical event id, so a
        rotation replay resolves to the SAME action and never double-fires.
        Returns the typed phase: "unavailable" (ledger unreachable — retry;
        nothing was started), a persisted terminal, "started"/"conflict".

        The outbound effect is a bounded root LINKED to the envelope's
        frozen producer context — the same lineage an attempt carries."""
        target = msg.target()
        return await self._actions.run_owned(
            action_id=self._receipt_action_id(canonical_id),
            owner=ActionOwner(kind="event_receipt", id=canonical_id),
            op="react",
            links=bt.carrier_links(env.trace),
            request={
                "target": {"platform": target.platform,
                           "id": target.conversation_id,
                           "kind": target.conversation_type},
                "emoji": emoji, "to_author": msg.sender_id,
                "to_timestamp": msg.timestamp})

    async def _receipt_step(self, env: Envelope, canonical_id: str) -> None:
        """Post-enqueue receipt work for one published envelope. The intent
        came from the envelope (frozen at capture); eligibility is re-checked
        against CURRENT projection — revocation suppresses the queued
        reaction. A reserve failure parks it in `_pending_receipts` so the
        next pass retries without a restart."""
        if not env.receipt:
            return
        proj = self.load_projection()
        msg = message_from_dict(env.message)
        if not _receipt_eligible(proj, msg, self.cfg.signal_account):
            return  # revoked since capture — suppressed, no effect
        state = await self._receipt(
            canonical_id, msg, env.receipt["emoji"], env=env)
        if state == "unavailable":
            self._pending_receipts[canonical_id] = env

    async def _retry_pending_receipts(self) -> None:
        for canonical_id, env in list(self._pending_receipts.items()):
            proj = self.load_projection()
            msg = message_from_dict(env.message)
            if not _receipt_eligible(proj, msg, self.cfg.signal_account):
                del self._pending_receipts[canonical_id]
                continue  # revoked while pending — dropped, never sent
            state = await self._receipt(
                canonical_id, msg, env.receipt["emoji"], env=env)
            if state != "unavailable":
                del self._pending_receipts[canonical_id]

    # ---- sole publisher: journal head → Redis, ordered, never skipped ------

    async def replay_journal(self) -> None:
        """The shared `JournalPublisher` loop — the ONE writer from journal
        to Redis; `_handle` only appends through the sink and wakes it."""
        await self._publisher.run()

    def stop_replay(self) -> None:
        self._publisher.stop()

    # ---- /v1 API ------------------------------------------------------------

    def routes(self) -> web.Application:
        app = web.Application(middlewares=[self._auth])
        app.router.add_get("/metrics", self.get_metrics)
        app.router.add_post("/v1/actions", self.post_action)
        app.router.add_get("/v1/actions/{aid}", self.get_action)
        app.router.add_get("/v1/self", self.get_self)
        app.router.add_get("/v1/health", self.get_health)
        app.router.add_post("/v1/reads", self.post_read)
        app.router.add_put("/v1/attachments/{key}", self.put_attachment)
        app.router.add_get("/v1/attachments/{key}", self.get_attachment)
        return app

    async def get_metrics(self, req: web.Request) -> web.Response:
        if self._metrics is None:
            return web.Response(status=503, text="metrics unavailable")
        return web.Response(text=self._metrics.render(),
                            headers={"Content-Type": "text/plain; version=0.0.4"})

    @web.middleware
    async def _auth(self, req: web.Request, handler: Any) -> web.Response:
        if req.headers.get("Authorization") != f"Bearer {self.cfg.token}":
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(req)

    def _receipt_intent_ids(self) -> frozenset[str]:
        """The durable evidence an `event_receipt` owner must name — the
        shared journal read (`receipt_intent_ids`): canonical event alias
        AND rotation-stable source digest of every frozen intent."""
        return receipt_intent_ids(self.journal)

    async def post_action(self, req: web.Request) -> web.Response:
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        reply = await self._actions.run(body, links=bt.carrier_links({
            "traceparent": req.headers.get("traceparent") or "",
            "tracestate": req.headers.get("tracestate") or ""}))
        return web.json_response(
            action_response(str(body.get("action_id") or ""), reply),
            status=action_http_status(reply))

    async def get_action(self, req: web.Request) -> web.Response:
        """Persisted, scoped result — 404 only for a definitely-absent id;
        `started` (in progress or uncertain) is a 200 with the row's truth.
        The shared service does the tenant scoping; the shared renderer the
        honest outcome shape (`message_id` rides for client reconcile)."""
        aid = req.match_info["aid"]
        try:
            row = await self._actions.get(aid)
        except Exception:  # noqa: BLE001 — ledger unreachable is a 503, not a 404
            return web.json_response({"error": "store unavailable"}, status=503)
        if row is None:
            return web.json_response({"error": "unknown action"}, status=404)
        return web.json_response(action_response(aid, _reply_from_row(row)))

    async def get_self(self, req: web.Request) -> web.Response:
        try:
            await self._rpc("getUserStatus", {})
            return web.json_response({"platform": PLATFORM,
                                      "primary": self.cfg.signal_account,
                                      "aliases": [self.cfg.signal_account]})
        except Exception as exc:  # noqa: BLE001 — status facade
            return web.json_response({"error": type(exc).__name__}, status=502)

    async def get_health(self, req: web.Request) -> web.Response:
        try:
            await self._rpc("getUserStatus", {})
            return web.json_response({"ok": True, "detail": None})
        except Exception as exc:  # noqa: BLE001 — health is the probe
            return web.json_response({"ok": False, "detail": type(exc).__name__})

    async def post_read(self, req: web.Request) -> web.Response:
        body = await req.json()
        if body["op"] == "roster":
            groups = await self._rpc("listGroups", {})
            gid = (body.get("params") or {}).get("conversation_id")
            for g in groups or []:
                if g.get("id") == gid or g.get("groupId") == gid:
                    members = g.get("members") or []
                    return web.json_response({"available": True,
                                              "members": members})
            return web.json_response({"available": False, "members": []})
        return web.json_response({"error": "unknown read"}, status=404)

    async def put_attachment(self, req: web.Request) -> web.Response:
        key = req.match_info["key"]
        if not _attach_key_ok(key):
            return web.json_response({"error": "bad key"}, status=400)
        body = await req.read()
        path = self.cfg.attach_dir / key
        if path.is_file() and path.read_bytes() != body:
            # Immutable custody: a key names ONE payload; a different body
            # under the same key is a conflict, never a silent overwrite.
            return web.json_response({"error": "key conflict"}, status=409)
        self.cfg.attach_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return web.Response(status=201)

    async def get_attachment(self, req: web.Request) -> web.Response:
        key = req.match_info["key"]
        if not _attach_key_ok(key):
            return web.json_response({"error": "bad key"}, status=400)
        path = self.cfg.attach_dir / key
        if not path.is_file():
            return web.json_response({"error": "unknown"}, status=404)
        return web.Response(body=path.read_bytes())


def _admitted(proj: dict[str, Any], conversation_id: str, sender_id: str,
              conversation_type: str) -> bool:
    return admitted(proj, conversation_id=conversation_id,
                    sender_id=sender_id, conversation_type=conversation_type)


def _receipt_eligible(proj: dict[str, Any], msg: Any, own_account: str) -> bool:
    """The shared eligibility rule (`service.receipt_via`), re-checked
    before every effect — revocation suppresses the queued reaction."""
    return bool(receipt_via(
        proj, conversation_id=msg.conversation_id,
        conversation_type=msg.conversation_type, sender_id=msg.sender_id,
        mentions=set(msg.mentions),
        quote_author_id=msg.quote.author_id if msg.quote else None,
        body=msg.body or "", self_ids={own_account}))


def _bootstrap_secrets() -> list[str]:
    """The values the log redactor must catch — the bridge's own token and
    the correlation key, the only secrets it holds."""
    try:
        cfg = BridgeConfig.from_env()
    except Exception:  # noqa: BLE001 — logging must still come up on a bad env
        return []
    return [s for s in (cfg.token, cfg.correlation_key.hex()) if s]


def _setup_tracing() -> None:
    """Env-driven equivalent of `__main__`'s `[obs.tracing]` wiring — the
    signal-bridge has no config.toml, so the same knobs ride the process
    env (`NORA_TRACING_ENABLED`/`_EXPORTER`/`_ENDPOINT`/`_HEADERS_ENV`).
    Tracing is never load-bearing: a bad endpoint or an unreachable
    collector warns and the bridge still captures."""
    if os.environ.get("NORA_TRACING_ENABLED", "").lower() not in (
            "1", "true", "yes", "on"):
        return
    try:
        from nora.agent.telemetry import setup_agent_tracing
        from nora.infra.version import VersionManifest
        from nora.obs.tracing import build_sampler

        # service.version rides OTEL_RESOURCE_ATTRIBUTES — `Resource.create`
        # merges it over the explicit service.name. Only what the baked
        # manifest proves: a dev checkout exports no version claim.
        ans = VersionManifest().current()
        ver = ans.entry.version if ans.status == "ok" and ans.entry else ""
        existing = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
        if ver and "service.version" not in existing:
            os.environ["OTEL_RESOURCE_ATTRIBUTES"] = (
                f"{existing},service.version={ver}"
                if existing else f"service.version={ver}")
        setup_agent_tracing(
            sampler=build_sampler(), service_name="signal-bridge",
            exporter=os.environ.get("NORA_TRACING_EXPORTER", "otlp_http"),
            endpoint=os.environ.get("NORA_TRACING_ENDPOINT", ""),
            headers_env=os.environ.get("NORA_TRACING_HEADERS_ENV", ""),
            credentials=dict(os.environ))
    except Exception as exc:  # noqa: BLE001 — tracing is never load-bearing
        log.warning("tracing setup failed; continuing without tracing: %s",
                    type(exc).__name__)


async def supervise() -> None:
    cfg = BridgeConfig.from_env()
    journal = Journal(cfg.journal_path)
    # Preflight BEFORE the child may receive a byte — every restart, not once.
    journal.probe()
    cfg.attach_dir.mkdir(parents=True, exist_ok=True)
    bridge = Bridge(cfg, metrics=Metrics())
    replay_task = asyncio.ensure_future(bridge.replay_journal())
    child = await asyncio.create_subprocess_exec(
        # signal-cli's raw JSON-RPC binds LOOPBACK only: co-networked
        # containers reach the protected /v1 API (token-authed), never the
        # unauthenticated daemon socket. on-start/Java25 untouched.
        "/usr/local/bin/signal-cli", "--account", cfg.signal_account,
        "daemon", "--http", "127.0.0.1:18080", "--receive-mode", "on-start")
    try:
        app = bridge.routes()
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg.port).start()  # noqa: S104 — private nora network, token-authed
        await bridge.capture()
    except (JournalError, OSError) as exc:
        log.error("capture journal broken — stopping upstream: %s",
                  type(exc).__name__)
    finally:
        bridge.stop_replay()
        replay_task.cancel()
        child.terminate()
        try:
            await asyncio.wait_for(child.wait(), timeout=10)
        except TimeoutError:
            child.kill()
    sys.exit(1)


if __name__ == "__main__":
    from nora.obs.logging import setup_logging

    setup_logging(level=os.environ.get("NORA_LOG_LEVEL", "INFO"),
                  format="json", secrets=_bootstrap_secrets())
    _setup_tracing()
    asyncio.run(supervise())
