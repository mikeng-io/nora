"""Runtime wiring of the queue telemetry contract — the actual public
`StreamReader` and `WorkRunner` classes against disposable Redis+Postgres
and the disposable collector fixture (test_bridge_observability's).

Nothing here manufactures a state transition by hand to prove the runner ran:
every claim, reclaim, finish and ACK goes through the real classes. The only
fakes are at the already-provided `nora.testing` model/send seams and, where a
test needs one, an injected dead/exploding dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import urllib.request
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.app.bridge_queue import (
    BridgeRuntime,
    DbSpeakingLocks,
    StreamReader,
    WorkRunner,
    stream_name,
)
from nora.domain.models import NormalizedMessage
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.schema import BRIDGE_WORK, MESSAGES
from nora.obs.metrics import Metrics
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.envelope import (
    Envelope,
    canonical_source_key,
    event_id,
)
from nora.platform.bridge.inspection import QueueInspector
from nora.platform.bridge.queue import (
    CONSUMER_GROUP,
    StreamProducer,
    ensure_group,
)
from nora.platform.bridge.rehydrate import message_to_dict
from nora.testing import FakeAgentRunner, ScriptedTurn
from tests.integration.test_bridge_observability import (
    _jaeger_trace,
    _sink_spans,
    otel_stack,  # noqa: F401 — imported fixture
)
from tests.integration.test_bridge_observability import (
    otlp as otlp,
)

pytestmark = pytest.mark.integration

KEY = b"telemetry-correlation-key"
PLATFORM, ACCOUNT, CONV = "signal", "+00000000009", "g-telemetry"
JAEGER = "http://localhost:16686"


def _msg(body: str, ts: int, conv: str = CONV) -> dict:
    return message_to_dict(NormalizedMessage(
        platform=PLATFORM, conversation_id=conv, conversation_type="group",
        sender_id="+00000000008", sender_name="Sender", timestamp=ts,
        body=body, raw={"dataMessage": {"message": body}},
        message_id=f"t{ts}"))


def _envelope(body: str, ts: int, *, trace: dict | None = None,
              conv: str = CONV) -> Envelope:
    sk = canonical_source_key(PLATFORM, ACCOUNT, conv, "message", f"{ts}")
    return Envelope(
        platform=PLATFORM, account_key=ACCOUNT, kind="message",
        source_key=sk, event_id=event_id(sk, key=KEY, key_version=1),
        conversation_id=conv, conversation_type="group",
        sender_id="+00000000008", sender_name="Sender", sent_ms=ts,
        received_ms=ts + 1, queued_ms=ts + 2, time_quality="upstream",
        message=_msg(body, ts, conv), trace=trace or {}, admission_rev=1)


@pytest.fixture
async def bridge(pg_url, redis_client):
    store = SqlBridgeStore(create_async_engine(pg_url))
    stream = stream_name(PLATFORM, ACCOUNT)
    await ensure_group(redis_client, stream)
    yield store, redis_client, stream
    await store._engine.dispose()


def _reader(redis_client, store, metrics=None) -> StreamReader:
    return StreamReader(platform=PLATFORM, account_key=ACCOUNT,
                        redis=redis_client, store=store, metrics=metrics)


def _runner(store, redis_client, harness, **kw) -> WorkRunner:
    # `instance="w-test"` aligns the runner's claim/renew owner with the
    # `claim_next(owner="w-test")` calls below — production `run()` claims as
    # `self._owner`, and the renewal fence requires the same tuple.
    return WorkRunner(
        store=store, redis=redis_client,
        ingest=harness.handler._dispatcher._ingest,
        engagement=harness._engagement,
        slots=harness._engagement._slots, db_locks=DbSpeakingLocks(store),
        adapters={PLATFORM: harness.sink}, gate=None,
        metrics=harness.metrics, instance="w-test", **kw)


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


async def _read_once(reader: StreamReader, store, event_id: str) -> None:
    """Drive the reader's own `_read` until this event is registered."""
    task = asyncio.create_task(reader._read())
    try:
        await _wait_event(store, event_id)
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _work_row(store, work_id: int):
    async with store._engine.connect() as conn:
        return (await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == work_id))).mappings().one()


