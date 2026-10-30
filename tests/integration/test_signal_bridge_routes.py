"""signal-bridge /v1 routes + capture/publisher against real infra.

Signal upstream is `nora.testing.signal_daemon.FakeSignalDaemon` — a real
aiohttp server speaking the actual /api/v1/rpc + /api/v1/events wire — so the
wrapper runs its REAL SignalSender/SignalReactor/capture code, not injected
port doubles. Redis + Postgres are disposable containers; outages are real
TCP refusal/sever via a local proxy, injected through the bridge's
constructor collaborators — never monkeypatched production instances.
"""

import asyncio
import contextlib
import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import redis.asyncio as aioredis
import sqlalchemy as sa
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import create_async_engine

from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.schema import BRIDGE_ACTION, BRIDGE_WORK, MESSAGES, OTP_CHALLENGE
from nora.testing.signal_daemon import FakeSignalDaemon

pytestmark = pytest.mark.integration

_WRAPPER = Path(__file__).resolve().parents[2] / "deploy/signal-bridge/wrapper.py"
_spec = importlib.util.spec_from_file_location("signal_bridge_wrapper", _WRAPPER)
wrapper = importlib.util.module_from_spec(_spec)
sys.modules["signal_bridge_wrapper"] = wrapper
_spec.loader.exec_module(wrapper)

AUTH = {"Authorization": "Bearer test-token"}
ACCT = "sig-acct-1"
OWN = "+00000000001"
SENDER = "+00000000002"
ROOM = "g-room-1"


# ---- real transport collaborators ------------------------------------------


class TcpProxy:
    """A plain TCP forwarder with a kill switch — a REAL network outage:
    connection refused/severed, never a stubbed method."""

    def __init__(self, host: str, port: int):
        self._upstream = (host, port)
        self.up = True
        self._writers: set[asyncio.StreamWriter] = set()
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._pipe, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _pipe(self, reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter) -> None:
        if not self.up:
            self.refused = getattr(self, "refused", 0) + 1
            writer.close()
            return
        self.accepted = getattr(self, "accepted", 0) + 1
        try:
            ur, uw = await asyncio.open_connection(*self._upstream)
        except OSError:
            writer.close()
            return
        self._writers |= {writer, uw}

        async def fwd(a: asyncio.StreamReader, b: asyncio.StreamWriter) -> None:
            try:
                while data := await a.read(65536):
                    b.write(data)
                    await b.drain()
            except (OSError, ConnectionError):
                pass
            finally:
                b.close()

        try:
            await asyncio.gather(fwd(reader, uw), fwd(ur, writer))
        finally:
            self._writers -= {writer, uw}

    def set_up(self, up: bool) -> None:
        self.up = up
        if not up:
            for w in self._writers:
                w.close()

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()


class GatedEngine:
    """A constructor-level engine seam: `begin()` raises like a dead Postgres
    while `down` holds; everything else delegates to the real AsyncEngine."""

    def __init__(self, real_url: str):
        self._inner = create_async_engine(real_url)
        self.down = False

    def begin(self, *a: Any, **k: Any):
        if self.down:
            raise ConnectionError("pg unreachable")
        return self._inner.begin(*a, **k)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def _redis_url(redis_client) -> str:
    kw = redis_client.connection_pool.connection_kwargs
    return f"redis://{kw['host']}:{kw['port']}/{kw.get('db', 0)}"


def _cfg(tmp_path, *, signal_http: str, redis_url: str, database_url: str):
    return wrapper.BridgeConfig(
        account_key=ACCT, token="test-token",  # noqa: S106 — fixture secret
        signal_http=signal_http, signal_account=OWN,
        port=0, redis_url=redis_url, database_url=database_url,
        journal_path=tmp_path / "journal.jsonl",
        attach_dir=tmp_path / "att", projection_dir=tmp_path / "proj",
        correlation_key=b"test-correlation-key", key_version=1)


def _projection(tmp_path, **over) -> None:
    d = tmp_path / "proj"
    d.mkdir(parents=True, exist_ok=True)
    proj = {"revision": 1, "enabled": True, "rooms": [ROOM],
            "dm_allowed_senders": [],
            "room_receipt_senders": {ROOM: [SENDER]},
            "receipt_emoji": "👍"}
    proj.update(over)
    (d / "signal.json").write_text(json.dumps(proj))


@pytest.fixture
async def daemon():
    d = FakeSignalDaemon()
    client = TestClient(TestServer(d.app()))
    await client.start_server()
    d.url = str(client.make_url("")).rstrip("/")
    yield d
    await client.close()


async def _spawn(cfg, *, store=None, redis_client=None):
    """One Bridge + publisher + /v1 client — the real runtime shape."""
    bridge = wrapper.Bridge(cfg, store=store, redis_client=redis_client)
    bridge.journal.probe()  # what supervise() does before the child may run
    publisher = asyncio.ensure_future(bridge.replay_journal())
    client = TestClient(TestServer(bridge.routes()))
    await client.start_server()
    return bridge, publisher, client


async def _teardown(bridge, publisher, client) -> None:
    bridge.stop_replay()
    publisher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await publisher
    await client.close()
    await bridge.store._engine.dispose()
    await bridge.redis.aclose()


@pytest.fixture
async def bridge_app(pg_url, redis_client, tmp_path, daemon):
    _projection(tmp_path)
    cfg = _cfg(tmp_path, signal_http=daemon.url,
               redis_url=_redis_url(redis_client), database_url=pg_url)
    bridge, publisher, client = await _spawn(cfg)
    yield bridge, client, daemon
    await _teardown(bridge, publisher, client)


def _send(aid: str, owner: dict, text: str = "hello") -> dict:
    return {"action_id": aid, "op": "send", "owner": owner,
            "request": {"target": {"id": ROOM, "kind": "group"},
                        "text": text}}


# ---- durable owner rows (the authority the closed gate checks) ---------------

