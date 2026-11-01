"""The platform-bridge handoff against REAL Postgres + Redis
(design/platform-bridges).

Every test drives the production seam: `StreamProducer.enqueue` (the dedupe
Lua) → `StreamReader._register` (atomic event+obligation) →
`WorkRunner._execute` (claim, ingest under `tx_hook`, obligation commit, then
XACK — never in a `finally`). The pipelines are the harness's real
`IngestPipeline`/`EngagementPipeline`; only the agent runner, Honcho and the
transport adapter are fakes — the same fakes every tag-path test uses.
"""

import asyncio
import contextlib
import json
from dataclasses import replace

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.app.bridge_queue import DbSpeakingLocks, StreamReader, WorkRunner, stream_name
from nora.domain.models import NormalizedMessage
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.schema import BRIDGE_WORK, MESSAGES
from nora.platform.bridge.envelope import Envelope, canonical_source_key, event_id
from nora.platform.bridge.queue import CONSUMER_GROUP, StreamProducer, ensure_group
from nora.platform.bridge.rehydrate import message_to_dict
from nora.testing import ScriptedTurn

pytestmark = pytest.mark.integration

KEY = b"test-correlation-key"
PLATFORM, ACCOUNT, CONV = "signal", "+00000000001", "g-bridge"


def _msg(body: str, ts: int) -> dict:
    return message_to_dict(NormalizedMessage(
        platform=PLATFORM, conversation_id=CONV, conversation_type="group",
        sender_id="+00000000002", sender_name="Sender", timestamp=ts,
        body=body, raw={"dataMessage": {"message": body}}, message_id=f"m{ts}"))


def _envelope(body: str, ts: int, *, dedupe_suffix: str | None = None,
              receipt: dict | None = None, key_version: int = 1) -> Envelope:
    sk = canonical_source_key(
        PLATFORM, ACCOUNT, CONV, "message", dedupe_suffix or f"{ts}")
    return Envelope(
        platform=PLATFORM, account_key=ACCOUNT, kind="message",
        source_key=sk, event_id=event_id(sk, key=KEY, key_version=key_version),
        conversation_id=CONV, conversation_type="group",
        sender_id="+00000000002", sender_name="Sender", sent_ms=ts,
        received_ms=ts + 1, queued_ms=ts + 2, time_quality="upstream",
        message=_msg(body, ts), receipt=receipt, admission_rev=1)


@pytest.fixture
async def bridge(pg_url, redis_client):
    store = SqlBridgeStore(create_async_engine(pg_url))
    stream = stream_name(PLATFORM, ACCOUNT)
    await ensure_group(redis_client, stream)
    yield store, redis_client, stream
    await store._engine.dispose()


def _reader(redis_client, store, stream) -> StreamReader:
    r = StreamReader(platform=PLATFORM, account_key=ACCOUNT,
                     redis=redis_client, store=store)
    assert r.stream == stream
    return r


def _runner(store, redis_client, harness, **kw) -> WorkRunner:
    return WorkRunner(
        store=store, redis=redis_client,
        ingest=harness.handler._dispatcher._ingest,
        engagement=harness._engagement,
        slots=harness._engagement._slots, db_locks=DbSpeakingLocks(store),
        adapters={PLATFORM: harness.sink}, gate=None,
        metrics=harness.metrics, **kw)


async def _pending(redis_client, stream) -> int:
    info = await redis_client.xpending(stream, CONSUMER_GROUP)
    return info["pending"] if isinstance(info, dict) else info[0]


async def _produce(redis_client, stream, env: Envelope) -> str:
    sid, _canonical = await StreamProducer(redis_client, stream).enqueue(env)
    return sid


async def _wait_event(store, event_id: str):
    for _ in range(150):
        row = await store.event(event_id)
        if row is not None:
            return row
        await asyncio.sleep(0.1)
    raise TimeoutError(f"event {event_id} never registered")


