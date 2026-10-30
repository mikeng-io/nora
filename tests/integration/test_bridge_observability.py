"""Bridge queue observability — the wired half: real Redis, real Postgres,
the disposable collector fixture, read back FROM Jaeger and the synthetic
Langfuse sink.

What this file deliberately does NOT claim: the queue runtime callsites
(`StreamProducer.enqueue`, the reader/claim loop, `WorkRunner`, the action
seam) belong to the sibling implementation and are wired by the parent lead
after this patch lands. Here the LIFECYCLE is driven at the same Redis and
Postgres primitives those callsites will use — XADD / XREADGROUP / XAUTOCLAIM
/ XACK and the `bridge_work` / `bridge_action` columns — so every helper is
proven against the real wire without pretending the app already emits it.

`redis` is not a dependency of THIS worktree — it arrives with the sibling's
queue runtime and `pyproject.toml` is outside this patch's allowed paths —
so `_MiniRedis` below is a minimal RESP client returning the redis-py
response shapes the inspector's injected client documents. It talks the real
wire protocol to a real server: every semantic under test (last-delivered,
the PEL, XAUTOCLAIM) is the server's own, not a fake's answer.
`test_real_redis_client_shapes_the_contract` runs the same inspector against
the REAL `redis.asyncio` client (importorskip; `uv run --with redis`) to
prove the shapes match.

Same for the DDL: `0141_bridge_inbox` is the sibling's migration, so the
`bridge_work`/`bridge_action` tables are created here with the columns the
queries actually touch — the real types and fenced-update semantics, without
the FK machinery nothing under test reads.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import sqlalchemy as sa
from opentelemetry.trace import Link

from nora.obs.metrics import Metrics
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.inspection import CONSUMER_GROUP, QueueInspector

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "deploy" / "otel-queue-fixture"
SERVICE = "nora-bridge-observability-test"
JAEGER = "http://localhost:16686"
SINK = "http://localhost:5318"
PLATFORM = "signal"
STREAM = "nq:in:signal:synthetic-obs-test"


# ---------------------------------------------------------------------------
# The disposable collector fixture + an OTLP provider under the REAL sampler
# ---------------------------------------------------------------------------

def _http(url: str, data: bytes | None = None,
          ctype: str = "application/json") -> bytes:
    req = urllib.request.Request(  # noqa: S310 — fixture-local URLs only
        url, data=data, headers={"Content-Type": ctype} if data else {})
    return urllib.request.urlopen(req, timeout=5).read()  # noqa: S310 — fixture-local


def _wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            _http(url)
            return
        except (OSError, ValueError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.25)


def _wait_tcp(port: int, timeout: float = 30.0) -> None:
    import socket
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.25)


def _compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — a fixed argv, never user input
        ["docker", "compose", "-f", str(FIXTURE / "docker-compose.yml"),
         *args], capture_output=True, text=True)


@pytest.fixture(scope="module")
def otel_stack():
    up = _compose("up", "-d", "--build")
    assert up.returncode == 0, up.stderr
    _wait_http(f"{SINK}/healthz")
    _wait_http(f"{JAEGER}/")
    _wait_tcp(4318)
    yield
    _compose("down", "-v")


@pytest.fixture
def otlp(otel_stack, monkeypatch):
    """A provider exporting to the fixture under `build_sampler()` — the
    production rule, never AlwaysOn (tests/conftest.py's exported_spans
    docstring is why)."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from nora.obs import tracing as _tracing

    provider = TracerProvider(
        sampler=_tracing.build_sampler(),
        resource=Resource.create({"service.name": SERVICE}))
    provider.add_span_processor(SimpleSpanProcessor(
        OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces")))
    monkeypatch.setattr(_tracing.trace, "get_tracer", provider.get_tracer)
    yield provider
    provider.shutdown()


def _jaeger_trace(trace_id: str, timeout: float = 15.0) -> dict:
    """One trace document from Jaeger's own API — the readback that counts."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            data = json.loads(
                _http(f"{JAEGER}/api/traces/{trace_id}"))["data"]
            if data:
                return data[0]
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() > deadline:
            raise AssertionError(f"trace {trace_id} not in Jaeger")
        time.sleep(0.3)


def _sink_spans() -> list[dict]:
    return json.loads(_http(f"{SINK}/spans"))["spans"]


def _trace_id_of(span) -> str:
    return f"{span.get_span_context().trace_id:032x}"


# ---------------------------------------------------------------------------
# A minimal RESP client — the redis-py SHAPES on the real wire
# ---------------------------------------------------------------------------

class _RespError(Exception):
    pass


def _enc_cmd(args: list) -> bytes:
    out = [f"*{len(args)}\r\n".encode()]
    for a in args:
        b = a if isinstance(a, bytes) else str(a).encode()
        out += [f"${len(b)}\r\n".encode(), b, b"\r\n"]
    return b"".join(out)


async def _read_reply(reader: asyncio.StreamReader):
    line = await reader.readline()
    tag, rest = line[:1], line[1:-2]
    if tag == b"+":
        return rest
    if tag == b"-":
        raise _RespError(rest.decode())
    if tag == b":":
        return int(rest)
    if tag == b"$":
        n = int(rest)
        if n == -1:
            return None
        return (await reader.readexactly(n + 2))[:-2]
    if tag == b"*":
        n = int(rest)
        return None if n == -1 else [
            await _read_reply(reader) for _ in range(n)]
    raise _RespError(f"unknown reply tag {tag!r}")


def _d(v):
    return v.decode() if isinstance(v, (bytes, bytearray)) else v


def _pairs(raw: list) -> dict:
    """A flat k,v RESP array → a str dict (redis-py's HGETALL/XINFO shape)."""
    it = iter(raw)
    return {_d(k): _d(v) for k, v in zip(it, it, strict=False)}


def _entries(raw: list) -> list[tuple[str, dict]]:
    return [(_d(eid), _pairs(fields)) for eid, fields in raw]


class _MiniRedis:
    """The slice of `redis.asyncio.Redis` the inspector and the lifecycle
    callsites consume — returned in redis-py's response shapes so the injected
    collaborator contract is the real one, exercised on the real wire."""

    def __init__(self, host: str, port: int) -> None:
        self._host, self._port = host, port

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port)

    async def close(self) -> None:
        self._writer.close()

    async def call(self, *args):
        self._writer.write(_enc_cmd(list(args)))
        await self._writer.drain()
        return await _read_reply(self._reader)

    async def xadd(self, stream: str, fields: dict,
                   entry_id: str = "*") -> str:
        flat = [x for kv in fields.items() for x in kv]
        return _d(await self.call("XADD", stream, entry_id, *flat))

    async def xgroup_create(self, stream: str, group: str,
                            entry_id: str = "0-0") -> None:
        await self.call("XGROUP", "CREATE", stream, group, entry_id)

    async def xreadgroup(self, group: str, consumer: str,
                       streams: dict, count: int = 10,
                       block_ms: int | None = None):
        args = ["XREADGROUP", "GROUP", group, consumer, "COUNT", count]
        if block_ms is not None:
            args += ["BLOCK", block_ms]
        args += ["STREAMS", *streams.keys(), *streams.values()]
        raw = await self.call(*args)
        if not raw:
            return []
        return [(_d(name), _entries(items)) for name, items in raw]

    async def xautoclaim(self, stream: str, group: str, consumer: str,
                         min_idle_ms: int, start: str, count: int = 10):
        raw = await self.call("XAUTOCLAIM", stream, group, consumer,
                              min_idle_ms, start, "COUNT", count)
        next_start = _d(raw[0])
        claimed = _entries(raw[1])
        deleted = [_d(d) for d in (raw[2] or [])] if len(raw) > 2 else []
        return next_start, claimed, deleted

    async def xack(self, stream: str, group: str, *ids: str) -> int:
        return int(await self.call("XACK", stream, group, *ids))

    async def xlen(self, stream: str) -> int:
        return int(await self.call("XLEN", stream))

    async def xrange(self, stream: str, min: str, max: str,
                     count: int | None = None):
        args = ["XRANGE", stream, min, max]
        if count:
            args += ["COUNT", count]
        return _entries(await self.call(*args))

    async def xpending(self, stream: str, group: str) -> dict:
        raw = await self.call("XPENDING", stream, group)
        n, lo, hi, consumers = raw
        return {"pending": int(n),
                "min": _d(lo) if lo else None,
                "max": _d(hi) if hi else None,
                "consumers": [{"name": _d(c[0]), "pending": int(c[1])}
                              for c in (consumers or [])]}

    async def xpending_range(self, stream: str, group: str,
                             min: str = "-", max: str = "+",
                             count: int | None = None) -> list[dict]:
        # the extended form's count is POSITIONAL — `start end count`,
        # there is no COUNT keyword (same shape redis-py emits)
        args = ["XPENDING", stream, group, min, max]
        if count:
            args.append(count)
        raw = await self.call(*args)
        return [{"message_id": _d(r[0]), "consumer": _d(r[1]),
                 "time_since_delivered": int(r[2]),
                 "times_delivered": int(r[3])} for r in raw]

    async def xinfo_groups(self, stream: str) -> list[dict]:
        raw = await self.call("XINFO", "GROUPS", stream)
        return [_pairs(g) for g in raw]

    async def time(self) -> tuple[int, int]:
        secs, micros = await self.call("TIME")
        return int(_d(secs)), int(_d(micros))


class _OneStreamDies(_MiniRedis):
    """The fault injector — ONE named stream fails, the rest behave: this is
    what a `partial` read is made of."""

    def __init__(self, *a, doomed: str, **kw):
        super().__init__(*a, **kw)
        self._doomed = doomed

    async def xinfo_groups(self, stream: str) -> list[dict]:
        if stream == self._doomed:
            raise _RespError("ERR socket closed mid-read")
        return await super().xinfo_groups(stream)


@pytest.fixture(scope="module")
def redis_server():
    """REAL Redis in a container — the same image the sibling compose pins
    (`redis:7.4-alpine`, deploy/docker-compose.yml there)."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (DockerContainer("redis:7.4-alpine")
                 .with_exposed_ports(6379))
    with container:
        wait_for_logs(container, "Ready to accept connections")
        yield (container.get_container_host_ip(),
               int(container.get_exposed_port(6379)))


@pytest.fixture
async def redis_client(redis_server):
    client = _MiniRedis(*redis_server)
    await client.connect()
    yield client
    await client.close()


# ---------------------------------------------------------------------------
# The durable side — real Postgres, the 0141 columns the queries touch
# ---------------------------------------------------------------------------

_BRIDGE_DDL = """
CREATE TABLE IF NOT EXISTS bridge_work (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    event_id TEXT,
    kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    platform TEXT NOT NULL,
    account_key TEXT NOT NULL DEFAULT 'synthetic',
    conversation_id TEXT NOT NULL DEFAULT 'synthetic',
    due_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    state TEXT NOT NULL DEFAULT 'pending',
    generation INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    lease_owner TEXT,
    leased_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS bridge_action (
    action_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    account_key TEXT NOT NULL DEFAULT 'synthetic',
    owner_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    owner_generation INTEGER,
    op TEXT NOT NULL,
    request JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_hash TEXT NOT NULL DEFAULT 'synthetic',
    state TEXT NOT NULL DEFAULT 'prepared',
    platform_ref TEXT,
    platform_ms BIGINT,
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
"""


@pytest.fixture
async def engine(pg_url):
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(pg_url)
    async with eng.begin() as conn:
        for stmt in _BRIDGE_DDL.strip().split(";\n"):
            if stmt.strip():
                await conn.execute(sa.text(stmt))
    yield eng
    await eng.dispose()


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _insert_work(engine, *, platform=PLATFORM, kind="turn",
                       state="running", generation=0, due_ms_offset=0):
    async with engine.begin() as conn:
        row = (await conn.execute(sa.text(
            "INSERT INTO bridge_work (platform, kind, state, generation,"
            " ordinal, account_key, conversation_id, payload, due_at)"
            " VALUES (:p, :k, :s, :g, 0, 'synthetic', 'synthetic',"
            " '{}'::jsonb,"
            " now() + CAST(:d AS double precision) * interval '1 millisecond')"
            " RETURNING id"),
            {"p": platform, "k": kind, "s": state, "g": generation,
             "d": due_ms_offset})).one()
    return int(row[0])


# ---------------------------------------------------------------------------
# The lifecycle: app-absent enqueue → attempt A → reclaim B → deliver → ACK,
# read back as SEPARATE linked roots from Jaeger — never from the exporter.
# ---------------------------------------------------------------------------

class TestLinkedLifecycle:
    async def test_enqueue_reclaim_ack_exports_linked_roots(
            self, otlp, redis_client, engine):
        metrics = Metrics()
        queued_ms = _now_ms()

        # ① Enqueue while the app is absent — the stream exists before any
        # group does, which is what "durable across a release gap" means.
        with bt.producer_span(PLATFORM, event_id="ev-t1") as carrier:
            envelope = {"id": "ev-t1", "trace": dict(carrier)}
            stream_id = await redis_client.xadd(
                STREAM, {"env": json.dumps(envelope)})
        bt.lifecycle(metrics, "enqueued", platform=PLATFORM,
                     event_id="ev-t1", stream_id=stream_id,
                     queued_ms=queued_ms)
        producer_ctx = bt.remote_context(carrier["traceparent"],
                                         carrier.get("tracestate"))
        producer_trace = carrier["traceparent"].split("-")[1]

        # The app arrives: group created AFTER the entry existed.
        await redis_client.xgroup_create(STREAM, CONSUMER_GROUP)
        entries = await redis_client.xreadgroup(
            CONSUMER_GROUP, "c-1", {STREAM: ">"}, count=1)
        assert entries[0][1][0][0] == stream_id
        env_back = json.loads(entries[0][1][0][1]["env"])
        producer_back = bt.remote_context(env_back["trace"]["traceparent"],
                                          env_back["trace"].get("tracestate"))
        assert producer_back is not None
        assert producer_back.trace_id == producer_ctx.trace_id

        # ② Attempt A registers durable work and persists its span ref BEFORE
        # running — a later reclaim can link to it even though A never
        # finished.
        work_id = await _insert_work(engine, state="running", generation=1)
        with bt.attempt_span(
            platform=PLATFORM, work_kind="turn", event_id="ev-t1",
            attempt_id=bt.attempt_identity(work_id, 1), generation=1,
            producer=producer_back, previous_attempt=None,
            queue_wait_ms=_now_ms() - queued_ms,
        ) as sp_a:
            rec_a = bt.attempt_record(
                generation=1, attempt_id=bt.attempt_identity(work_id, 1),
                ref=bt.span_ref(sp_a), started_ms=_now_ms())
            assert await bt.record_attempt_started(
                engine, work_id=work_id, generation=1,
                record=rec_a) == "written"
        attempt_a_trace = _trace_id_of(sp_a)
        bt.lifecycle(metrics, "attempt_started", platform=PLATFORM,
                     event_id="ev-t1",
                     attempt_id=bt.attempt_identity(work_id, 1),
                     ordinal=1)

        # A dies mid-attempt: no finish write, no ACK. The row reads back its
        # own telemetry_last_attempt — the reclaim's link target.
        async with engine.connect() as conn:
            result = (await conn.execute(sa.text(
                "SELECT result FROM bridge_work WHERE id = :i"),
                {"i": work_id})).scalar_one()
        prev = bt.last_attempt(result)
        assert prev and prev["attempt_id"] == f"w-{work_id}:1"

        # ③ Reclaim to B — XAUTOCLAIM hands the SAME entry to a new owner;
        # the generation fence moves with it.
        _next, claimed, _ = await redis_client.xautoclaim(
            STREAM, CONSUMER_GROUP, "c-2", 0, "0-0", count=1)
        assert claimed and claimed[0][0] == stream_id
        await _bump_generation(engine, work_id=work_id, generation=2)
        with bt.attempt_span(
            platform=PLATFORM, work_kind="turn", event_id="ev-t1",
            attempt_id=bt.attempt_identity(work_id, 2), generation=2,
            producer=producer_back,
            previous_attempt=bt.attempt_link(prev),
            queue_wait_ms=_now_ms() - queued_ms,
        ) as sp_b:
            rec_b = bt.attempt_record(
                generation=2, attempt_id=bt.attempt_identity(work_id, 2),
                ref=bt.span_ref(sp_b), started_ms=_now_ms())
            assert await bt.record_attempt_started(
                engine, work_id=work_id, generation=2,
                record=rec_b) == "written"
            # the outbound action is its own bounded root linked to B
            with bt.operation_span(
                    "deliver", platform=PLATFORM,
                    links=[Link(bt.attempt_link(rec_b)), Link(producer_back)],
                    action_id="act-t1", event_id="ev-t1") as sp_deliver:
                bt.lifecycle(metrics, "delivery_started", platform=PLATFORM,
                             event_id="ev-t1", action_id="act-t1")
                bt.lifecycle(metrics, "delivery_confirmed", platform=PLATFORM,
                             event_id="ev-t1", action_id="act-t1")
                bt.action_metrics(metrics, platform=PLATFORM,
                                  op="send_reply", outcome="confirmed")
            # the durable finish carries the completed attempt record in the
            # same fenced write
            finished = bt.attempt_finished_fields(
                2, record=rec_b, outcome="done",
                finished_ms=_now_ms())
            async with engine.begin() as conn:
                res = await conn.execute(sa.text(
                    "UPDATE bridge_work SET state='done',"
                    " result = result || CAST(:p AS jsonb),"
                    " completed_at = now(), updated_at = now()"
                    " WHERE id = :i AND generation = 2"),
                    {"p": json.dumps(finished), "i": work_id})
                assert res.rowcount == 1
            bt.attempt_metrics(metrics, platform=PLATFORM, kind="turn",
                               outcome="done", duration_ms=12.0)
            bt.observe_queue_wait(metrics, platform=PLATFORM,
                                  wait_ms=_now_ms() - queued_ms)
            bt.lifecycle(metrics, "completed", platform=PLATFORM,
                         event_id="ev-t1",
                         attempt_id=bt.attempt_identity(work_id, 2))
        attempt_b_trace = _trace_id_of(sp_b)
        deliver_trace = _trace_id_of(sp_deliver)
        attempt_b_ctx = bt.attempt_link(rec_b)

        # the model turn is a SEPARATE generation root that links BACK —
        # never a queue child (design 04)
        gen_fields = bt.generation_link_fields(
            attempt=bt.last_attempt(finished), event_id="ev-t1",
            attempt_id=bt.attempt_identity(work_id, 2),
            queue_wait_ms=100)
        from opentelemetry import trace as _trace
        from opentelemetry.context import Context
        sp_gen = _trace.get_tracer("nora").start_span(
            "bridge.turn", context=Context(), links=gen_fields["links"],
            attributes={"nora.trace_kind": "generation",
                        **gen_fields["attributes"]})
        gen_trace = _trace_id_of(sp_gen)
        sp_gen.end()

        # ④ ACK after the durable commit
        with bt.operation_span("ack", platform=PLATFORM,
                               links=[Link(attempt_b_ctx)],
                               event_id="ev-t1") as sp_ack:
            assert await redis_client.xack(
                STREAM, CONSUMER_GROUP, stream_id) == 1
        ack_trace = _trace_id_of(sp_ack)
        bt.lifecycle(metrics, "acked", platform=PLATFORM,
                     event_id="ev-t1", stream_id=stream_id)
        otlp.force_flush()

        # ----- Jaeger readback: the acceptance is the exported documents ----
        enq = _jaeger_trace(producer_trace)
        enq_span = enq["spans"][0]
        assert enq_span["operationName"] == "queue.enqueue"
        assert enq_span["references"] == []

        a = _jaeger_trace(attempt_a_trace)["spans"][0]
        assert a["operationName"] == "queue.attempt"
        refs = {(r["refType"], r["traceID"], r["spanID"])
                for r in a["references"]}
        assert ("FOLLOWS_FROM", producer_trace,
                f"{producer_ctx.span_id:016x}") in refs

        b = _jaeger_trace(attempt_b_trace)["spans"][0]
        refs = {(r["refType"], r["traceID"], r["spanID"])
                for r in b["references"]}
        prev_ctx = bt.attempt_link(prev)
        assert ("FOLLOWS_FROM", producer_trace,
                f"{producer_ctx.span_id:016x}") in refs
        assert ("FOLLOWS_FROM", attempt_a_trace,
                f"{prev_ctx.span_id:016x}") in refs
        tags = {t["key"]: t["value"] for t in b["tags"]}
        assert tags["nora.bridge.attempt_id"] == f"w-{work_id}:2"
        assert tags["nora.bridge.generation"] == 2
        assert tags["nora.bridge.event_id"] == "ev-t1"
        assert tags["nora.bridge.queue_wait_ms"] >= 0

        gen = _jaeger_trace(gen_trace)["spans"][0]
        assert gen["operationName"] == "bridge.turn"
        grefs = {(r["refType"], r["traceID"]) for r in gen["references"]}
        assert ("FOLLOWS_FROM", attempt_b_trace) in grefs
        # a separate root — no shared trace id anywhere in the chain
        assert len({producer_trace, attempt_a_trace, attempt_b_trace,
                    deliver_trace, ack_trace, gen_trace}) == 6

        assert _jaeger_trace(deliver_trace)["spans"][0][
            "operationName"] == "queue.deliver"
        assert _jaeger_trace(ack_trace)["spans"][0][
            "operationName"] == "queue.ack"

        # every exported queue attribute is the allowlisted vocabulary — the
        # envelope's private fields (sender, body) never crossed
        for trace_id in (attempt_a_trace, attempt_b_trace, deliver_trace,
                         ack_trace):
            for s in _jaeger_trace(trace_id)["spans"]:
                for t in s["tags"]:
                    assert t["key"].startswith("nora.") or t["key"].startswith(
                        ("otel.", "span.", "internal.")), t["key"]
                    assert "synthetic-sender" not in str(t["value"])

    async def test_generation_trace_is_sink_positive_control_and_queue_is_not(
            self, otlp):
        """The Langfuse leg: a generation's WHOLE trace is kept (tail
        sampling), a queue-only trace never arrives."""
        _http(f"{SINK}/reset", data=b"{}")
        from opentelemetry import trace as _trace
        from opentelemetry.context import Context, attach, detach
        t = _trace.get_tracer("nora")
        # a generation root with a chat child — the sink's positive control
        root = t.start_span("engagement.turn",
                            attributes={"nora.trace_kind": "generation",
                                        "nora.bridge.event_id": "ev-ctl"})
        token = attach(_trace.set_span_in_context(root, Context()))
        try:
            chat = t.start_span(
                "chat", attributes={"gen_ai.operation.name": "chat"})
            chat.end()
        finally:
            detach(token)
        root.end()
        # a queue-only trace in the same flush — must NOT reach the sink
        with bt.producer_span(PLATFORM, event_id="ev-q"):
            pass
        with bt.operation_span("ack", platform=PLATFORM):
            pass
        otlp.force_flush()

        deadline = time.monotonic() + 15  # decision_wait is 2s; batch is 200ms
        spans = []
        while time.monotonic() < deadline:
            spans = _sink_spans()
            if any(s["name"] == "chat" for s in spans):
                break
            await asyncio.sleep(0.4)
        names = {s["name"] for s in spans}
        assert "chat" in names                    # positive control DID land
        assert "engagement.turn" in names         # the whole trace is kept
        assert "queue.enqueue" not in names
        assert "queue.ack" not in names           # queue-only: absent
        # and the chat span the sink kept is exactly what Jaeger's filter
        # dropped — the routing asymmetry, read from both stores
        gen_trace_id = f"{root.get_span_context().trace_id:032x}"
        jnames = {s["operationName"]
                  for s in _jaeger_trace(gen_trace_id)["spans"]}
        assert "engagement.turn" in jnames and "chat" not in jnames

    async def test_queue_exclusion_beats_a_gen_ai_attribute(self, otlp):
        """The explicit kind filter runs BEFORE the tail policy: a queue
        root that somehow carries a model-looking `gen_ai` attribute still
        cannot reach Langfuse. Without the filter, the tail policy's
        string_attribute match would have kept the whole trace."""
        _http(f"{SINK}/reset", data=b"{}")
        from opentelemetry import trace as _trace
        from opentelemetry.context import Context, attach, detach
        t = _trace.get_tracer("nora")
        # positive control — a real generation trace lands
        root = t.start_span("engagement.turn",
                            attributes={"nora.trace_kind": "generation"})
        token = attach(_trace.set_span_in_context(root, Context()))
        try:
            chat = t.start_span(
                "chat", attributes={"gen_ai.operation.name": "chat"})
            chat.end()
        finally:
            detach(token)
        root.end()
        # the spoof: a queue root carrying a model attribute — `invoke_agent`
        # is tail-sampled but NOT Jaeger-filtered, so it's the sharpest probe:
        # without the kind filter this exact span would reach Langfuse AND
        # Jaeger. Set directly (the helper's allowlist would never pass it —
        # the collector must not trust span contents to carry kind truth).
        with bt.operation_span("ack", platform=PLATFORM,
                               event_id="ev-spoof") as sp:
            sp.set_attribute("gen_ai.operation.name", "invoke_agent")
        ack_trace = _trace_id_of(sp)
        otlp.force_flush()

        deadline = time.monotonic() + 15
        spans = []
        while time.monotonic() < deadline:
            spans = _sink_spans()
            if any(s["name"] == "chat" for s in spans):
                break
            await asyncio.sleep(0.4)
        names = {s["name"] for s in spans}
        assert "chat" in names              # positive control DID land
        assert "queue.ack" not in names     # the spoof stayed out
        # Jaeger's leg has no queue exclusion (and `invoke_agent` is not the
        # `chat` it drops) — the span IS there
        assert _jaeger_trace(ack_trace)["spans"][0][
            "operationName"] == "queue.ack"

    async def test_plumbing_roots_are_dropped_end_to_end(self, otlp):
        """A `plumbing` root is dropped by build_sampler() before ANY
        collector sees it — absent from Jaeger AND the sink. The positive
        control is a queue span in the same flush landing fine."""
        _http(f"{SINK}/reset", data=b"{}")
        from opentelemetry import trace as _trace
        from opentelemetry.context import Context

        from nora.obs.tracing import TRACE_KIND
        t = _trace.get_tracer("nora")
        sp = t.start_span("plumbing.probe", context=Context(),
                          attributes={TRACE_KIND: "plumbing"})
        sp.end()
        tid = f"{sp.get_span_context().trace_id:032x}"
        with bt.operation_span("ack", platform=PLATFORM) as qsp:
            pass
        otlp.force_flush()
        # control: the queue root DID export — the absence below is the
        # sampler's decision, not a broken pipe
        assert _jaeger_trace(_trace_id_of(qsp))["spans"][0][
            "operationName"] == "queue.ack"
        await asyncio.sleep(2)      # past the 200ms batch window
        try:
            data = json.loads(_http(f"{JAEGER}/api/traces/{tid}"))["data"]
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            data = []           # a 404 IS the absence we're asserting
        assert data == []
        assert all(s["name"] != "plumbing.probe" for s in _sink_spans())

    async def test_sanitized_error_exports_type_only_to_jaeger(self, otlp):
        planted = "s3cr3t-probe-value"
        with pytest.raises(RuntimeError), bt.attempt_span(
            platform=PLATFORM, work_kind="turn", event_id="ev-sec",
            attempt_id="w-sec:1", generation=1,
            producer=None, previous_attempt=None,
        ) as sp:
            raise RuntimeError(
                f"redis auth {planted} https://u:p@internal/path "
                "sender +15550100 body='hi there'")
        tid = _trace_id_of(sp)
        otlp.force_flush()
        s = _jaeger_trace(tid)["spans"][0]
        blob = json.dumps(s)
        assert planted not in blob and "15550100" not in blob
        assert "hi there" not in blob and "u:p@internal" not in blob
        # the failure is visible — as a TYPE, never as content
        assert any(entry["fields"] and "RuntimeError" in json.dumps(entry)
                   for entry in s["logs"])


async def _bump_generation(engine, *, work_id: int, generation: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "UPDATE bridge_work SET generation = :g, attempts = :g,"
            " updated_at = now() WHERE id = :i"),
            {"g": generation, "i": work_id})


# ---------------------------------------------------------------------------
# Inspection: waiting ≠ XLEN, pending is the PEL, failure is unavailable
# ---------------------------------------------------------------------------

class TestInspection:
    @pytest.mark.parametrize("kind", ["waiting", "pending"])
    async def test_age_gauge_clears_after_a_measured_drain(
            self, redis_client, kind):
        stream = f"nq:in:signal:age-drain-{kind}"
        entry = await redis_client.xadd(stream, {"env": "{}"})
        if kind == "pending":
            await redis_client.xgroup_create(stream, CONSUMER_GROUP)
            await redis_client.xreadgroup(
                CONSUMER_GROUP, "c-1", {stream: ">"}, count=1)
        await asyncio.sleep(0.03)
        metrics = Metrics()
        inspector = QueueInspector(redis_client, metrics=metrics)
        measured = await inspector.inspect([stream], platform=PLATFORM)
        assert measured.status == "ok"
        metric = f"nora_bridge_queue_{kind}_oldest_seconds"
        assert metrics.get(metric, platform=PLATFORM) > 0
        if kind == "waiting":
            await redis_client.xgroup_create(stream, CONSUMER_GROUP)
            await redis_client.xreadgroup(
                CONSUMER_GROUP, "c-1", {stream: ">"}, count=1)
        await redis_client.xack(stream, CONSUMER_GROUP, entry)
        empty = await inspector.inspect([stream], platform=PLATFORM)
        assert empty.status == "nothing_found"
        assert metrics.get(metric, platform=PLATFORM) == 0

    async def test_waiting_is_never_delivered_not_stream_length(
            self, redis_client):
        stream = "nq:in:signal:obs-inspect"
        for i in range(3):
            await redis_client.xadd(stream, {"env": json.dumps({"i": i})})
        metrics = Metrics()
        insp = QueueInspector(redis_client, metrics=metrics)

        # the group does not exist yet: every entry waits (not "checked 0
        # with entries present")
        snap = await insp.inspect([stream], platform=PLATFORM)
        assert snap.status == "ok"
        assert snap.waiting_entries == 3 and snap.pending_entries == 0
        assert snap.waiting_oldest_ms is not None

        # consume one: waiting drops, the PEL picks it up — disjoint sets
        await redis_client.xgroup_create(stream, CONSUMER_GROUP)
        got = await redis_client.xreadgroup(
            CONSUMER_GROUP, "c-1", {stream: ">"}, count=1)
        assert len(got[0][1]) == 1
        snap = await insp.inspect([stream], platform=PLATFORM)
        assert snap.waiting_entries == 2 and snap.pending_entries == 1
        assert snap.pending_oldest_ms is not None
        # and the stream LENGTH is 3 — the number XLEN would have told: the
        # assertion exists to prove waiting isn't it.
        assert await redis_client.xlen(stream) == 3

        # ACK it: PEL drains; waiting unchanged
        await redis_client.xack(stream, CONSUMER_GROUP, got[0][1][0][0])
        snap = await insp.inspect([stream], platform=PLATFORM)
        assert snap.waiting_entries == 2 and snap.pending_entries == 0
        # gauges were written for a real read (the positive control for the
        # unavailable test below)
        assert metrics.get("nora_bridge_queue_waiting_entries",
                           platform=PLATFORM) == 2

    async def test_unavailable_is_not_zero(self, redis_server):
        host, port = redis_server
        dead = _MiniRedis(host, port)
        await dead.connect()
        await dead.close()                    # dead wire
        metrics = Metrics()
        insp = QueueInspector(dead, metrics=metrics)
        snap = await insp.inspect(["nq:in:signal:dead"], platform=PLATFORM)
        assert snap.status == "unavailable"
        assert snap.waiting_entries is None and snap.pending_entries is None
        # a failed read publishes NO backlog sample — and the inspection
        # itself is what got counted
        assert metrics.get("nora_bridge_inspections_total",
                           platform=PLATFORM, result="unavailable") == 1
        assert metrics.get("nora_bridge_queue_waiting_entries",
                           platform=PLATFORM) == 0

    async def test_degraded_read_is_unavailable_with_sections_kept(
            self, redis_server):
        """[healthy, failed] must NOT publish a fresh-0 aggregate or the
        healthy half's 1 as the whole — the aggregate is unmeasurable, so
        `unavailable` + `partial`, sections retained, no gauge."""
        host, port = redis_server
        stream_ok, stream_dead = "nq:in:signal:ok", "nq:in:signal:dead"
        client = _OneStreamDies(host, port, doomed=stream_dead)
        await client.connect()
        await client.xadd(stream_ok, {"env": "{}"})
        metrics = Metrics()
        insp = QueueInspector(client, metrics=metrics)
        snap = await insp.inspect([stream_ok, stream_dead],
                                  platform=PLATFORM)
        assert snap.status == "unavailable"     # not a fourth result
        assert snap.partial is True             # ... but something measured
        assert snap.waiting_entries is None     # no fresh-0 / lower bound
        assert snap.pending_entries is None
        # the section that DID measure stays visible — explicitly, in the
        # per-stream snapshots, never silently summed
        ok_snap = next(s for s in snap.streams if s.stream == stream_ok)
        assert ok_snap.waiting == 1
        assert next(s for s in snap.streams
                    if s.stream == stream_dead).error
        # no aggregate gauge — the counter records the inspection itself
        assert metrics.get("nora_bridge_queue_waiting_entries",
                           platform=PLATFORM) == 0
        assert metrics.get("nora_bridge_inspections_total",
                           platform=PLATFORM, result="unavailable") == 1
        await client.close()

    async def test_db_depth_deferred_retained_uncertain(
            self, redis_client, engine):
        for state, offset in (("pending", -50), ("retryable_failure", 30_000),
                              ("failed_retained", 0), ("done", 0),
                              ("uncertain", 0)):
            await _insert_work(engine, state=state,
                               due_ms_offset=offset)
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO bridge_action (action_id, platform, account_key,"
                " owner_kind, owner_id, op, request, request_hash, state)"
                " VALUES ('act-u1', :p, 'synthetic', 'work', 'w-x',"
                " 'send_reply', '{}'::jsonb, 'h-u1', 'uncertain')"),
                {"p": PLATFORM})
        metrics = Metrics()
        insp = QueueInspector(redis_client, engine=engine, metrics=metrics)
        snap = await insp.inspect([], platform=PLATFORM)
        assert snap.status == "ok"
        assert snap.deferred == {"pending": 1, "retryable_failure": 1}
        assert snap.retained == {"failed_retained": 1, "uncertain": 1}
        assert snap.uncertain_actions == 1
        assert snap.oldest_due_seconds is not None \
            and snap.oldest_due_seconds >= 0
        assert metrics.get("nora_bridge_deferred_work_entries",
                           platform=PLATFORM, state="pending") == 1
        assert metrics.get("nora_bridge_retained_actions",
                           platform=PLATFORM, state="uncertain") == 1

        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "UPDATE bridge_work SET state='done' WHERE platform=:p"),
                {"p": PLATFORM})
            await conn.execute(sa.text(
                "UPDATE bridge_action SET state='confirmed' WHERE platform=:p"),
                {"p": PLATFORM})
        empty = await insp.inspect([], platform=PLATFORM)
        assert empty.status == "nothing_found"
        assert metrics.get("nora_bridge_deferred_work_entries",
                           platform=PLATFORM, state="pending") == 0
        assert metrics.get("nora_bridge_retained_work_entries",
                           platform=PLATFORM, state="failed_retained") == 0
        assert metrics.get("nora_bridge_retained_actions",
                           platform=PLATFORM, state="uncertain") == 0

    async def test_db_failure_marks_unavailable_not_empty(
            self, redis_client, pg_url):
        from sqlalchemy.ext.asyncio import create_async_engine
        bad = create_async_engine(
            pg_url.replace("/t_", "/db_that_does_not_exist_"))
        metrics = Metrics()
        insp = QueueInspector(redis_client, engine=bad, metrics=metrics)
        await redis_client.xadd("nq:in:signal:dbf", {"env": "{}"})
        snap = await insp.inspect(["nq:in:signal:dbf"], platform=PLATFORM)
        assert snap.status == "unavailable"  # can't measure fully
        assert snap.partial is True          # redis section DID measure
        assert snap.db_status == "unavailable"
        assert snap.streams[0].waiting == 1  # retained, not summed
        assert snap.waiting_entries is None
        assert snap.deferred == {}
        await bad.dispose()

    async def test_nothing_found_is_a_checked_empty(self, redis_client,
                                                    engine):
        # every section measured, every obligation absent — checked-empty,
        # not "we didn't look"
        insp = QueueInspector(redis_client, engine=engine)
        snap = await insp.inspect(["nq:in:signal:empty"], platform=PLATFORM)
        assert snap.status == "nothing_found"
        assert snap.db_status == "ok"
        # positive control: a lone uncertain ACTION is an obligation — not
        # nothing — even with empty streams and no work rows
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO bridge_action (action_id, platform, account_key,"
                " owner_kind, owner_id, op, request, request_hash, state)"
                " VALUES ('act-nf', :p, 'synthetic', 'work', 'w-x',"
                " 'send_reply', '{}'::jsonb, 'h-nf', 'uncertain')"),
                {"p": PLATFORM})
        snap = await insp.inspect(["nq:in:signal:empty"], platform=PLATFORM)
        assert snap.status == "ok"
        assert snap.uncertain_actions == 1

    async def test_pending_age_is_delivery_idle_not_enqueue_age(
            self, redis_client):
        """The PEL age is the server's IDLE evidence — time since last
        DELIVERY. An entry enqueued an hour ago and delivered just now is a
        fresh pending; enqueue-age would lie about it by 3.6e6 ms."""
        stream = "nq:in:signal:obs-idle"
        old_id = f"{_now_ms() - 3_600_000}-0"          # an hour-old entry id
        await redis_client.xadd(stream, {"env": "{}"}, entry_id=old_id)
        await redis_client.xgroup_create(stream, CONSUMER_GROUP)
        got = await redis_client.xreadgroup(
            CONSUMER_GROUP, "c-1", {stream: ">"}, count=1)
        assert got[0][1][0][0] == old_id
        insp = QueueInspector(redis_client)

        snap = await insp.inspect([stream], platform=PLATFORM)
        assert snap.pending_entries == 1
        assert snap.pending_oldest_ms is not None
        assert snap.pending_oldest_ms < 60_000   # idle ≈ now, not ~3_600_000

        # idle GROWS while unacked — it tracks the delivery, not the enqueue
        first = snap.pending_oldest_ms
        await asyncio.sleep(0.4)
        snap = await insp.inspect([stream], platform=PLATFORM)
        assert snap.pending_oldest_ms >= first + 250

        # a RECLAIM is a new delivery — the idle clock restarts (the
        # redelivery change the design measures against)
        _n, claimed, _ = await redis_client.xautoclaim(
            stream, CONSUMER_GROUP, "c-2", 0, "0-0", count=1)
        assert claimed
        snap = await insp.inspect([stream], platform=PLATFORM)
        assert snap.pending_oldest_ms < first + 250

    async def test_real_redis_client_shapes_the_contract(
            self, redis_server):
        """The injected-client contract against the REAL `redis.asyncio`
        client — same dict/bytes shapes `_MiniRedis` mirrors, same
        `xpending_range` idle evidence. Runs under `uv run --with redis`;
        skips where redis is not installed."""
        aioredis = pytest.importorskip("redis.asyncio")
        host, port = redis_server
        client = aioredis.Redis(host=host, port=port)
        try:
            stream = "nq:in:signal:obs-real"
            await client.xadd(stream, {"env": "{}"})
            insp = QueueInspector(client)
            snap = await insp.inspect([stream], platform=PLATFORM)
            assert snap.status == "ok" and snap.waiting_entries == 1

            await client.xgroup_create(stream, CONSUMER_GROUP, id="0")
            got = await client.xreadgroup(CONSUMER_GROUP, "c-1",
                                          {stream: ">"}, count=1)
            assert got
            snap = await insp.inspect([stream], platform=PLATFORM)
            assert snap.waiting_entries == 0
            assert snap.pending_entries == 1
            assert snap.pending_oldest_ms is not None

            # the raw extended-XPENDING callback dict the inspector reads —
            # byte keys/values verified against the real client, not assumed
            raw = await client.xpending_range(
                stream, CONSUMER_GROUP, min="-", max="+", count=5)
            assert isinstance(raw, list) and isinstance(raw[0], dict)
            assert "time_since_delivered" in raw[0]
            assert isinstance(raw[0]["message_id"], bytes)
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Durable attempt record — the receipt truth under the generation fence
# ---------------------------------------------------------------------------

class TestAttemptRecordDurable:
    async def test_record_survives_and_stale_writes_are_fenced(self, engine):
        work_id = await _insert_work(engine, state="running", generation=1)
        rec = {"generation": 1, "attempt_id": bt.attempt_identity(work_id, 1),
               "trace_id": "ab" * 16, "span_id": "cd" * 8,
               "traceparent": f"00-{'ab'*16}-{'cd'*8}-01",
               "started_ms": _now_ms()}
        assert await bt.record_attempt_started(
            engine, work_id=work_id, generation=1, record=rec) == "written"
        # the stale generation loses the fence — a dead attempt can't
        # overwrite the live one's link target. `fenced` is NOT
        # `unavailable`: the fence answered, telemetry itself is fine
        stale = dict(rec, span_id="ee" * 8)
        assert await bt.record_attempt_started(
            engine, work_id=work_id, generation=0,
            record=stale) == "fenced"
        async with engine.connect() as conn:
            result = (await conn.execute(sa.text(
                "SELECT result FROM bridge_work WHERE id = :i"),
                {"i": work_id})).scalar_one()
        assert bt.last_attempt(result)["attempt_id"] == rec["attempt_id"]
        assert bt.attempt_link(bt.last_attempt(result)) is not None

    async def test_finished_attempt_reads_back_for_the_next_link(
            self, engine):
        work_id = await _insert_work(engine, state="running", generation=1)
        rec = bt.attempt_record(
            generation=1, attempt_id=bt.attempt_identity(work_id, 1),
            ref={"trace_id": "ff" * 16, "span_id": "11" * 8,
                 "traceparent": f"00-{'ff'*16}-{'11'*8}-01"},
            started_ms=_now_ms())
        assert await bt.record_attempt_started(
            engine, work_id=work_id, generation=1, record=rec) == "written"
        fields = bt.attempt_finished_fields(
            1, record=rec, outcome="failed_retained",
            finished_ms=_now_ms(), reason="retained")
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "UPDATE bridge_work SET state='failed_retained',"
                " result = result || CAST(:p AS jsonb) WHERE id = :i"),
                {"p": json.dumps(fields), "i": work_id})
            result = (await conn.execute(sa.text(
                "SELECT result FROM bridge_work WHERE id = :i"),
                {"i": work_id})).scalar_one()
        last = bt.last_attempt(result)
        assert last["outcome"] == "failed_retained"
        assert result["telemetry_attempt_1"]["finished_ms"] is not None


# ---------------------------------------------------------------------------
# Collector outage — telemetry must never be load-bearing
# ---------------------------------------------------------------------------

class TestCollectorOutage:
    async def test_queue_work_completes_with_the_collector_down(
            self, otel_stack, otlp):
        """The exporter's POST fails, the spans drop, the work does not
        notice — the whole point of helpers that never raise. Then the
        collector comes back and export resumes (positive control that the
        outage, not the code, was the gap)."""
        stopped = _compose("stop", "collector")
        assert stopped.returncode == 0
        try:
            with bt.producer_span(PLATFORM, event_id="ev-outage") as carrier:
                pass
            with bt.attempt_span(
                platform=PLATFORM, work_kind="turn", event_id="ev-outage",
                attempt_id="w-out:1", generation=1,
                producer=bt.remote_context(carrier.get("traceparent")),
                previous_attempt=None,
            ):
                pass
            metrics = Metrics()
            bt.lifecycle(metrics, "enqueued", platform=PLATFORM,
                         event_id="ev-outage")
            otlp.force_flush()          # export fails here — silently
        finally:
            started = _compose("start", "collector")
            assert started.returncode == 0
            _wait_tcp(4318)
        # export resumes: a fresh queue root lands in Jaeger
        with bt.operation_span("ack", platform=PLATFORM,
                               event_id="ev-after") as sp:
            pass
        otlp.force_flush()
        s = _jaeger_trace(_trace_id_of(sp))["spans"][0]
        assert s["operationName"] == "queue.ack"