async def _pending_row(bridge, *, conversation_id: str = ROOM,
                       tag_id: str = "tag-x", platform: str = "signal",
                       delivery_status: str = "pending") -> int:
    """A committed pending outbound audit row — live_invocation authority."""
    async with bridge.store._engine.begin() as conn:
        rid = (await conn.execute(sa.insert(MESSAGES).values(
            platform=platform, conversation_id=conversation_id,
            conversation_type="dm", sender_id=OWN, ts=1,
            body="pending body", is_nora=1,
            delivery_status=delivery_status,
            raw_json=json.dumps({"tag_id": tag_id}))
            .returning(MESSAGES.c.id))).scalar_one()
    return int(rid)


async def _live_owner(bridge, **kw) -> tuple[dict, int]:
    rid = await _pending_row(bridge, **kw)
    return {"kind": "live_invocation", "id": str(rid)}, rid


async def _otp_owner(bridge, *, platform: str = "signal",
                     expires_in: timedelta = timedelta(minutes=5),
                     with_pending: bool = True) -> tuple[dict, str]:
    """A real `otp_challenge` row (+ its pending outbound audit row) — the
    durable owner `OtpService` binds before `deliver.build`."""
    cid = str(uuid.uuid4())
    now = datetime.now(UTC)
    expires = now + expires_in
    # ck_otp_challenge_expiry requires expires_at > issued_at — an expired
    # challenge is issued in the past, never given a backwards window.
    issued = min(now, expires - timedelta(minutes=1))
    async with bridge.store._engine.begin() as conn:
        await conn.execute(sa.insert(OTP_CHALLENGE).values(
            challenge_id=cid, person_id="person-1", purpose="sign_in",
            platform=platform, code_hash="0" * 64,
            issued_at=issued, expires_at=expires))
    if with_pending:
        await _pending_row(bridge, tag_id=f"otp-{cid}")
    return {"kind": "otp_challenge", "id": cid}, cid


async def _work_owner(bridge, *, conversation_id: str = ROOM,
                      generation: int = 1, state: str = "running",
                      platform: str = "signal", account_key: str = ACCT,
                      tag_id: str | None = None,
                      lease_seconds: int | None = 300) -> dict:
    """A REAL claimed `bridge_work` row — `running` with a live lease, the
    same shape `claim_next` leaves. `lease_seconds=None` inserts no lease."""
    async with bridge.store._engine.begin() as conn:
        wid = (await conn.execute(sa.insert(BRIDGE_WORK).values(
            kind="turn", ordinal=0, platform=platform,
            account_key=account_key, conversation_id=conversation_id,
            state=state, generation=generation,
            lease_owner=None if lease_seconds is None else "nora-test",
            leased_until=None if lease_seconds is None else (
                sa.func.now() + sa.text(
                    f"interval '{int(lease_seconds)} seconds'")),
            payload={} if tag_id is None else {"tag_id": tag_id})
            .returning(BRIDGE_WORK.c.id))).scalar_one()
    return {"kind": "work", "id": f"w-{wid}", "generation": generation}


def sig_event(source: str, body: str, ts: int, *,
              mention_own: bool = False, group: str = ROOM) -> dict:
    """A signal-cli receive event, shaped as `parse_event` consumes it."""
    data = {"message": body, "timestamp": ts,
            "groupInfo": {"groupId": group}}
    if mention_own:
        data["mentions"] = [
            {"number": OWN, "name": "N", "start": 0, "length": 1}]
    return {"envelope": {"source": source, "sourceNumber": source,
                         "sourceName": "Peer", "timestamp": ts,
                         "dataMessage": data}}


async def _captured(bridge, daemon, events: list, deadline_s: float = 5.0):
    """Drive events through the REAL SSE capture path into the journal."""
    base = len([r for r in bridge.journal.recover() if "envelope" in r])
    cap = asyncio.ensure_future(bridge.capture())
    try:
        for ev in events:
            await daemon.emit(ev)
        end = asyncio.get_running_loop().time() + deadline_s
        while (len([r for r in bridge.journal.recover()  # noqa: ASYNC110
                    if "envelope" in r]) < base + len(events)
               and asyncio.get_running_loop().time() < end):
            # The journal is a file — nothing raises an event to wait on.
            await asyncio.sleep(0.05)
    finally:
        cap.cancel()


async def _stream_envelopes(redis_client):
    kw = redis_client.connection_pool.connection_kwargs
    client = aioredis.Redis(host=kw["host"], port=kw["port"])
    try:
        rows = await client.xrange(f"nq:in:signal:{ACCT}")
        return [wrapper.Envelope.from_fields(json.loads(f[b"e"].decode()))
                for _sid, f in rows]
    finally:
        await client.aclose()


async def _stream_envelopes_for(bridge):
    """Read the stream through the BRIDGE's own redis client."""
    rows = await bridge.redis.xrange(bridge.cfg.stream)
    return [wrapper.Envelope.from_fields(json.loads(f[b"e"].decode()))
            for _sid, f in rows]


async def _wait_for(fn, deadline_s: float = 8.0) -> None:
    end = asyncio.get_running_loop().time() + deadline_s
    while not fn():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition never reached")
        await asyncio.sleep(0.05)


def _drained(bridge) -> bool:
    """Publisher consumed every journaled record — probe lines included."""
    return bridge._replay_offset >= len(bridge.journal.recover())


# ---- durable /v1/actions -----------------------------------------------------


async def test_duplicate_post_one_upstream_effect(bridge_app):
    """Two concurrent identical POSTs: the CAS admits one; ONE real send
    lands on the fake daemon; the replay returns the persisted outcome."""
    bridge, client, daemon = bridge_app
    owner, _ = await _live_owner(bridge)
    a, b = await asyncio.gather(
        client.post("/v1/actions", json=_send("a-1", owner), headers=AUTH),
        client.post("/v1/actions", json=_send("a-1", owner), headers=AUTH))
    bodies = [await a.json(), await b.json()]
    assert {r["state"] for r in bodies} <= {"confirmed", "started", "rejected"}
    assert len(daemon.sent) == 1  # exactly one upstream effect, on the wire
    got = await client.get("/v1/actions/a-1", headers=AUTH)
    assert got.status == 200
    assert (await got.json())["state"] == "confirmed"