def _refs(span: dict) -> set[str]:
    """The trace_ids a Jaeger span's references point at (FOLLOWS_FROM —
    the map Jaeger makes of an OTel Link)."""
    out: set[str] = set()
    for ref in span.get("references") or []:
        if ref.get("traceID"):
            out.add(ref["traceID"])
    return out


def _jaeger_search(operation: str, timeout: float = 15.0) -> list[dict]:
    """Every span with this operation name the service exported — polled,
    since Jaeger indexes asynchronously."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            url = (f"{JAEGER}/api/traces?service="
                   "nora-bridge-observability-test&limit=200")
            data = json.loads(urllib.request.urlopen(  # noqa: S310
                url, timeout=5).read())["data"]
            spans = [s for t in data for s in t["spans"]
                     if s["operationName"] == operation]
            if spans:
                return spans
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() > deadline:
            raise AssertionError(f"no {operation!r} span in Jaeger")
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# The required replacement scenario: consumer down → replace → phase A killed
# → reclaim → attempt B links producer + previous attempt → complete + ACK.
# ---------------------------------------------------------------------------

async def test_replacement_chain_links_and_bounded_lifetimes(
        bridge, redis_client, harness_factory, otlp, caplog):
    store, _, stream = bridge
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"text": "replacement reply"})],
        final_text="replacement reply")])

    # 1. Enqueue while the consumer is DOWN — a real producer span's carrier
    #    rides the envelope exactly as the bridge stamps it.
    with bt.producer_span(PLATFORM, event_id="capture") as carrier:
        pass
    env = _envelope("nora please reply", 2001,
                    trace={k: carrier[k] for k in ("traceparent", "tracestate")
                           if carrier.get(k)})
    await _produce(redis_client, stream, env)
    producer_trace = carrier["trace_id"]
    assert await _pending(redis_client, stream) == 0  # waiting, not pending

    # 2. Replacement consumer: reader registers (claim root linked to the
    #    producer), then the runner executes the ingest attempt.
    caplog.set_level(logging.INFO, "nora.platform.bridge.telemetry")
    reader = _reader(redis_client, store, metrics=h.metrics)
    await _read_once(reader, store, env.event_id)
    runner = _runner(store, redis_client, h)
    ingest = await store.claim_next(owner="w-test", lease_seconds=60)
    assert ingest is not None and ingest.kind == "ingest"
    await runner._execute(ingest)
    assert await _pending(redis_client, stream) == 0  # acked after commit

    # 3. The turn's first attempt dies mid-phase — a REAL cancel the runner
    #    reparks, leaving only the started attempt record on the row.
    turn_a = await store.claim_next(owner="w-test", lease_seconds=60)
    assert turn_a is not None and turn_a.kind == "turn"
    h.set_script(FakeAgentRunner(
        [ScriptedTurn(tool_calls=[("send_reply", {"text": "x"})],
                      final_text="x")],
        delay=30.0, on_call=h._calls.append))
    killed = asyncio.ensure_future(runner._execute(turn_a))
    await asyncio.sleep(0.8)  # inside the scripted model delay
    killed.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await killed
    a_rec = (await _work_row(store, turn_a.id))["result"][
        "telemetry_last_attempt"]
    assert a_rec["attempt_id"].endswith(f":{turn_a.generation}")
    assert "finished_ms" not in a_rec  # died mid-phase — start only

    # 4. Reclaim + second attempt — linked to producer AND previous attempt,
    #    finishes durably; the model turn is a SEPARATE generation trace
    #    linked back to attempt B.
    turn_b = await store.claim_next(owner="w-test", lease_seconds=60)
    assert turn_b is not None and turn_b.generation > turn_a.generation
    h.set_script([ScriptedTurn(
        tool_calls=[("send_reply", {"text": "replacement reply"})],
        final_text="replacement reply")])
    await runner._execute(turn_b)
    await h.drain()
    row = await _work_row(store, turn_b.id)
    assert row["state"] == "done"
    assert h.sink.texts == ["replacement reply"]  # sent exactly once
    b_rec = row["result"]["telemetry_last_attempt"]
    assert "finished_ms" in b_rec and b_rec["outcome"] == "done"

    # ---- Jaeger readback, not local exporter counts -----------------------
    assert next(s for s in _jaeger_search("queue.claim")
                if producer_trace in _refs(s))

    a_doc = _jaeger_trace(a_rec["trace_id"])
    assert len(a_doc["spans"]) == 1  # no model child under a queue trace
    a_span = a_doc["spans"][0]
    assert a_span["operationName"] == "queue.attempt"
    assert producer_trace in _refs(a_span)

    b_doc = _jaeger_trace(b_rec["trace_id"])
    assert len(b_doc["spans"]) == 1
    b_span = b_doc["spans"][0]
    assert producer_trace in _refs(b_span)
    assert a_rec["trace_id"] in _refs(b_span)  # previous attempt linked

    # Bounded lifetime: attempt B's span covers the attempt — far below the
    # 30s scripted delay attempt A was cancelled inside.
    assert b_span["duration"] < 10_000_000  # µs

    gen = next(s for s in _jaeger_search("engagement")
               if b_rec["trace_id"] in _refs(s))
    assert gen["traceID"] != b_rec["trace_id"]
    assert {s["traceID"] for s in _jaeger_trace(gen["traceID"])["spans"]} \
        == {gen["traceID"]}
    # The queue kind never reaches Langfuse — the sink stays empty of it for
    # all of the above (the generation trace is the only leg that may arrive).
    sink_spans = await asyncio.to_thread(_sink_spans)
    queue_spans_in_sink = [
        s for s in sink_spans
        if {t["key"]: t["value"].get("stringValue") for t in
            s.get("attributes", [])}.get("nora.trace_kind") == "queue"]
    assert queue_spans_in_sink == []

    # ---- the safe JSON lifecycle record -----------------------------------
    events = [r for r in caplog.records
              if getattr(r, "queue_event", None)]
    names = {r.queue_event for r in events}
    assert {"claimed", "attempt_started", "completed", "acked"} <= names
    for r in events:
        assert r.platform == PLATFORM
        for banned in ("body", "sender_id", "account_key", "source_key",
                       "conversation_id"):
            assert banned not in r.__dict__


# ---------------------------------------------------------------------------
# Lease/liveness controls
# ---------------------------------------------------------------------------

async def test_lease_renews_through_a_long_turn(
        bridge, redis_client, harness_factory):
    """A REAL 1-second lease survives a 3s turn: `leased_until` read back
    from the row advances on every heartbeat tick, repeated `expire_leases`
    mid-phase steal nothing, and a sibling row claimed with NO heartbeat
    DOES expire — the positive control that proves the scanner works."""
    h = await harness_factory(
        delay=3.0, script=[ScriptedTurn(
            tool_calls=[("send_reply", {"text": "long turn"})],
            final_text="long turn")])
    store, _, stream = bridge
    env = _envelope("nora take your time", 2002)
    await _produce(redis_client, stream, env)
    await _read_once(_reader(redis_client, store), store, env.event_id)
    runner = _runner(store, redis_client, h, lease_seconds=1)
    await runner._execute(await store.claim_next(
        owner="w-test", lease_seconds=1))

    # Positive control: a second event's ingest in ANOTHER conversation,
    # claimed (via the conversation filter — the turn row is earlier due)
    # and NEVER executed — its 1s lease must actually lapse.
    env2 = _envelope("control row expires", 2006, conv="g-ctl")
    await _produce(redis_client, stream, env2)
    await _read_once(_reader(redis_client, store), store, env2.event_id)
    control = await store.claim_next(
        owner="w-dead", lease_seconds=1, conversation_id="g-ctl")
    assert control is not None and control.kind == "ingest"

    turn = await store.claim_next(owner="w-test", lease_seconds=1)
    assert turn is not None and turn.kind == "turn"
    phase = asyncio.ensure_future(runner._execute(turn))
    # Read the row's OWN columns back — no spy on the runner's internals.
    leased: list[Any] = []
    states: list[str] = []
    for _ in range(5):
        await asyncio.sleep(0.45)          # heartbeat ticks at lease/4=0.25s
        row = await _work_row(store, turn.id)
        leased.append(row["leased_until"])
        states.append(row["state"])
        await store.expire_leases()        # the scanner, mid-phase
        if (await _work_row(store, control.id))["state"] == "pending":
            break                          # control expired: enough ticks
    assert states and all(s == "running" for s in states)
    assert len({str(t) for t in leased}) >= 2  # leased_until ADVANCED
    # The un-renewed control row expired; the renewed turn never did.
    assert (await _work_row(store, control.id))["state"] == "pending"
    assert (await _work_row(store, turn.id))["state"] == "running"
    await phase
    await h.drain()
    assert (await _work_row(store, turn.id))["state"] == "done"
    assert h.sink.texts == ["long turn"]


async def _wait_calls(h, n: int, polls: int = 100) -> None:
    for _ in range(polls):
        if h.agents_constructed >= n:
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"model calls {h.agents_constructed} < {n}")


async def _ingest_states(store) -> dict[int, str]:
    async with store._engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            BRIDGE_WORK.c.id, BRIDGE_WORK.c.state).where(
            BRIDGE_WORK.c.kind == "ingest"))).all()
    return {r[0]: r[1] for r in rows}


async def test_lanes_ingest_progresses_while_turn_suspended(
        bridge, redis_client, harness_factory):
    """Design 03's rule executed on the real runner: turn A suspends inside
    the model call on a speak worker; ingest B for the SAME conversation
    still commits its audit row through the independent comprehend lane,
    and a second conversation's turn reaches the model on the other speak
    worker. A stays the only speaker — B's turn is reparked by the DB lock,
    never run, and nothing sends twice."""
    h = await harness_factory(delay=30.0, script=[ScriptedTurn(
        tool_calls=[("send_reply", {"text": "slow A"})],
        final_text="slow A")])
    store, _, stream = bridge
    envs = [_envelope("nora slow A", 2010),
            _envelope("nora later B", 2011),
            _envelope("nora other room", 2012, conv="g-other")]
    for env in envs:
        await _produce(redis_client, stream, env)
        await _read_once(_reader(redis_client, store), store, env.event_id)
    runner = _runner(store, redis_client, h)
    running = asyncio.ensure_future(runner.run())
    try:
        await _wait_calls(h, 1)            # turn A inside the 30s model call
        # B's ingest must commit while A is still suspended — poll until the
        # audit row lands, all under the same suspension window.
        for _ in range(100):
            states = await _ingest_states(store)
            if len(states) == 3 and set(states.values()) == {"done"}:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError(f"ingests stalled behind the turn: {states}")
        async with store._engine.connect() as conn:
            assert len((await conn.execute(sa.select(MESSAGES))).all()) == 3
        assert h.sink.texts == []          # A never finished: nothing sent
        # The other conversation's turn reaches the model on the second
        # speak worker — lane independence in both directions.
        await _wait_calls(h, 2)
        await asyncio.sleep(0.4)           # give a wrongly-ordered turn B time
        assert h.agents_constructed == 2   # conv1's second turn never ran
        assert h.sink.texts == []
    finally:
        runner.stop()
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running
    # Cancelled mid-model: every turn row reparks, none completes, nothing
    # sent — exactly-once survives the process "dying" at the worst moment.
    async with store._engine.connect() as conn:
        turns = (await conn.execute(sa.select(
            BRIDGE_WORK.c.id, BRIDGE_WORK.c.state, BRIDGE_WORK.c.lease_owner,
            BRIDGE_WORK.c.leased_until, BRIDGE_WORK.c.generation,
            BRIDGE_WORK.c.attempts, BRIDGE_WORK.c.conversation_id,
            BRIDGE_WORK.c.event_id).where(
            BRIDGE_WORK.c.kind == "turn"))).all()
    assert {r[1] for r in turns} <= {"pending", "retryable_failure"}, \
        f"turn rows left non-pending: {turns}"
    assert h.sink.texts == []


async def test_killed_lock_backend_halts_turn_and_reclaims(
        bridge, redis_client, harness_factory):
    """The held-session check, for real: `pg_terminate_backend` on the exact
    backend holding the speaking lock kills the session mid-phase — the
    heartbeat's pg_locks probe sees it, the phase unwinds before any effect,
    and only lease expiry + reclaim (never the stale attempt) lets a later
    generation proceed."""
    h = await harness_factory(delay=15.0, script=[ScriptedTurn(
        tool_calls=[("send_reply", {"text": "must not send"})],
        final_text="must not send")])
    store, _, stream = bridge
    env = _envelope("nora kill my backend", 2020)
    await _produce(redis_client, stream, env)
    await _read_once(_reader(redis_client, store), store, env.event_id)
    runner = _runner(store, redis_client, h, lease_seconds=1)
    await runner._execute(await store.claim_next(
        owner="w-test", lease_seconds=1))
    turn = await store.claim_next(owner="w-test", lease_seconds=1)
    assert turn is not None and turn.kind == "turn"
    phase = asyncio.ensure_future(runner._execute(turn))
    await _wait_calls(h, 1)                # inside the model — locks held

    # Positive control: a second locker cannot steal while the LIVE session
    # holds it — lease expiry alone never unlocks speaking.
    assert not await DbSpeakingLocks(store).try_acquire((PLATFORM, CONV))

    async with store._engine.connect() as conn:
        pid = (await conn.execute(sa.text(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory'"
            " AND classid = hashtext(:ns)::oid"
            " AND objid = hashtext(:k)::oid"),
            {"ns": "nora-bridge-speak", "k": f"{PLATFORM}:{CONV}"})
        ).scalar_one()
        await conn.execute(sa.text("SELECT pg_terminate_backend(:p)"),
                           {"p": pid})

    # Heartbeat ticks at lease/4 = 0.25s — the phase unwinds on its own.
    await asyncio.wait_for(phase, timeout=8)
    assert h.sink.texts == []              # cancelled before any effect
    row = await _work_row(store, turn.id)
    # The killed attempt resolves NOTHING: the repark bumps the generation
    # the unwind's late finish would fence on, so the row lands pending —
    # claimable by a later generation, and nothing says the turn ran.
    assert row["state"] == "pending"
    assert row["generation"] == turn.generation + 1
    assert row["lease_owner"] is None
    async with store._engine.connect() as conn:
        assert (await conn.execute(sa.text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
            " AND classid = hashtext(:ns)::oid"
            " AND objid = hashtext(:k)::oid"),
            {"ns": "nora-bridge-speak", "k": f"{PLATFORM}:{CONV}"})
        ).scalar_one() == 0                # the lock died with the backend

    # The repark already freed the row (the stale generation can never
    # finish it) — a later claim under a NEW generation runs the turn.
    await asyncio.sleep(1.3)
    await store.expire_leases()
    h.set_script(FakeAgentRunner([ScriptedTurn(
        tool_calls=[("send_reply", {"text": "recovered"})],
        final_text="recovered")], on_call=h._calls.append))
    live = await store.claim_next(owner="w-test", lease_seconds=30)
    assert live is not None and live.generation > turn.generation
    await runner._execute(live)
    assert (await _work_row(store, live.id))["state"] == "done"
    assert h.sink.texts == ["recovered"]   # exactly one send, on retry


async def test_stale_generation_attempt_fences_without_effects(
        bridge, redis_client, harness):
    """The SAME work row reclaimed to generation N+1 fences the stale gen-N
    attempt at its record write — the old attempt runs nothing, and the row
    under the live generation is untouched."""
    store, _, stream = bridge
    env = _envelope("stale attempt must not run", 2003)
    await _produce(redis_client, stream, env)
    await _read_once(_reader(redis_client, store), store, env.event_id)
    runner = _runner(store, redis_client, harness)
    stale = await store.claim_next(owner="w-dead", lease_seconds=60)
    assert stale is not None

    # The "dead" owner's row is expired and re-claimed under a new generation.
    async with store._engine.begin() as conn:
        await conn.execute(sa.text(
            "UPDATE bridge_work SET leased_until = now() - interval '1s'"
            " WHERE id = :id"), {"id": stale.id})
    await store.expire_leases()
    live = await store.claim_next(owner="w-live", lease_seconds=60)
    assert live is not None and live.generation > stale.generation

    before = harness.agents_constructed
    await runner._execute(stale)   # fenced at record_attempt_started
    assert harness.agents_constructed == before  # no model, no phase
    row = await _work_row(store, stale.id)
    assert row["state"] == "running" and row["generation"] == live.generation

    # The live attempt runs the real phase and finishes.
    await runner._execute(live)
    row = await _work_row(store, live.id)
    assert row["state"] == "done"
    assert row["result"]["telemetry_last_attempt"]["attempt_id"].endswith(
        f":{live.generation}")


# ---------------------------------------------------------------------------
# ACK ordering and lost-ACK recovery
# ---------------------------------------------------------------------------

async def test_lost_ack_keeps_pending_and_dedupes_reclaim(
        bridge, redis_client, harness_factory, monkeypatch):
    """XACK failure after the durable commit: state stays `done`, the entry
    stays pending, and redelivery registers as a duplicate — the ack lands
    on reclaim without re-ingesting."""
    h = await harness_factory(script=[ScriptedTurn(
        tool_calls=[("send_reply", {"text": "still once"})],
        final_text="still once")])
    store, _, stream = bridge
    env = _envelope("nora ack me twice", 2004)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, metrics=h.metrics)
    await _read_once(reader, store, env.event_id)

    runner = _runner(store, redis_client, h)
    real_xack = redis_client.xack
    calls = []

    async def fail_once(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("redis dropped")
        return await real_xack(*a, **kw)

    monkeypatch.setattr(redis_client, "xack", fail_once)
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(work)
    # done is durable despite the lost ACK — the downstream turn obligation
    # stays nonterminal, which is the point of the durable handoff.
    assert (await _work_row(store, work.id))["state"] == "done"
    assert await _pending(redis_client, stream) == 1

    # The turn obligation drains the same way — the entry is still pending
    # because the redelivery check (not the turn) owns this event's ACK.
    turn = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(turn)
    await h.drain()
    assert (await _work_row(store, turn.id))["state"] == "done"
    assert h.sink.texts == ["still once"]

    # Reclaim redelivers: duplicate suppression sees the event's work is all
    # terminal, so the reader's own ACK path discharges it — no re-ingest.
    monkeypatch.undo()
    await reader._register(sid, {"e": json.dumps(env.to_fields())},
                           reclaimed=True)
    assert await _pending(redis_client, stream) == 0
    async with store._engine.connect() as conn:
        inbound = (await conn.execute(sa.select(MESSAGES).where(
            MESSAGES.c.is_nora == 0))).all()
    assert len(inbound) == 1  # the redelivery never re-ingested
    assert h.sink.texts == ["still once"]  # never re-sent


async def test_collector_outage_leaves_work_and_ack_identical(
        bridge, redis_client, harness, monkeypatch):
    """An exporter that cannot reach its collector changes nothing: the same
    claim→execute→commit→ack sequence completes untraced."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from nora.obs import tracing as _tracing

    provider = TracerProvider(
        sampler=_tracing.build_sampler(),
        resource=Resource.create({"service.name": "outage"}))
    provider.add_span_processor(SimpleSpanProcessor(
        OTLPSpanExporter(endpoint="http://localhost:1/v1/traces")))
    monkeypatch.setattr(_tracing.trace, "get_tracer", provider.get_tracer)
    store, _, stream = bridge
    env = _envelope("outage is silent", 2005)
    await _produce(redis_client, stream, env)
    await _read_once(_reader(redis_client, store), store, env.event_id)
    runner = _runner(store, redis_client, harness)
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(work)
    assert (await _work_row(store, work.id))["state"] == "done"
    assert await _pending(redis_client, stream) == 0
    provider.shutdown()


