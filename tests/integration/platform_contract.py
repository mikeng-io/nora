"""The `platform` contract's checks, shared by the Python bridges' route tests.

A bridge's test supplies what only it can make happen (a row in each stored
state, an unreadable query, an account before login); these functions drive
the wire and hold every answer to the document and the kernel's status table.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import sqlalchemy as sa
from aiohttp import web

from nora.domain.service_api import OperationOutcome, status_for
from nora.infra.sql.schema import MESSAGES

DOCUMENT = Path(__file__).resolve().parents[2] / "docs/service-api/openapi.json"
OPERATIONS = "/v1/platform/operations"
BLOBS = "/v1/platform/blobs"
#: The document's routes this contract owns; it lists other contracts' too.
CONTRACT_PREFIXES = ("/v1/platform/", "/v1/operations/")
PARTS = {"outcome", "payload", "when", "from", "scope", "trace"}
#: The collections the old wire served; none may be registered.
OLD_COLLECTIONS = ("actions", "self", "reads", "attachments", "health",
                   "groups", "guilds", "channels", "presence")


def documented_routes() -> set[tuple[str, str]]:
    paths = json.loads(DOCUMENT.read_text())["paths"]
    return {(method.upper(), path) for path, item in paths.items() for method in item
            if path.startswith(CONTRACT_PREFIXES)}


def assert_serves_the_contract(app: web.Application) -> None:
    """The contract's routes both ways, the root probes, and none of the old wire."""
    served = {(route.method, route.resource.canonical)
              for route in app.router.routes() if route.resource is not None}
    contract = {(m, p) for m, p in served
                if p.startswith(CONTRACT_PREFIXES) and m != "HEAD"}
    assert contract == documented_routes()
    assert {("GET", "/health"), ("GET", "/ready")} <= served
    old = tuple(f"/v1/{name}" for name in OLD_COLLECTIONS)
    assert not [p for _, p in served if p.startswith(old)]


async def envelope(resp: Any) -> dict[str, Any]:
    """An answer's body, held to the six parts and its outcome's status."""
    body = await resp.json()
    assert set(body) == PARTS, body
    assert resp.status == status_for(body["outcome"]), (resp.status, body)
    return body


async def pending_row(engine: Any, *, platform: str, conversation_id: str) -> int:
    """A committed pending outbound audit row: what a `live_invocation` owner names."""
    async with engine.begin() as conn:
        return int((await conn.execute(sa.insert(MESSAGES).values(
            platform=platform, conversation_id=conversation_id,
            conversation_type="group", sender_id="generated-self", ts=1,
            body="pending", is_nora=1, delivery_status="pending",
            raw_json=json.dumps({"tag_id": "generated-tag"}))
            .returning(MESSAGES.c.id))).scalar_one())


@contextlib.contextmanager
def swapped(owner: Any, attr: str, value: Any = None) -> Iterator[None]:
    """`owner.attr` is `value` for the block: by default a collaborator the bridge lacks."""
    kept = getattr(owner, attr)
    setattr(owner, attr, value)
    try:
        yield
    finally:
        setattr(owner, attr, kept)


@dataclass
class Bridge:
    """What one bridge's test supplies to `drive_outcomes`."""

    http: Any
    auth: dict[str, str]
    store: Any
    engine: Any
    platform: str
    account_key: str
    conversation_id: str
    no_ledger: Callable[[], Any]          # a context manager: the bridge has no ledger
    query_ok: tuple[str, dict[str, Any]]
    query_unavailable: tuple[str, dict[str, Any]]
    logged_out: Callable[[], Any]        # a context manager: before the platform login
    blob_limit: Callable[[int], None]            # set the blob size bound


async def _seed(bridge: Bridge, operation_id: str, owner: dict[str, Any],
                effect: dict[str, Any], state: str, kind: str = "") -> None:
    async with bridge.store._engine.begin() as conn:
        await bridge.store.reserve_action(
            conn, action_id=operation_id, platform=bridge.platform,
            account_key=bridge.account_key, owner_kind=owner["kind"],
            owner_id=owner["id"], owner_generation=None, op="send", request=effect,
            message_id=int(owner["id"]))
        if state != "started":
            await bridge.store.action_state(
                conn, operation_id, state,
                error={"kind": kind, "message": "generated"} if kind else None)