async def test_action_scoped_get(bridge_app):
    """Absent is 404; another platform's action is 404 (scoped); a settled
    action returns its persisted truth."""
    bridge, client, _ = bridge_app
    got = await client.get("/v1/actions/never-existed", headers=AUTH)
    assert got.status == 404
    async with bridge.store._engine.begin() as conn:
        await bridge.store.reserve_action(
            conn, action_id="a-2", platform="discord", account_key="other",
            owner_kind="work", owner_id="w-2", owner_generation=None,
            op="send", request={"target": {"id": "x", "kind": "group"},
                                "text": "t"})
    got = await client.get("/v1/actions/a-2", headers=AUTH)
    assert got.status == 404  # scoped: another platform/account's row
    owner, _ = await _live_owner(bridge)
    r = await client.post("/v1/actions", json=_send("a-3", owner), headers=AUTH)
    assert (await r.json())["state"] == "confirmed"
    got = await client.get("/v1/actions/a-3", headers=AUTH)
    assert (await got.json())["state"] == "confirmed"


async def test_lost_upstream_answer_is_uncertain_and_never_resent(bridge_app):
    """The daemon APPLIES the send then the answer is lost: the honest state
    is `uncertain`; a replay never re-sends."""
    bridge, client, daemon = bridge_app
    owner, _ = await _live_owner(bridge)
    daemon.drop_next_rpc = 1
    r = await client.post("/v1/actions", json=_send("a-lost", owner),
                          headers=AUTH)
    assert (await r.json())["state"] == "uncertain"
    assert len(daemon.sent) == 1  # effect applied upstream, answer lost
    r2 = await client.post("/v1/actions", json=_send("a-lost", owner),
                           headers=AUTH)
    assert (await r2.json())["state"] == "uncertain"
    assert len(daemon.sent) == 1  # never blindly retried
    got = await client.get("/v1/actions/a-lost", headers=AUTH)
    assert (await got.json())["state"] == "uncertain"


async def test_get_reports_in_progress_row(bridge_app):
    """A `started` row is 200-with-truth, not absent and not terminal."""
    bridge, client, _ = bridge_app
    async with bridge.store._engine.begin() as conn:
        await bridge.store.reserve_action(
            conn, action_id="a-mid", platform="signal", account_key=ACCT,
            owner_kind="work", owner_id="w-9", owner_generation=None,
            op="send", request=_send("a-mid", {"kind": "work", "id": "w-9",
                                             "generation": 1})["request"])
    got = await client.get("/v1/actions/a-mid", headers=AUTH)
    assert got.status == 200
    assert (await got.json())["state"] == "started"


async def test_unknown_work_owner_fails_closed(bridge_app):
    """A `w-{int}` owner must name a real `bridge_work` row — refused BEFORE
    the CAS and before any upstream effect."""
    bridge, client, daemon = bridge_app
    body = _send("a-ghost", owner={"kind": "work", "id": "w-999999",
                                 "generation": 1})
    r = await client.post("/v1/actions", json=body, headers=AUTH)
    assert r.status == 403
    assert len(daemon.sent) == 0
    assert await bridge.store.action("a-ghost") is None


async def test_db_unavailable_refuses_before_effect(bridge_app, tmp_path,
                                                    daemon):
    """A dead ledger means 503 BEFORE the upstream call — an unrecorded
    effect is the one thing this API must never produce."""
    cfg = _cfg(tmp_path, signal_http=daemon.url,
               redis_url="redis://127.0.0.1:1/0",
               database_url="postgresql+asyncpg://u:p@127.0.0.1:1/none")
    bridge = wrapper.Bridge(cfg)
    client = TestClient(TestServer(bridge.routes()))
    await client.start_server()
    try:
        r = await client.post(
            "/v1/actions",
            json=_send("a-dead", {"kind": "live_invocation", "id": "1"}),
            headers=AUTH)
        assert r.status == 503
        assert len(daemon.sent) == 0
    finally:
        await client.close()


async def test_attachment_custody(bridge_app):
    """Bounded key validation on both verbs; unknown vs bad key distinct;
    a conflicting same-key write is a 409 — immutable custody."""
    _, client, _ = bridge_app
    bad = await client.get("/v1/attachments/bad.key", headers=AUTH)
    assert bad.status == 400
    missing = await client.get("/v1/attachments/nope1", headers=AUTH)
    assert missing.status == 404
    put = await client.put("/v1/attachments/k1", data=b"one", headers=AUTH)
    assert put.status == 201
    conflict = await client.put("/v1/attachments/k1", data=b"two",
                                headers=AUTH)
    assert conflict.status == 409
    same = await client.put("/v1/attachments/k1", data=b"one", headers=AUTH)
    assert same.status == 201  # identical payload is a no-op, not a conflict
    got = await client.get("/v1/attachments/k1", headers=AUTH)
    assert got.status == 200 and (await got.read()) == b"one"


async def test_auth_required(bridge_app):
    _, client, _ = bridge_app
    r = await client.post(
        "/v1/actions",
        json=_send("a-4", {"kind": "live_invocation", "id": "1"}))
    assert r.status == 401


async def test_media_custody_roundtrips_through_the_app_source(bridge_app):
    """The app's half of the custody contract end-to-end: bytes land under
    the bridge's custody key, and `BridgeMediaSource` — the media source
    composition hands every bridge-mode adapter — fetches the SAME bytes
    back by that key. An absent key is `available=False`, never bytes."""
    from nora.domain.media import MediaRef
    from nora.platform.bridge.client import BridgeEndpoint, BridgeMediaSource

    _, client, _ = bridge_app
    put = await client.put("/v1/attachments/inhash1",
                           data=b"payload-bytes", headers=AUTH)
    assert put.status == 201
    source = BridgeMediaSource("signal", BridgeEndpoint(
        base_url=str(client.make_url("")).rstrip("/"),
        token="test-token"))  # noqa: S106 — fixture secret
    got = await source.fetch(ref=MediaRef(platform_id="inhash1"))
    assert got.available is True and got.data == b"payload-bytes"
    absent = await source.fetch(ref=MediaRef(platform_id="nope1"))
    assert absent.available is False and absent.reason == "absent"