# ---------------------------------------------------------------------------
# Inspection exposure — the runtime surface composition mounts
# ---------------------------------------------------------------------------

async def test_runtime_exposes_bounded_inspection(
        bridge, redis_client, harness):
    """`BridgeRuntime` carries the inspector and serves the bounded
    snapshot — real counts while obligations exist, honest zeroes after."""
    store, _, stream = bridge
    env = _envelope("inspect me", 2006)
    await _produce(redis_client, stream, env)
    metrics = Metrics()
    reader = _reader(redis_client, store, metrics=metrics)
    runner = _runner(store, redis_client, harness)
    inspector = QueueInspector(redis_client, engine=store._engine,
                               metrics=metrics)
    runtime = BridgeRuntime(redis=redis_client, store=store,
                            readers=[reader], runner=runner,
                            inspector=inspector)

    snapshot = await runtime.queue_snapshot()
    assert snapshot[stream]["status"] == "ok"
    assert snapshot[stream]["waiting_entries"] == 1

    await _read_once(reader, store, env.event_id)
    work = await store.claim_next(owner="w-test", lease_seconds=60)
    await runner._execute(work)

    snapshot = await runtime.queue_snapshot()
    assert snapshot[stream]["waiting_entries"] == 0
    assert snapshot[stream]["pending_entries"] == 0