async def drive_outcomes(bridge: Bridge) -> None:
    """Every outcome «The wire» names, each answer held to the contract."""
    http, auth = bridge.http, bridge.auth
    rid = await pending_row(bridge.engine, platform=bridge.platform,
                            conversation_id=bridge.conversation_id)
    owner = {"kind": "live_invocation", "id": str(rid)}
    effect = {"target": {"kind": "group", "id": bridge.conversation_id}, "text": "hello"}

    async def operation(operation_id: str, *, kind: str = "send",
                        body_owner: dict[str, Any] = owner, text: str = "hello") -> Any:
        return await http.post(OPERATIONS, headers=auth, json={
            "operation_id": operation_id, "owner": body_owner, "kind": kind,
            "effect": {**effect, "text": text}})

    for state, kind, outcome in [
        ("confirmed", "", "complete"), ("started", "", "pending"),
        ("rejected", "platform", "rejected"), ("rejected", "unsupported", "unsupported"),
        ("uncertain", "", "uncertain"),
    ]:
        await _seed(bridge, f"op-{outcome}", owner, effect, state, kind)
        body = await envelope(await operation(f"op-{outcome}"))
        assert body["outcome"] == outcome
        assert body["when"]["state"] == ("started" if outcome == "pending" else "settled")
        assert body["scope"] == {"level": "room", "id": bridge.conversation_id}
    denied = await envelope(await operation(
        "op-denied", body_owner={"kind": "work", "id": "w-999999", "generation": 1}))
    assert denied["outcome"] == "denied" and denied["when"]["started_at_ms"] is None
    assert await bridge.store.action("op-denied") is None
    with bridge.no_ledger():
        assert (await envelope(await operation("op-dark")))["outcome"] == "unavailable"
    assert (await operation("op-bogus", kind="bogus")).status == 422
    conflict = await operation("op-complete", text="another effect")
    assert conflict.status == 409 and "outcome" not in await conflict.json()

    record = await envelope(await http.get(f"{OPERATIONS}/op-complete", headers=auth))
    assert record["outcome"] == "ok" and record["payload"]["outcome"] == "complete"
    assert record["payload"]["when"]["settled_at_ms"] is not None
    started = await envelope(await http.get(f"{OPERATIONS}/op-pending", headers=auth))
    assert started["payload"]["outcome"] == "pending"
    assert started["payload"]["when"]["state"] == "started"
    absent = await envelope(await http.get(f"{OPERATIONS}/op-never", headers=auth))
    assert absent["outcome"] == "nothing_found" and absent["payload"] is None

    await _drive_reads(bridge)
    await _drive_blobs(bridge)


async def _drive_reads(bridge: Bridge) -> None:
    http, auth = bridge.http, bridge.auth

    async def query(kind: str, params: dict[str, Any]) -> Any:
        return await http.post("/v1/platform/queries", headers=auth,
                               json={"kind": kind, "params": params})

    ok = await envelope(await query(*bridge.query_ok))
    assert ok["outcome"] == "ok" and ok["when"]["known_by"] == "bridge_clock"
    assert ok["from"]["source"] == bridge.platform
    assert (await envelope(await query(*bridge.query_unavailable)))["outcome"] == "unavailable"
    assert (await query("bogus", {})).status == 422
    account = await envelope(await http.get("/v1/platform/accounts/self", headers=auth))
    assert account["outcome"] == "ok" and account["payload"]["primary"]
    with bridge.logged_out():
        early = await envelope(await http.get("/v1/platform/accounts/self", headers=auth))
    assert early["outcome"] == "unavailable"
    other = await envelope(await http.get("/v1/platform/accounts/another", headers=auth))
    assert other["outcome"] == "nothing_found"
    status = await envelope(await http.get("/v1/operations/status", headers=auth))
    assert status["outcome"] == "ok" and "ok" in status["payload"]


async def _drive_blobs(bridge: Bridge) -> None:
    http, auth = bridge.http, bridge.auth
    bridge.blob_limit(8)

    async def put(key: str, data: bytes) -> dict[str, Any]:
        return await envelope(await http.put(f"{BLOBS}/{key}", data=data, headers=auth))

    assert (await put("k1", b"one"))["payload"] == {"key": "k1"}
    assert (await put("k1", b"one"))["outcome"] == "complete"
    other = await put("k1", b"two")
    assert other["outcome"] == "rejected" and other["payload"]["error"]["kind"] == "conflict"
    large = await put("k2", b"x" * 9)
    assert large["outcome"] == "rejected" and large["payload"]["error"]["kind"] == "too_large"
    assert (await http.get(f"{BLOBS}/bad..key", headers=auth)).status == 400
    got = await http.get(f"{BLOBS}/k1", headers=auth)
    assert got.status == 200 and await got.read() == b"one"
    absent = await envelope(await http.get(f"{BLOBS}/k2", headers=auth))
    assert absent["outcome"] == "nothing_found"


def assert_settled_operation(status: int, body: Any) -> None:
    """A settled operation envelope as a controller's outbox counts it delivered:
    the six parts, each well formed, an operation outcome under its own status."""
    assert isinstance(body, dict) and set(body) == PARTS, body
    assert body["outcome"] in get_args(OperationOutcome) and body["outcome"] != "pending"
    assert status == status_for(body["outcome"]), (status, body)
    when, source, scope = body["when"], body["from"], body["scope"]
    assert when["state"] == "settled"
    assert all(isinstance(when[k], int) for k in ("started_at_ms", "settled_at_ms")), when
    assert isinstance(source["target"], str) and source["platform_ref"] is None, source
    assert scope["level"] == "installation" and isinstance(body["trace"], str), body
    assert body["trace"] == "" or len(body["trace"]) == 32, body