# ---- capture → journal → sole publisher --------------------------------------


async def test_capture_publishes_in_order_through_redis_outage(
        redis_client, pg_url, tmp_path, daemon):
    """Real outage: Redis refused, capture keeps receiving into the journal,
    nothing is published, the head is never skipped. On recovery the head
    drains in FIFO order and a NEW capture (arriving during recovery) still
    cannot outrun it — reverting _handle to a direct XADD makes this RED."""
    _projection(tmp_path)
    kw = redis_client.connection_pool.connection_kwargs
    proxy = TcpProxy(kw["host"], kw["port"])
    await proxy.start()
    cfg = _cfg(tmp_path, signal_http=daemon.url,
               redis_url=f"redis://127.0.0.1:{proxy.port}/0",
               database_url=pg_url)
    bridge, publisher, client = await _spawn(cfg)
    try:
        proxy.set_up(False)
        await _captured(bridge, daemon, [
            sig_event(SENDER, "first", 1001),
            sig_event(SENDER, "second", 1002)])
        await _wait_for(lambda: len([
            r for r in bridge.journal.recover() if "envelope" in r]) >= 2)
        # The head is blocked: only non-envelope records (preflight probes)
        # ever pass it — no envelope was published during the outage.
        recs = bridge.journal.recover()
        assert all("envelope" not in r
                   for r in recs[:bridge._replay_offset])
        proxy.set_up(True)
        await _wait_for(lambda: _drained(bridge))
        # A fresh capture after recovery joins the journal tail — it may
        # only publish AFTER the journaled head.
        await _captured(bridge, daemon, [sig_event(SENDER, "third", 1003)])
        await _wait_for(lambda: _drained(bridge))
        bodies = [e.message["body"] for e in
                  await _stream_envelopes(redis_client)]
        assert bodies[:2] == ["first", "second"]
        assert "third" in bodies
    finally:
        await _teardown(bridge, publisher, client)
        await proxy.close()


async def test_receipt_intent_frozen_in_envelope(bridge_app):
    """Receipt eligibility is decided AT CAPTURE and frozen in `env.receipt`:
    an addressed (mentioning) event gets intent; an ambient room message
    does not — though both are admitted."""
    bridge, _, daemon = bridge_app
    await _captured(bridge, daemon, [
        sig_event(SENDER, "ambient hello", 1101),
        sig_event(SENDER, "￼ hey", 1102, mention_own=True)])
    await _wait_for(lambda: len([
        r for r in bridge.journal.recover() if "envelope" in r]) >= 2)
    recs = [r for r in bridge.journal.recover() if "envelope" in r]
    envs = [wrapper.Envelope.from_fields(r["envelope"]) for r in recs]
    by_ts = {e.sent_ms: e for e in envs}
    assert by_ts[1101].receipt is None             # ambient — no intent
    assert by_ts[1102].receipt == {"emoji": "👍"}  # addressed — intent frozen


async def test_receipt_sent_once_for_addressed_event(bridge_app):
    """The full path: capture (intent frozen) → enqueue → durable receipt →
    exactly one upstream reaction and one confirmed action row."""
    bridge, _, daemon = bridge_app
    await _captured(bridge, daemon, [
        sig_event(SENDER, "￼ hey", 1201, mention_own=True)])
    await _wait_for(lambda: len(daemon.reactions) >= 1)
    assert len(daemon.reactions) == 1
    import sqlalchemy as sa

    from nora.infra.sql.schema import BRIDGE_ACTION
    async with bridge.store._engine.connect() as conn:
        rows = (await conn.execute(sa.select(BRIDGE_ACTION).where(
            BRIDGE_ACTION.c.op == "react"))).mappings().all()
    assert len(rows) == 1 and rows[0]["state"] == "confirmed"
    assert rows[0]["action_id"].startswith("receipt:v1:")


async def test_unaddressed_capture_gets_no_receipt(bridge_app):
    """An admitted but unaddressed room message IS enqueued — ambient
    retention — but grows no reaction and no action row."""
    bridge, _, daemon = bridge_app
    await _captured(bridge, daemon, [sig_event(SENDER, "ambient", 1301)])
    await _wait_for(lambda: _drained(bridge))
    await asyncio.sleep(0.3)  # a receipt task would have run by now
    assert len(daemon.reactions) == 0


async def test_denied_room_is_never_journaled(bridge_app):
    """A denied room leaves no body, no envelope, no journal line."""
    bridge, _, daemon = bridge_app
    await _captured(bridge, daemon, [
        sig_event(SENDER, "off-limits", 1401, group="g-denied")],
        deadline_s=1.5)
    recs = [r for r in bridge.journal.recover() if "envelope" in r]
    assert recs == []


async def test_denied_sender_mention_captured_but_no_receipt(bridge_app):
    """room_receipt_senders is the group receipt allowlist: a sender OUTSIDE
    it who @-mentions Nora in an admitted room is still CAPTURED and
    enqueued — retention — but deterministically gets no reaction."""
    bridge, _, daemon = bridge_app
    await _captured(bridge, daemon, [
        sig_event("+00000000009", "￼ hey", 1402, mention_own=True)])
    await _wait_for(lambda: _drained(bridge))
    await asyncio.sleep(0.3)
    assert len(daemon.reactions) == 0
    # …but the event was still captured and published.
    assert len(await _stream_envelopes_for(bridge)) == 1


async def test_wildcard_room_member_addressed_gets_receipt(
        bridge_app, tmp_path):
    """`*` in room_receipt_senders grants any room member receipt
    eligibility — still requiring the deterministic addressed check."""
    _projection(tmp_path, room_receipt_senders={ROOM: ["*"]})
    bridge, _, daemon = bridge_app
    await _captured(bridge, daemon, [
        sig_event("+00000000009", "￼ hey", 1403, mention_own=True)])
    await _wait_for(lambda: len(daemon.reactions) >= 1)
    assert len(daemon.reactions) == 1