async def test_signal_event_flows_audit_work_and_xack(bridge, redis_client, harness):
    store, _, stream = bridge
    env = _envelope("hello room", 1000)
    sid = await _produce(redis_client, stream, env)

    reader = _reader(redis_client, store, stream)
    reader_task = asyncio.create_task(reader._read())
    try:
        event = await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task
    assert event["stream_id"] == sid
    # Registration commits the ingest obligation but does NOT ack.
    assert await _pending(redis_client, stream) == 1

    runner = _runner(store, redis_client, harness)
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    assert work is not None and work.kind == "ingest"
    await runner._execute(work)

    # The audit row + the mapping landed under one commit (the tx_hook stamped
    # the row id onto the event payload inside append's own transaction).
    event = await store.event(env.event_id)
    assert event["payload"]["message_row_id"]
    async with store._engine.connect() as conn:
        rows = (await conn.execute(sa.select(MESSAGES).where(
            MESSAGES.c.id == event["payload"]["message_row_id"]))).all()
    assert len(rows) == 1
    assert event["state"] == "done"
    assert await _pending(redis_client, stream) == 0  # ack AFTER the commit


async def test_projection_revision_survives_registration_ingest_and_ack(bridge, harness):
    store, redis_client, stream = bridge
    revision = (1 << 53) - 1
    env = replace(_envelope("revision boundary", 1002), admission_rev=revision)
    sid = await _produce(redis_client, stream, env)
    batch = await redis_client.xreadgroup(
        CONSUMER_GROUP, "revision-reader", {stream: ">"}, count=1)
    claimed_sid, fields = batch[0][1][0]
    await _reader(redis_client, store, stream)._register(claimed_sid, fields)

    event = await store.event(env.event_id)
    assert event is not None and event["admission_rev"] == revision
    assert event["stream_id"] == sid
    assert await _pending(redis_client, stream) == 1
    work = await store.claim_next(owner="revision-worker", lease_seconds=60)
    assert work is not None and work.kind == "ingest"
    await _runner(store, redis_client, harness)._execute(work)

    event = await store.event(env.event_id)
    assert event["admission_rev"] == revision
    assert event["payload"]["message_row_id"]
    assert event["state"] == "done"
    assert await _pending(redis_client, stream) == 0


async def test_duplicate_delivery_registers_once_and_acks_when_owned(
        bridge, redis_client, harness):
    store, _, stream = bridge
    env = _envelope("twice", 1001)
    sid1 = await _produce(redis_client, stream, env)
    sid2 = await _produce(redis_client, stream, env)  # Lua dedupe: same sid
    assert sid2 == sid1  # producer returns the ORIGINAL stream id
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    async with store._engine.connect() as conn:
        work_rows = (await conn.execute(sa.select(BRIDGE_WORK))).all()
    assert len(work_rows) == 1  # one ingest obligation, not two


async def test_rotated_alias_replay_returns_original_entry(
        bridge, redis_client):
    """The regression a bare alias-dedupe misses: after a correlation-key
    rotation the same source mints a NEW event_id — the Lua must still return
    the original stream id and XADD nothing (no second entry, no second
    receipt)."""
    _, redis, stream = bridge
    v1 = _envelope("rotated", 1003, key_version=1)
    sid1, canon1 = await StreamProducer(redis, stream).enqueue(v1)
    v2 = _envelope("rotated", 1003, key_version=2)  # new alias, same source
    assert v2.event_id != v1.event_id
    sid2, canon2 = await StreamProducer(redis, stream).enqueue(v2)
    assert sid2 == sid1
    # Downstream effects (receipt, action ids) key on the CANONICAL id — the
    # rotated retry's new alias never escapes into a second effect.
    assert canon2 == canon1 == v1.event_id
    assert await redis.xlen(stream) == 1