async def test_failed_stream_inspection_is_unavailable_not_zero(
        bridge, redis_client):
    """A stream that errors mid-inspection: the section stays visible, the
    aggregate is `unavailable`, and NO backlog gauge is published."""
    store, _, stream = bridge
    await _produce(redis_client, stream, _envelope("kept", 2007))
    metrics = Metrics()
    inspector = QueueInspector(redis_client, engine=store._engine,
                               metrics=metrics)
    result = await inspector.inspect(
        [stream, "nq:in:signal:never-created"], platform=PLATFORM)
    assert result.status == "ok"
    assert result.waiting_entries == 1

    class BrokenRedis:
        async def xinfo_groups(self, *a, **kw):
            raise ConnectionError("gone")

        async def xrange(self, *a, **kw):
            raise ConnectionError("gone")

        async def xpending(self, *a, **kw):
            raise ConnectionError("gone")

        async def xpending_range(self, *a, **kw):
            raise ConnectionError("gone")

        async def time(self):
            raise ConnectionError("gone")

    degraded = QueueInspector(BrokenRedis(), engine=store._engine,
                              metrics=metrics)
    out = await degraded.inspect([stream], platform=PLATFORM)
    assert out.status == "unavailable"
    assert out.waiting_entries is None and out.pending_entries is None
    assert out.streams[0].error == "ConnectionError"