async def test_journal_entry_without_intent_stays_silent(bridge_app):
    """A journal record carrying NO receipt intent — captured before the
    feature, or ineligible — never grows a reaction on replay even with
    receipts enabled now."""
    bridge, _, daemon = bridge_app
    env = _envelope("src-ambient", receipt=None)
    bridge.journal.append({"envelope": env.to_fields()})
    bridge._dirty.set()  # what _handle does after every append
    await _wait_for(lambda: _drained(bridge))
    await asyncio.sleep(0.3)
    assert len(daemon.reactions) == 0


async def test_revoked_intent_suppresses_queued_receipt(
        redis_client, pg_url, tmp_path, daemon):
    """Intent frozen at capture is still re-checked against CURRENT
    projection at effect time: disabling receipts while the head is blocked
    suppresses the queued reaction entirely."""
    _projection(tmp_path)
    kw = redis_client.connection_pool.connection_kwargs
    proxy = TcpProxy(kw["host"], kw["port"])
    await proxy.start()
    cfg = _cfg(tmp_path, signal_http=daemon.url,
               redis_url=f"redis://127.0.0.1:{proxy.port}/0",
               database_url=pg_url)
    bridge, publisher, client = await _spawn(cfg)
    try:
        proxy.set_up(False)
        await _captured(bridge, daemon, [
            sig_event(SENDER, "￼ hey", 1501, mention_own=True)])
        await _wait_for(lambda: len([
            r for r in bridge.journal.recover() if "envelope" in r]) >= 1)
        _projection(tmp_path, receipt_emoji="")  # revoke before recovery
        proxy.set_up(True)
        await _wait_for(lambda: _drained(bridge))
        await asyncio.sleep(0.3)
        assert len(daemon.reactions) == 0  # suppressed — intent revoked
    finally:
        await _teardown(bridge, publisher, client)
        await proxy.close()


async def test_receipt_reserve_failure_retries_without_restart(
        redis_client, pg_url, tmp_path, daemon):
    """Enqueue succeeds while the action ledger is down: the receipt stays
    PENDING in the publisher's retry set — it lands when Postgres returns,
    no restart, exactly one effect."""
    _projection(tmp_path)
    gated = GatedEngine(pg_url)
    gated.down = True
    cfg = _cfg(tmp_path, signal_http=daemon.url,
               redis_url=_redis_url(redis_client), database_url=pg_url)
    bridge, publisher, client = await _spawn(
        cfg, store=SqlBridgeStore(gated))
    try:
        await _captured(bridge, daemon, [
            sig_event(SENDER, "￼ hey", 1601, mention_own=True)])
        await _wait_for(lambda: _drained(bridge))
        await asyncio.sleep(0.3)
        assert len(daemon.reactions) == 0  # enqueued; receipt still pending
        assert bridge._pending_receipts     # parked, not dropped
        gated.down = False
        await _wait_for(lambda: len(daemon.reactions) >= 1)
        assert len(daemon.reactions) == 1
        assert not bridge._pending_receipts
    finally:
        await _teardown(bridge, publisher, client)


# ---- closed typed owner authority + durable pending-row mapping -------------


async def test_owner_gate_denies_every_unbound_shape(bridge_app):
    """Closed set, closed state: every malformed, unbound, foreign-platform,
    wrong-target, resolved, expired or evidence-free owner is refused BEFORE
    the CAS — zero upstream effects, zero action rows."""
    bridge, client, daemon = bridge_app
    foreign, _rid = await _live_owner(bridge, platform="whatsapp")
    wrong_target, _ = await _live_owner(bridge, conversation_id="conv-other")
    resolved, _ = await _live_owner(bridge, delivery_status="sent")
    expired_otp, _ = await _otp_owner(
        bridge, expires_in=timedelta(minutes=-1))
    no_pending_otp, _ = await _otp_owner(bridge, with_pending=False)
    foreign_otp, _ = await _otp_owner(bridge, platform="whatsapp")

    cases = [
        # (name, body-mutation, expected status)
        ("missing", lambda b: b.pop("owner"), 400),
        ("bad kind", lambda b: b["owner"].update(kind="nope"), 400),
        ("malformed work id",
         lambda b: b["owner"].update(kind="work", id="w-xyz"), 403),
        ("work missing generation",
         lambda b: b["owner"].update(
             kind="work", id="w-1", generation=None), 403),
        ("unbound live_invocation",
         lambda b: b["owner"].update(kind="live_invocation", id="app"), 403),
        ("live: foreign platform row",
         lambda b: b["owner"].update(**foreign), 403),
        ("live: wrong conversation",
         lambda b: b["owner"].update(**wrong_target), 403),
        ("live: row already resolved",
         lambda b: b["owner"].update(**resolved), 403),
        ("otp: expired",
         lambda b: b["owner"].update(**expired_otp), 403),
        ("otp: no pending outbound row",
         lambda b: b["owner"].update(**no_pending_otp), 403),
        ("otp: foreign platform",
         lambda b: b["owner"].update(**foreign_otp), 403),
        ("receipt: no journaled intent",
         lambda b: b["owner"].update(kind="event_receipt",
                                     id="never-captured"), 403),
    ]
    for i, (name, mutate, want) in enumerate(cases):
        body = _send(f"deny-{i}", {"kind": "live_invocation", "id": "1"})
        mutate(body)
        r = await client.post("/v1/actions", json=body, headers=AUTH)
        assert r.status == want, f"{name}: {r.status} != {want} ({await r.json()})"
        assert await bridge.store.action(f"deny-{i}") is None, name
    assert len(daemon.sent) == 0