async def test_audit_hook_rolls_back_atomically(bridge, redis_client, harness):
    """A hook that raises inside append's transaction leaves NO row: no
    message, no stamped mapping — the work retries clean, not half-written."""
    store, _, stream = bridge
    env = _envelope("boom", 1002)
    await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    real_run = harness.handler._dispatcher._ingest.run

    async def exploding_hook(conn, row_id, context):
        raise RuntimeError("hook fault")

    class ExplodingIngest:
        async def run(self, *, message, tx_hook=None, durable=False):
            return await real_run(message=message, tx_hook=exploding_hook,
                                  durable=durable)

    runner = _runner(store, redis_client, harness)
    runner._ingest = ExplodingIngest()
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(work)
    async with store._engine.connect() as conn:
        assert (await conn.execute(sa.select(MESSAGES))).all() == []
        row = (await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == work.id))).mappings().one()
    assert row["state"] == "retryable_failure"
    assert await _pending(redis_client, stream) == 1  # still pending


async def test_audit_failure_is_retryable_not_silence(
        bridge, redis_client, harness, tmp_path):
    """A swallowed audit failure (`audit_refused`) retires as a typed failure,
    never an `intentional_skip`. The fault is a tx_hook explosion — the
    same-commit write the bridge adds to the audit append — because
    `db_append_fails` only fails `is_nora=True` appends and can never reach
    an inbound user message's audit write."""
    store, _, stream = bridge
    env = _envelope("lost write", 1003)
    await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    real_run = harness.handler._dispatcher._ingest.run

    async def exploding_hook(conn, row_id, context):
        raise RuntimeError("hook fault")

    class ExplodingIngest:
        async def run(self, *, message, tx_hook=None, durable=False):
            return await real_run(message=message, tx_hook=exploding_hook,
                                  durable=durable)

    runner = _runner(store, redis_client, harness)
    runner._ingest = ExplodingIngest()
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(work)
    # The event stays OPEN while its obligation retries — `failed_retained`
    # is the terminal answer after attempts exhaust, not the first failure's.
    event = await store.event(env.event_id)
    assert event["state"] == "open"
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == work.id))).mappings().one()
    assert row["state"] == "retryable_failure"
    assert row["result"]["outcome"] == "audit_refused"
    assert await _pending(redis_client, stream) == 1


async def test_kill_after_commit_before_ack_recovers(
        bridge, redis_client, harness, monkeypatch):
    """Commit obligations, die before XACK: the entry stays pending, the next
    registration finds the event's work all terminal and acks then."""
    store, _, stream = bridge
    env = _envelope("crash window", 1004)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    runner = _runner(store, redis_client, harness)
    runner._gate = harness.handler._dispatcher._ingest._policy
    monkeypatch.setattr(runner, "_ack",
                        lambda w: (_ for _ in ()).throw(RuntimeError("killed")))
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    with contextlib.suppress(RuntimeError):
        await runner._execute(work)
    assert await _pending(redis_client, stream) == 1  # died BEFORE the ack

    # The exception retired the obligation retryably — a pending entry whose
    # registration must NOT ack while work is still owed.
    await reader._register(sid, {"e": json.dumps(env.to_fields())})
    assert await _pending(redis_client, stream) == 1

    # Reclaim: the committed-audit replay guard skips re-ingest, re-derives the
    # gate, re-commits obligations idempotently, THEN acks.
    monkeypatch.undo()
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(work)
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == work.id))).mappings().one()
        assert len((await conn.execute(sa.select(MESSAGES))).all()) == 1
    assert row["state"] == "done"
    assert await _pending(redis_client, stream) == 0