async def test_work_owner_real_row_authority(bridge_app):
    """A real live `bridge_work` row with matching conversation/generation
    executes; the same owner with a stale generation or terminal state is
    denied before any effect."""
    bridge, client, daemon = bridge_app
    owner = await _work_owner(bridge, generation=3)
    r = await client.post("/v1/actions", json=_send("w-ok", owner), headers=AUTH)
    assert (await r.json())["state"] == "confirmed"
    assert len(daemon.sent) == 1
    # Stale generation — the row moved on; the claim is dead.
    stale = await _work_owner(bridge, generation=1)
    stale["generation"] = 0
    r = await client.post("/v1/actions", json=_send("w-stale", stale),
                          headers=AUTH)
    assert r.status == 403
    # Terminal work keeps no authority.
    dead = await _work_owner(bridge, state="done")
    r = await client.post("/v1/actions", json=_send("w-dead", dead), headers=AUTH)
    assert r.status == 403
    # Live-but-unclaimed: a re-parked row is not acting either — the next
    # claimer's generation is the only one that may sign.
    parked = await _work_owner(bridge, state="pending", lease_seconds=None)
    r = await client.post("/v1/actions", json=_send("w-parked", parked),
                          headers=AUTH)
    assert r.status == 403
    # A lease the DB clock reads as dead — the expire_leases sweep need not
    # have run for the owner to be refused.
    lapsed = await _work_owner(bridge, lease_seconds=-60)
    r = await client.post("/v1/actions", json=_send("w-lapsed", lapsed),
                          headers=AUTH)
    assert r.status == 403
    # Foreign platform/account.
    alien = await _work_owner(bridge, platform="discord")
    r = await client.post("/v1/actions", json=_send("w-alien", alien),
                          headers=AUTH)
    assert r.status == 403
    assert len(daemon.sent) == 1


async def test_renew_lease_is_fenced(bridge_app):
    """`renew_lease` extends only the CURRENT holder's lease — a stale
    generation, a foreign holder, a re-parked row or a dead lease all write
    nothing and answer False."""
    bridge, _, _ = bridge_app
    owner = await _work_owner(bridge, generation=1, lease_seconds=60)
    wid = int(owner["id"][2:])
    store = bridge.store
    assert await store.renew_lease(
        wid, generation=1, owner="nora-test", lease_seconds=600) is True
    async with store._engine.begin() as conn:
        until = (await conn.execute(sa.select(
            BRIDGE_WORK.c.leased_until).where(
            BRIDGE_WORK.c.id == wid))).scalar_one()
        assert until > datetime.now(UTC) + timedelta(minutes=9)
    # Stale generation / foreign holder / wrong state / dead lease.
    assert await store.renew_lease(
        wid, generation=0, owner="nora-test", lease_seconds=600) is False
    assert await store.renew_lease(
        wid, generation=1, owner="someone-else", lease_seconds=600) is False
    parked = await _work_owner(bridge, state="pending", lease_seconds=None)
    assert await store.renew_lease(
        int(parked["id"][2:]), generation=1, owner="nora-test",
        lease_seconds=600) is False
    lapsed = await _work_owner(bridge, lease_seconds=-60)
    assert await store.renew_lease(
        int(lapsed["id"][2:]), generation=1, owner="nora-test",
        lease_seconds=600) is False


async def test_expire_leases_respects_live_speaking_session(bridge_app):
    """An expired lease alone is not a dead owner: while the speaking
    advisory lock for (platform, conversation) is still GRANTED — the
    server-side proof a backend still speaks for that room — the row keeps
    its claim. Releasing the lock lets the sweep re-park it."""
    bridge, _, _ = bridge_app
    store = bridge.store
    owner = await _work_owner(bridge, lease_seconds=-60)  # already dead
    wid = int(owner["id"][2:])
    lock = await store._engine.connect()
    try:
        got = (await lock.execute(sa.text(
            "SELECT pg_try_advisory_lock(hashtext(:ns), hashtext(:k))"),
            {"ns": "nora-bridge-speak", "k": f"signal:{ROOM}"})).scalar_one()
        assert got is True
        await store.expire_leases()
        async with store._engine.begin() as conn:
            state = (await conn.execute(sa.select(
                BRIDGE_WORK.c.state).where(
                BRIDGE_WORK.c.id == wid))).scalar_one()
        assert state == "running"  # live session: the claim is not stolen
    finally:
        await lock.execute(sa.text(
            "SELECT pg_advisory_unlock(hashtext(:ns), hashtext(:k))"),
            {"ns": "nora-bridge-speak", "k": f"signal:{ROOM}"})
        await lock.close()
    await store.expire_leases()
    async with store._engine.begin() as conn:
        state, gen = (await conn.execute(sa.select(
            BRIDGE_WORK.c.state, BRIDGE_WORK.c.generation).where(
            BRIDGE_WORK.c.id == wid))).one()
        # Holder gone: re-parked AND generation-bumped — a finish that
        # raced the expire→reclaim window carries the stale generation
        # and lands nothing.
        assert state == "pending" and gen == int(owner["generation"]) + 1


async def test_otp_send_end_to_end_same_pending_row_redacted(bridge_app):
    """The REAL deliverer path `OtpService` drives: bound otp_challenge
    owner → `store.append` (pending audit row, code MASKED) → bridge
    validates challenge + row → effect → confirmed → the SAME row is marked
    sent. The action's persisted request never holds the cleartext code,
    while the request HASH still fences a different code on the same id."""
    from nora.doctrine.loader import Doctrine
    from nora.domain.actions import ActionOwner
    from nora.domain.audience import ONE_TIME_CODE
    from nora.domain.ports import ConversationTarget
    from nora.engagement.stages import deliver
    from nora.infra.sql.audit import SqlRecordStore
    from nora.obs.metrics import Metrics
    from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint

    bridge, client, daemon = bridge_app
    _o, cid = await _otp_owner(bridge, with_pending=False)
    base = str(client.make_url("")).rstrip("/")
    adapter = BridgeAdapter(
        "signal", BridgeEndpoint(base_url=base, token="test-token"))  # noqa: S106
    bound = adapter.bind_owner(ActionOwner(kind="otp_challenge", id=cid))
    records = SqlRecordStore(bridge.store._engine)
    handle = deliver.build(
        store=records, adapter=bound,
        target=ConversationTarget("signal", ROOM, "group"),
        doctrine=Doctrine(
            sections=(), text="<nora_doctrine>otp</nora_doctrine>",
            doctrine_text="otp", sha256="deadbeef", runtime_context="",
            loaded_at=0.0),
        canary="CANARY", tag_id=f"otp-{cid}", nora_sender_id=OWN,
        conversation_id=ROOM, budgets={"send_reply": 1},
        terminal=frozenset(), metrics=Metrics(), audience=ONE_TIME_CODE,
        composer=None)
    ok = await handle.deliverer.deliver(
        "314159 is your Nora sign-in code. It expires in 5 minutes. "
        "Nobody will ever ask you for it, not even Nora.",
        source=ONE_TIME_CODE,
        record_text="······ is your Nora sign-in code. It expires in "
                    "5 minutes. Nobody will ever ask you for it, not even Nora.")
    assert ok is True
    assert len(daemon.sent) == 1  # the REAL code left on the wire only
    async with bridge.store._engine.connect() as conn:
        act = (await conn.execute(sa.select(BRIDGE_ACTION).where(
            BRIDGE_ACTION.c.action_id == f"{cid}:send:0"))).mappings().one()
        msg = (await conn.execute(sa.select(MESSAGES).where(
            MESSAGES.c.id == act["message_id"]))).mappings().one()
    assert act["state"] == "confirmed"
    assert act["owner_kind"] == "otp_challenge" and act["owner_id"] == cid
    # The SAME pending audit row got the confirmation — no second outbound.
    assert msg["delivery_status"] == "sent" and msg["is_nora"] == 1
    assert f"otp-{cid}" in msg["raw_json"]
    # Credential never rests: stored request is redacted, hash stayed full.
    assert "314159" not in json.dumps(act["request"])
    assert act["request"]["text"] == "[redacted]"
    await adapter.close()


async def test_live_invocation_bound_by_deliverer_record_first(bridge_app):
    """An UNBOUND bridge adapter through `Deliverer` mints its durable owner
    from the pending audit row it just committed — the row that does not
    exist until `append` lands is exactly the row that authorizes the send."""
    from nora.doctrine.loader import Doctrine
    from nora.domain.ports import ConversationTarget
    from nora.engagement.stages import deliver
    from nora.infra.sql.audit import SqlRecordStore
    from nora.obs.metrics import Metrics
    from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint

    bridge, client, daemon = bridge_app
    base = str(client.make_url("")).rstrip("/")
    adapter = BridgeAdapter(
        "signal", BridgeEndpoint(base_url=base, token="test-token"))  # noqa: S106
    handle = deliver.build(
        store=SqlRecordStore(bridge.store._engine), adapter=adapter,
        target=ConversationTarget("signal", ROOM, "group"),
        doctrine=Doctrine(
            sections=(), text="<nora_doctrine>live</nora_doctrine>",
            doctrine_text="live", sha256="deadbeef", runtime_context="",
            loaded_at=0.0),
        canary="CANARY", tag_id="live-tag-1", nora_sender_id=OWN,
        conversation_id=ROOM, budgets={"send_reply": 1},
        terminal=frozenset(), metrics=Metrics(), audience="tag",
        composer=None)
    assert await handle.deliverer.deliver("spoken reply") is True
    assert len(daemon.sent) == 1
    async with bridge.store._engine.connect() as conn:
        act = (await conn.execute(sa.select(BRIDGE_ACTION).where(
            BRIDGE_ACTION.c.owner_kind == "live_invocation"))).mappings().one()
        msg = (await conn.execute(sa.select(MESSAGES).where(
            MESSAGES.c.id == act["message_id"]))).mappings().one()
    # The owner IS the pending row it confirmed — bound record-first.
    assert act["owner_id"] == str(act["message_id"])
    assert act["state"] == "confirmed"
    assert msg["delivery_status"] == "sent"
    await adapter.close()


async def test_same_action_different_code_conflicts(bridge_app):
    """`request_hash` is over the FULL request: two sends sharing an
    action_id but differing ONLY in the code stay fenced — replay protection
    outlives the redaction."""
    bridge, client, daemon = bridge_app
    owner, cid = await _otp_owner(bridge)
    first = await client.post(
        "/v1/actions", json=_send(f"{cid}:send:0", owner,
                                  text="code is 111111"), headers=AUTH)
    assert (await first.json())["state"] == "confirmed"
    second = await client.post(
        "/v1/actions", json=_send(f"{cid}:send:0", owner,
                                  text="code is 222222"), headers=AUTH)
    assert second.status == 409
    assert len(daemon.sent) == 1  # the second code never left
    again = await client.post(
        "/v1/actions", json=_send(f"{cid}:send:0", owner,
                                  text="code is 111111"), headers=AUTH)
    assert (await again.json())["state"] == "confirmed"  # verbatim replay
    assert len(daemon.sent) == 1


async def test_uncertain_send_reaches_deliverer_as_uncertain(bridge_app):
    """A lost upstream answer reaches the Deliverer as `send_uncertain`:
    the action row persists `uncertain`, the audit row RESOLVES (a pending
    row would keep the live_invocation owner valid and invite a blind
    second send), and no second send is attempted."""
    from nora.doctrine.loader import Doctrine
    from nora.domain.ports import ConversationTarget
    from nora.engagement.stages import deliver
    from nora.infra.sql.audit import SqlRecordStore
    from nora.obs.metrics import Metrics
    from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint

    bridge, client, daemon = bridge_app
    base = str(client.make_url("")).rstrip("/")
    adapter = BridgeAdapter(
        "signal", BridgeEndpoint(base_url=base, token="test-token"))  # noqa: S106
    records = SqlRecordStore(bridge.store._engine)
    handle = deliver.build(
        store=records, adapter=adapter,
        target=ConversationTarget("signal", ROOM, "group"),
        doctrine=Doctrine(
            sections=(), text="<nora_doctrine>u</nora_doctrine>",
            doctrine_text="u", sha256="deadbeef", runtime_context="",
            loaded_at=0.0),
        canary="CANARY", tag_id="live-tag-unc", nora_sender_id=OWN,
        conversation_id=ROOM, budgets={"send_reply": 1},
        terminal=frozenset(), metrics=Metrics(), audience="tag",
        composer=None)
    daemon.drop_next_rpc = 1  # send lands, answer lost
    assert await handle.deliverer.deliver("maybe landed") is False
    assert handle.deliverer.last_outcome == "send_uncertain"
    assert len(daemon.sent) == 1  # ONE effect; nothing retried blindly
    async with bridge.store._engine.connect() as conn:
        act = (await conn.execute(sa.select(BRIDGE_ACTION).where(
            BRIDGE_ACTION.c.owner_kind == "live_invocation"))).mappings().one()
        msg = (await conn.execute(sa.select(MESSAGES).where(
            MESSAGES.c.id == act["message_id"]))).mappings().one()
    assert act["state"] == "uncertain"
    # Resolved as its own terminal disposition — never `pending` (a live
    # owner would invite a blind resend), never `failed` (an outcome nobody
    # disproved is not a proven non-send).
    assert msg["delivery_status"] == "uncertain"
    await adapter.close()