async def test_turn_reclaim_after_started_action_is_uncertain_not_replayed(
        bridge, redis_client, harness):
    """A turn whose first attempt STARTED an action must not re-run the model:
    reclaim marks it `uncertain` and the engagement pipeline is never re-entered."""
    store, _, stream = bridge
    env = _envelope("nora reply please", 1005)
    await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    runner = _runner(store, redis_client, harness)
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(work)  # ingest → engages → turn obligation

    turn = await store.claim_next(owner="w-test", lease_seconds=60)
    assert turn is not None and turn.kind == "turn"
    # Simulate a first attempt that STARTED a send then died: the action row
    # is the durable witness. A second attempt must fence, not replay.
    owner = f"w-{turn.id}"
    async with store._engine.begin() as conn:
        await store.reserve_action(
            conn, action_id=f"{owner}:send:0", platform=PLATFORM,
            account_key=ACCOUNT, owner_kind="work", owner_id=owner,
            owner_generation=turn.generation, op="send",
            request={"text": "partial"}, message_id=None)
        await store.action_state(conn, f"{owner}:send:0", "started")
    calls = []
    real_durable = harness._engagement.run_durable

    async def spy(*a, **kw):
        calls.append(1)
        return await real_durable(*a, **kw)

    harness._engagement.run_durable = spy
    await runner._execute(turn)
    assert calls == []  # never re-entered
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == turn.id))).mappings().one()
    assert row["state"] == "uncertain"
    assert harness.sink.sent == []  # no blind resend


async def test_turn_runs_admission_persisted_before_effects(
        bridge, redis_client, harness_factory):
    """run_durable persists the admission verdict inside the work row BEFORE
    any send — a reclaimed attempt reuses it instead of re-debouncing."""
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"text": "bridge reply"})],
        final_text="bridge reply")])
    store, _, stream = bridge
    env = _envelope("nora say something", 1006)
    await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    runner = _runner(store, redis_client, h)
    await runner._execute(await store.claim_next(owner="w-test", lease_seconds=60))

    turn = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(turn)
    await h.drain()
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == turn.id))).mappings().one()
    assert row["state"] == "done"
    assert (row["result"] or {}).get("admission", {}).get("verdict") \
        in ("RUN_NOW", "QUEUED", "ACCEPTED")
    # The send really went through the action path, not around it.
    assert h.sink.texts == ["bridge reply"]


async def test_concurrent_claims_skip_locked(bridge, redis_client, harness):
    """Two claimants must not take the same row: SKIP LOCKED is the fencing."""
    store, _, stream = bridge
    for i in range(3):
        await _produce(redis_client, stream, _envelope(f"m{i}", 2000 + i))
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        for _ in range(150):
            async with store._engine.connect() as conn:
                n = len((await conn.execute(
                    sa.select(BRIDGE_WORK))).all())
            if n == 3:
                break
            await asyncio.sleep(0.1)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    a = await store.claim_next(owner="a", lease_seconds=60)
    b = await store.claim_next(owner="b", lease_seconds=60)
    assert a is not None and b is not None and a.id != b.id


class _CommitGateStore(SqlBridgeStore):
    """Named failure injection for the claim commit window: runs the REAL
    `claim_next` — the row genuinely commits `running` — then suspends the
    receipt on `gate`. A cancel delivered while the gate is held is the
    state a kill inside the commit leaves: claim landed, receipt never
    delivered to the caller."""

    def __init__(self, engine, *, gate: asyncio.Event) -> None:
        super().__init__(engine)
        self._gate = gate

    async def claim_next(self, **kw):  # type: ignore[no-untyped-def]
        work = await super().claim_next(**kw)
        if work is not None:
            await self._gate.wait()
        return work


async def test_claim_cancelled_while_receipt_in_flight_reparks_committed_row(
        bridge, redis_client, harness, pg_url):
    """A cancel inside the claim commit used to orphan the row `running`
    under a dead owner until lease expiry: `work` bound only AFTER the
    await returned, so the receipt was unreachable. The claim must instead
    be shielded and its receipt awaited, then reparked."""
    store, _, _ = bridge
    async with store._engine.begin() as conn:
        await store.enqueue_work(
            conn, event_id=None, kind="ingest", ordinal=0,
            platform=PLATFORM, account_key=ACCOUNT, conversation_id=CONV,
            payload={"stream": stream_name(PLATFORM, ACCOUNT),
                     "stream_id": "1-1"})

    gate = asyncio.Event()
    gated = _CommitGateStore(create_async_engine(pg_url), gate=gate)
    try:
        runner = _runner(gated, redis_client, harness)
        task = asyncio.ensure_future(
            runner._claim_next(frozenset({"ingest"})))
        # Hold until the claim COMMITTED — `running` in the database, the
        # receipt suspended on the gate.
        for _ in range(150):
            async with gated._engine.connect() as conn:
                row = (await conn.execute(sa.select(BRIDGE_WORK))).one()
            if row.state == "running":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("claim never committed")
        task.cancel()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with gated._engine.connect() as conn:
            row = (await conn.execute(sa.select(BRIDGE_WORK))).one()
        # The committed claim was reparked — not orphaned `running` under a
        # dead owner until lease expiry.
        assert row.state == "pending" and row.lease_owner is None
    finally:
        await gated._engine.dispose()


async def test_receipt_is_bridge_owned_no_app_obligation(
        bridge, redis_client, harness):
    """An envelope carrying `receipt` produces NO app-side receipt
    obligation: the queued ack is emitted by the bridge's producer once per
    canonical event. The app enqueues nothing — a second receipt workflow
    would double-send across retries and key rotation."""
    store, _, stream = bridge
    env = _envelope("with receipt", 1007, receipt={"emoji": "👍"})
    await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, env.event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    runner = _runner(store, redis_client, harness)
    await runner._execute(await store.claim_next(owner="w-test", lease_seconds=60))
    async with store._engine.connect() as conn:
        kinds = [r.kind for r in
                 (await conn.execute(sa.select(BRIDGE_WORK))).all()]
    assert "receipt" not in kinds  # bridge owns it; the app never queues one


async def test_reserve_action_one_execute_under_concurrency(bridge):
    """Two simultaneous callers on one action_id: exactly ONE gets 'execute'
    (prepared→started CAS), the other gets 'replay' — never two effects."""
    store, _, _ = bridge
    req = {"op": "send", "target": {"id": "g-bridge"}, "text": "once"}

    async def attempt(owner_id: str, generation: int):
        async with store._engine.begin() as conn:
            return await store.reserve_action(
                conn, action_id="a-race", platform=PLATFORM,
                account_key=ACCOUNT, owner_kind="work", owner_id=owner_id,
                owner_generation=generation, op="send", request=req)

    r1, r2 = await asyncio.gather(attempt("w-1", 1), attempt("w-1", 1))
    verdicts = sorted(v for v, _ in (r1, r2))
    assert verdicts == ["execute", "replay"], (r1, r2)


async def test_reserve_action_rejects_stale_generation_and_foreign_owner(
        bridge):
    store, _, _ = bridge
    req = {"op": "send", "target": {"id": "g-bridge"}, "text": "once"}
    async with store._engine.begin() as conn:
        verdict, _ = await store.reserve_action(
            conn, action_id="a-fence", platform=PLATFORM,
            account_key=ACCOUNT, owner_kind="work", owner_id="w-9",
            owner_generation=1, op="send", request=req)
    assert verdict == "execute"
    async with store._engine.begin() as conn:
        # A reclaimed work (generation bump) re-reserving the SAME action_id:
        # "replay", never a second execute — the effect is already started.
        verdict, row = await store.reserve_action(
            conn, action_id="a-fence", platform=PLATFORM,
            account_key=ACCOUNT, owner_kind="work", owner_id="w-9",
            owner_generation=2, op="send", request=req)
    assert verdict == "replay" and row["state"] == "started"
    async with store._engine.begin() as conn:
        # A foreign owner asking for the same action_id is a conflict.
        verdict, _ = await store.reserve_action(
            conn, action_id="a-fence", platform=PLATFORM,
            account_key=ACCOUNT, owner_kind="otp_challenge", owner_id="o-1",
            owner_generation=None, op="send", request=req)
    assert verdict == "conflict"