async def test_lost_post_answer_reconciles_same_row_by_get(bridge_app):
    """A POST /v1/actions whose answer was lost does NOT make the client
    send a second time: `BridgeAdapter.send` reconciles through
    `GET /v1/actions/{id}` — the deterministic action id names the SAME
    reservation, `message_id` binds the SAME pending audit row. A landed
    `confirmed` reads as delivered, a 404 leaves an in-flight POST uncertain, and a row
    that bound a different audit row stays `uncertain`, never claimed."""
    from nora.domain.actions import ActionOwner
    from nora.domain.ports import ConversationTarget
    from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint, BridgeUnavailableError

    bridge, client, _ = bridge_app
    base = str(client.make_url("")).rstrip("/")

    async def pending_row() -> int:
        async with bridge.store._engine.begin() as conn:
            return (await conn.execute(sa.insert(MESSAGES).values(
                platform="signal", conversation_id=ROOM,
                conversation_type="group", sender_id=OWN, ts=1,
                body="x", is_nora=1, delivery_status="pending",
                raw_json="{}").returning(MESSAGES.c.id))).scalar_one()

    async def seed(mid: int, state: str, *, bound_id: int | None = None) -> None:
        """A `live_invocation` row exactly as the bridge persisted it:
        owner.id == message_id == the pending audit row, action_id the
        deterministic `{owner.id}:send:0` the bound client mints."""
        async with bridge.store._engine.begin() as conn:
            await bridge.store.reserve_action(
                conn, action_id=f"{mid}:send:0", platform="signal",
                account_key=ACCT, owner_kind="live_invocation",
                owner_id=str(mid), owner_generation=None, op="send",
                request={"target": {"kind": "group", "id": ROOM}, "text": "x"},
                message_id=mid if bound_id is None else bound_id)
            await bridge.store.action_state(
                conn, f"{mid}:send:0", state, platform_ref=f"ref-{mid}")

    class DropPost(BridgeAdapter):
        async def _post(self, path, body):  # answer lost after the call
            raise BridgeUnavailableError("post answer lost")

    target = ConversationTarget("signal", ROOM, "group")

    def adapter_for(mid: int) -> DropPost:
        return DropPost(
            "signal", BridgeEndpoint(base_url=base, token="test-token"),  # noqa: S106
            owner=ActionOwner(kind="live_invocation", id=str(mid)))

    # Landed `confirmed` + matching bound row → ok, NO second upstream send.
    mid = await pending_row()
    await seed(mid, "confirmed")
    res = await adapter_for(mid).send(target=target, text="x")
    assert res.ok is True

    # `message_id` bound a DIFFERENT audit row → cannot claim → uncertain.
    mid, other = await pending_row(), await pending_row()
    await seed(mid, "confirmed", bound_id=other)
    res = await adapter_for(mid).send(target=target, text="x")
    assert res.ok is False and res.uncertain is True

    # An absent row cannot disprove a POST that is still in flight.
    mid = await pending_row()
    res = await adapter_for(mid).send(target=target, text="x")
    assert res.ok is False and res.uncertain is True


async def test_event_receipt_owner_requires_journal_intent(bridge_app):
    """An HTTP `event_receipt` owner is honored ONLY for an event the
    bridge's own journal holds WITH a frozen receipt intent — the internal
    id cannot be borrowed for an arbitrary effect."""
    bridge, client, daemon = bridge_app
    await _captured(bridge, daemon, [
        sig_event(SENDER, "￼ hey", 1701, mention_own=True)])
    await _wait_for(lambda: _drained(bridge))
    intents = bridge._receipt_intent_ids()
    assert intents  # the journaled addressed event minted receipt authority
    body = {"action_id": "r-http-1", "op": "react",
            "owner": {"kind": "event_receipt", "id": next(iter(intents))},
            "request": {"target": {"id": ROOM, "kind": "group"},
                        "emoji": "👀", "to_author": SENDER,
                        "to_timestamp": 1701}}
    r = await client.post("/v1/actions", json=body, headers=AUTH)
    assert (await r.json())["state"] == "confirmed"
    assert len(daemon.reactions) >= 1
    row = await bridge.store.action("r-http-1")
    assert row["owner_kind"] == "event_receipt" and row["state"] == "confirmed"


def _envelope(sk: str, receipt: dict | None):
    from nora.platform.bridge.envelope import Envelope, event_id
    return Envelope(
        platform="signal", account_key=ACCT, kind="message", source_key=sk,
        event_id=event_id(sk, key=b"test-correlation-key", key_version=1),
        conversation_id=ROOM, conversation_type="group",
        sender_id=SENDER, sender_name="Peer", sent_ms=1000,
        received_ms=1000, queued_ms=1000, time_quality="upstream",
        message={"platform": "signal", "conversation_id": ROOM,
                 "conversation_type": "group", "sender_id": SENDER,
                 "sender_name": "Peer", "timestamp": 1000,
                 "body": "x", "raw": {}, "message_id": ""},
        attachments=(), trace={}, receipt=receipt, admission_rev=0)
