"""The app's side of the platform-bridge handoff (design/platform-bridges).

`StreamReader` — one ELECTED reader per Redis stream (a Postgres advisory
lock is the election). It `XAUTOCLAIM`s pending entries first (a dead
consumer's backlog), then `XREADGROUP`s new ones, and registers each envelope
as a `bridge_event` row PLUS its first `bridge_work` obligation in ONE
transaction. It does NOT `XACK` — per the approved baseline, the entry stays
pending until the ingest obligation commits complete downstream ownership;
the `WorkRunner` ACKs there.

`WorkRunner` — claims `bridge_work` rows (`FOR UPDATE SKIP LOCKED`), executes
them under the right per-conversation lock (ingest lock for `ingest`,
speaking lock for `turn`), and finishes them fenced by `generation`. A lease
that outlives its owner is re-claimed; a turn whose action rows already show
a started-or-later effect is marked `uncertain`, never replayed.

`DbSpeakingLocks` — the cross-process half of the speaking slot (a Postgres
advisory lock per conversation). The runner holds it AND the shared in-memory
`ConversationSlots` together, so bridge turns serialise against heartbeat
fires in-process and against a zombie or recreate-overlap peer at the DB.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import redis.asyncio as aioredis
import sqlalchemy as sa
from opentelemetry.trace import Link

from nora.domain.actions import ActionOwner
from nora.domain.ports import EngageDecision, EngagementRequest
from nora.infra.sql.bridge_store import (
    WORK_TERMINAL,
    BridgeWork,
    SqlBridgeStore,
)
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.envelope import Envelope, canonical_digest
from nora.platform.bridge.inspection import QueueInspector
from nora.platform.bridge.queue import CONSUMER_GROUP, ensure_group
from nora.platform.bridge.rehydrate import message_from_dict, message_to_dict

log = logging.getLogger("nora.app.bridge_queue")

SPEAK_NS = "nora-bridge-speak"
INGEST_NS = "nora-bridge-ingest"
READER_NS = "nora-bridge-reader"

# Legitimate silences at the queue seam — never retire an audit failure here.
SILENT_OUTCOMES = frozenset({
    "scope_refused", "edit_orphaned", "edit_absorbed", "reaction_recorded",
    "delete_recorded", "sender_not_allowed", "recorded",
})
FAILURE_OUTCOMES = frozenset({"audit_refused", "extraction_refused"})


class _LeaseLostError(Exception):
    """The work's lease stopped renewing (fenced row) or a held lock's
    connection died — the attempt halts before any further effect."""


#: `bridge_work.state` → the bounded lifecycle event emitted when the fenced
#: finish commits. `uncertain` has no event of its own — it lands as
#: `attempt_failed` with `outcome="uncertain"` distinguishing it from a
#: retryable stop.
_STATE_EVENT: dict[str, bt.LifecycleEvent] = {
    "done": "completed",
    "intentional_skip": "intentional_silence",
    "retryable_failure": "attempt_failed",
    "failed_retained": "failed_retained",
    "uncertain": "attempt_failed",
}

#: Independent claim lanes — design 03's "do not serialize all ingestion
#: behind a slow model" made structural. The comprehend lane runs every kind
#: that never holds a model turn; the speak lane runs the kinds that can.
#: Within a lane the DB claim is the no-duplicate arbiter (`FOR UPDATE SKIP
#: LOCKED`, generation bump), and per-conversation ordering still comes from
#: the INGEST/SPEAK advisory locks — lanes add INDEPENDENCE, not new rules.
_COMPREHEND_KINDS = frozenset({
    "ingest", "stimulus", "memory_feed", "participation", "receipt",
})
_SPEAK_KINDS = frozenset({"turn", "trace_debrief", "participation_fire"})

#: (kinds, workers): two of each — one suspended turn can occupy at most one
#: speak worker, and one slow ingest at most one comprehend worker.
_LANES: tuple[tuple[frozenset[str], int], ...] = (
    (_COMPREHEND_KINDS, 2),
    (_SPEAK_KINDS, 2),
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _person_dict(person: Any) -> dict[str, Any] | None:
    """The handoff shape for a resolved sender — the field set a `PersonRef`
    is rebuilt from on the far side of a work row or an event snapshot."""
    if person is None:
        return None
    return {"person_id": person.person_id, "name": person.name,
            "honcho_peer_id": person.honcho_peer_id,
            "first_name": person.first_name, "last_name": person.last_name,
            "aliases": list(person.aliases)}


def _decision_dict(decision: Any) -> dict[str, Any] | None:
    """The admitted-facts shape for an `EngageDecision` — what a turn
    payload and the ingest handoff snapshot both persist."""
    if decision is None:
        return None
    return {"engage": decision.engage, "reason": decision.reason,
            "rationale": decision.rationale,
            "confidence": decision.confidence,
            "threshold": decision.threshold,
            "allow_episode": decision.allow_episode,
            "relevance_gate": decision.relevance_gate}


def _payload_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """The producer's W3C carrier, wherever this work kind carries it —
    `envelope.trace` for ingest, propagated `trace` for the kinds ingest
    enqueued downstream."""
    env = payload.get("envelope")
    trace = env.get("trace") if isinstance(env, dict) else payload.get("trace")
    return dict(trace) if isinstance(trace, dict) else {}


def _queue_wait_ms(payload: dict[str, Any]) -> int | None:
    """Enqueue→claim latency in ms: the envelope's `queued_ms` for ingest,
    the `enqueued_ms` ingest stamps on downstream obligations for the rest.
    A missing stamp is absent data (None), never a fabricated zero."""
    env = payload.get("envelope")
    ref: Any = None
    if isinstance(env, dict):
        ref = env.get("queued_ms")
    if ref is None:
        ref = payload.get("enqueued_ms")
    if ref is None:
        return None
    return max(0, _now_ms() - int(ref))


def _link(ctx: Any) -> list[Link]:
    return [Link(ctx)] if ctx is not None else []


class DbSpeakingLocks:
    """The speaking slot's CROSS-PROCESS half: one Postgres advisory lock per
    (platform, conversation), held on a dedicated connection for the turn's
    whole run. The in-memory `ConversationSlots` every app path shares is the
    intra-process half — the runner holds BOTH, so a heartbeat fire and a
    bridge turn serialise on the same object in-process while a zombie or a
    recreate-overlap peer is fenced out at the DB. `lock_alive` surfaces the
    closed-connection case so a caller never believes it still speaks for a
    room after the DB went away.
    """

    def __init__(self, store: SqlBridgeStore) -> None:
        self._store = store
        self._in_flight: dict[tuple[str, str], Any] = {}

    async def try_acquire(self, key: tuple[str, str]) -> bool:
        if key in self._in_flight:
            return False
        conn = await self._store._engine.connect()
        try:
            ok = (await conn.execute(sa.text(
                "SELECT pg_try_advisory_lock(hashtext(:ns), hashtext(:k))"),
                {"ns": SPEAK_NS, "k": f"{key[0]}:{key[1]}"})).scalar_one()
        except sa.exc.SQLAlchemyError:
            await conn.close()
            return False
        if not ok:
            await conn.close()
            return False
        self._in_flight[key] = conn
        return True

    async def lock_alive(self, key: tuple[str, str]) -> bool:
        """Whether THIS held session still holds the advisory lock — asked on
        the lock-holding connection itself, against `pg_locks` filtered to
        `pg_backend_pid()`. `conn.closed` alone cannot see a server-killed
        backend: the socket looks open until it is used. The query does
        double duty — no matching row means the hold is gone (unlock or a
        dead-peer steal), and a killed backend surfaces as a failed execute.
        A pooled-connection probe would answer "the POOL is alive", not
        "the session that speaks for the room still does"."""
        conn = self._in_flight.get(key)
        if conn is None or conn.closed:
            return False
        try:
            held = await asyncio.wait_for(conn.execute(sa.text(
                "SELECT 1 FROM pg_locks WHERE locktype = 'advisory'"
                # pg_locks stores the int4 key as OID (unsigned); hashtext
                # is signed — a negative hash only matches through ::oid.
                " AND classid = hashtext(:ns)::oid"
                " AND objid = hashtext(:k)::oid"
                " AND pid = pg_backend_pid()"),
                {"ns": SPEAK_NS, "k": f"{key[0]}:{key[1]}"}),
                timeout=5.0)
            return held.scalar_one_or_none() is not None
        except (TimeoutError, sa.exc.SQLAlchemyError, OSError):
            return False

    async def release(self, key: tuple[str, str]) -> None:
        conn = self._in_flight.pop(key, None)
        if conn is None:
            return
        try:
            await conn.execute(sa.text(
                "SELECT pg_advisory_unlock(hashtext(:ns), hashtext(:k))"),
                {"ns": SPEAK_NS, "k": f"{key[0]}:{key[1]}"})
        except sa.exc.SQLAlchemyError:
            pass  # a dead connection already released the lock server-side
        finally:
            await conn.close()


def stream_name(platform: str, account_key: str) -> str:
    return f"nq:in:{platform}:{account_key}"


@dataclass
class BridgeRuntime:
    """The bridge-mode runtime surface composition hands to `nora/__main__`:
    one elected `StreamReader` per (platform, account_key) stream and the one
    `WorkRunner`. `run()` is cancelled by the app's `background.cancel_all`
    like every other spawned loop; `health()` reports each reader's liveness
    honestly — an unavailable Redis or a lost election is degraded, never
    reported healthy.
    """

    redis: aioredis.Redis
    store: SqlBridgeStore
    readers: list[StreamReader]
    runner: WorkRunner
    inspector: QueueInspector | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def run(self) -> None:
        children = [asyncio.ensure_future(self.runner.run()),
                    *(asyncio.ensure_future(r.run())
                      for r in self.readers)]
        try:
            await asyncio.gather(*children)
        except BaseException:
            # Same drain contract as WorkRunner.run: a plain gather returns
            # on the first child's end while siblings still unwind — and a
            # re-cancel would kill an already-unwinding child's cleanup.
            self._stop.set()
            for child in children:
                if not child.done() and child.cancelling() == 0:
                    child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            raise

    async def inspect_forever(self, *, interval_seconds: float = 60.0) -> None:
        """The runtime inspection loop — composition spawns it beside
        `run()`. One bounded `QueueInspector.inspect` per reader stream per
        interval; a degraded read publishes `unavailable` and NO backlog
        gauges, per the inspector's own posture."""
        if self.inspector is None:
            return
        while not self._stop.is_set():
            for reader in self.readers:
                await self.inspector.inspect(
                    [reader.stream], platform=reader._platform)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=interval_seconds)

    async def queue_snapshot(self) -> dict[str, Any]:
        """The HTTP-mount surface (composition mounts it): one bounded
        inspection per reader stream as a JSON-safe dict. `unavailable`
        when the inspector is unwired — an absent readout is honest, a
        fabricated empty one is not."""
        if self.inspector is None:
            return {"status": "unavailable",
                    "detail": "queue inspector not wired"}
        return {
            reader.stream: asdict(await self.inspector.inspect(
                [reader.stream], platform=reader._platform))
            for reader in self.readers
        }

    async def health(self) -> dict[str, Any]:
        try:
            await self.redis.ping()
            redis_ok = True
        except aioredis.RedisError:
            redis_ok = False
        return {"ok": redis_ok,
                "streams": [r.stream for r in self.readers],
                "detail": "" if redis_ok else "redis unavailable"}

    def stop(self) -> None:
        self._stop.set()
        self.runner.stop()
        for r in self.readers:
            r.stop()


class StreamReader:
    """Elected consumer of ONE bridge stream (design: one reader per stream).

    Registration is atomic — bridge_event row + ingest obligation in one
    transaction — but the entry stays PENDING: `XACK` happens in the
    WorkRunner after the ingest obligation's commit, which is the approved
    baseline ("pending until critical ingestion finishes or complete durable
    downstream ownership is committed").
    """

    def __init__(self, *, platform: str, account_key: str,
                 redis: aioredis.Redis, store: SqlBridgeStore,
                 consumer: str | None = None,
                 metrics: Any = None) -> None:
        self._platform = platform
        self._account_key = account_key
        self.stream = stream_name(platform, account_key)
        self._redis = redis
        self._store = store
        self._metrics = metrics
        self._consumer = consumer or f"nora-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            async with self._store.advisory_lock(
                    READER_NS, self.stream) as elected:
                if not elected:
                    await asyncio.sleep(2.0)
                    continue
                try:
                    await ensure_group(self._redis, self.stream)
                    await self._read()
                except (aioredis.RedisError, sa.exc.SQLAlchemyError) as exc:
                    log.warning(
                        "stream reader %s: transient failure (%s), retrying",
                        self.stream, type(exc).__name__)
                    await asyncio.sleep(2.0)

    async def _read(self) -> None:
        while not self._stop.is_set():
            claimed: Any = await self._redis.xautoclaim(
                self.stream, CONSUMER_GROUP, self._consumer,
                min_idle_time=30_000, start_id="0-0", count=50)
            for stream_id, fields in claimed[1] if len(claimed) > 1 else ():
                await self._register(stream_id, fields, reclaimed=True)
            batch: Any = await self._redis.xreadgroup(
                CONSUMER_GROUP, self._consumer,
                {self.stream: ">"}, count=50, block=5000)
            for _stream, entries in batch or ():
                for stream_id, fields in entries:
                    await self._register(stream_id, fields)

    async def _register(self, stream_id: str, fields: dict[Any, Any],
                        *, reclaimed: bool = False) -> None:
        sid = stream_id.decode() if isinstance(stream_id, bytes) else stream_id
        raw: Any = fields.get(b"e") or fields.get("e")
        envelope = Envelope.from_fields(json.loads(raw))
        producer_ctx = bt.remote_context(
            envelope.trace.get("traceparent"),
            envelope.trace.get("tracestate"))
        links = _link(producer_ctx)
        with bt.operation_span(
                "claim", platform=self._platform, links=links,
                event_id=envelope.event_id, stream_id=sid,
                stream=self.stream, consumer=self._consumer):
            async with self._store._engine.begin() as conn:
                event_id, is_new = await self._store.register_event(
                    conn, event_id=envelope.event_id,
                    platform=envelope.platform,
                    account_key=envelope.account_key,
                    source_key=envelope.source_key,
                    source_digest=canonical_digest(envelope.source_key),
                    kind=envelope.kind, stream=self.stream, stream_id=sid,
                    conversation_id=envelope.conversation_id,
                    conversation_type=envelope.conversation_type,
                    sender_id=envelope.sender_id,
                    received_ms=envelope.received_ms,
                    queued_ms=envelope.queued_ms,
                    time_quality=envelope.time_quality,
                    payload={"envelope": envelope.to_fields()},
                    admission_rev=envelope.admission_rev)
                if is_new:
                    await self._store.enqueue_work(
                        conn, event_id=event_id, kind="ingest", ordinal=0,
                        platform=envelope.platform,
                        account_key=envelope.account_key,
                        conversation_id=envelope.conversation_id,
                        payload={"envelope": envelope.to_fields(),
                                 "stream": self.stream, "stream_id": sid})
                # The entry is only ACKable once its event's work is durably
                # owned — re-check EVERY registration, not only is_new=False:
                # `register_event` answers "new EVENT?", and a redelivered
                # entry for the same event is still new to it. A redelivery
                # after a lost ACK (or a rotated-alias replay) whose
                # obligations all committed would otherwise pend forever.
                open_work = await self._store.event_open_work(conn, event_id)
                if not open_work:
                    with bt.operation_span(
                            "ack", platform=self._platform, links=links,
                            event_id=event_id, stream_id=sid):
                        await self._redis.xack(
                            self.stream, CONSUMER_GROUP, sid)
                    bt.lifecycle(self._metrics, "acked",
                                 platform=self._platform,
                                 event_id=event_id, stream_id=sid,
                                 stream=self.stream)
        duplicate = (not is_new) or not open_work
        bt.lifecycle(
            self._metrics,
            "duplicate_suppressed" if duplicate
            else "reclaimed" if reclaimed else "claimed",
            platform=self._platform, event_id=event_id, stream_id=sid,
            stream=self.stream,
            queue_wait_ms=max(0, _now_ms() - envelope.queued_ms))

    def stop(self) -> None:
        self._stop.set()


class WorkRunner:
    """Executes bridge_work obligations. Dependencies are the app's own
    pipelines — a bridge work row is a new OWNER of the same turn, never a
    parallel prompt/policy path."""

    def __init__(self, *, store: SqlBridgeStore, redis: aioredis.Redis,
                 ingest: Any, engagement: Any, slots: Any,
                 db_locks: DbSpeakingLocks,
                 adapters: dict[str, Any], gate: Any = None, metrics: Any = None,
                 participation: Any = None, memory_feed: Any = None,
                 stimulus: Any = None, debrief: Any = None,
                 instance: str | None = None, lease_seconds: int = 300,
                 allowlist: Any = None, persons: Any = None,
                 fire_speak: Any = None, participation_state: Any = None) -> None:
        self._store = store
        self._redis = redis
        self._ingest = ingest
        self._engagement = engagement
        self._slots = slots            # the SHARED in-memory ConversationSlots
        self._db_locks = db_locks      # the cross-process speaking half
        self._adapters = adapters
        self._gate = gate            # RelevanceGate | None (participation eval)
        self._metrics = metrics
        self._participation = participation  # ParticipationTrigger | None
        self._memory_feed = memory_feed      # memory_feed stage deps bundle | None
        self._stimulus = stimulus            # stimulus_stage.Wiring | None
        self._debrief = debrief              # Callable(trace_payload) | None
        # The CURRENT policy + identity truth, consulted by deferred effects:
        # an obligation enqueued under a policy that has since moved must fail
        # closed, never run under the snapshot's stale permission alone.
        self._allowlist = allowlist        # Policy | None
        self._persons = persons            # Persons | None
        self._fire_speak = fire_speak      # FirePath.speak bound method | None
        self._participation_state = participation_state  # cursor store | None
        self._owner = instance or f"nora-{uuid.uuid4().hex[:8]}"
        self._lease = lease_seconds
        self._stop = asyncio.Event()
        # In-flight attempts keyed by work id: `(work, telemetry record,
        # started_ms)`, set after `record_attempt_started`, cleared in
        # `_execute`'s finally. A dict, not one slot — the claim lanes run
        # attempts CONCURRENTLY, and a shared slot would let a turn's finish
        # merge an ingest's record.
        self._attempts: dict[int, tuple[Any, dict[str, Any], int]] = {}

    async def run(self) -> None:
        await self._store.expire_leases()
        lanes = [asyncio.ensure_future(self._claim_loop(kinds))
                 for kinds, workers in _LANES for _ in range(workers)]
        try:
            await asyncio.gather(*lanes)
        except BaseException:
            # gather(return_exceptions=False) resolves on the FIRST child
            # that ends — a sibling lane can still be unwinding its except
            # handler, owning a claimed row. Never return while a lane is
            # still alive. Re-cancelling a lane that is ALREADY unwinding
            # would deliver a second CancelledError into its repark write,
            # so only lanes not already cancelling get stopped.
            self._stop.set()
            for lane in lanes:
                if not lane.done() and lane.cancelling() == 0:
                    lane.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)
            raise

    async def _claim_loop(self, kinds: frozenset[str]) -> None:
        while not self._stop.is_set():
            work = await self._claim_next(kinds)
            if work is None:
                await self._store.expire_leases()
                await asyncio.sleep(0.5)
                continue
            try:
                await self._execute(work)
            except asyncio.CancelledError:
                # The cancel can land BETWEEN the committed claim and
                # `_execute`'s own CancelledError handler — before that
                # handler ever ran. The row is `running` under this owner
                # either way; repark here too so a graceful stop doesn't
                # leave it owned-but-dead until lease expiry. The fenced
                # `_repark` is a no-op if `_execute` already reparked.
                await self._repark(work, seconds=0, reason="cancelled")
                raise

    async def _claim_next(self, kinds: frozenset[str]) -> BridgeWork | None:
        """`SqlBridgeStore.claim_next` scoped to one lane's kinds — the same
        atomic claim (earliest due non-terminal row, `SKIP LOCKED`,
        generation bump, attempts++, lease stamp), filtered so a comprehend
        lane never takes a speak row and vice versa. Claim-then-repark is
        NOT a substitute: a wrong-lane claim burns a generation and an
        attempt count the work never ran."""
        # The claim runs as its own shielded task: a cancel delivered while
        # its COMMIT is in flight stops at the shield, so the commit lands
        # or rolls back on its own terms and the task holds the receipt.
        # Awaiting `claim_next` directly would propagate the cancel INTO
        # the commit — the outcome then resolves server-side with the
        # receipt lost, and a committed `running` row is orphaned under a
        # dead owner until lease expiry.
        claim = asyncio.ensure_future(self._store.claim_next(
            owner=self._owner, lease_seconds=self._lease, kinds=kinds))
        try:
            return await asyncio.shield(claim)
        except asyncio.CancelledError:
            # Recover the real receipt: the shield took the cancel, `claim`
            # kept running. A committed claim comes back as the work to
            # repark; a rolled-back one answers None or raises — nothing
            # to repark either way, and lease expiry is the backstop for
            # the genuinely ambiguous outcome.
            work: BridgeWork | None = None
            try:
                work = await claim
            except Exception as exc:  # noqa: BLE001 — the commit's own fault; the bounded type name is all the record needs
                log.warning("bridge claim faulted under cancel (%s); "
                            "no receipt to repark", type(exc).__name__)
            if work is not None:
                await self._repark(work, seconds=0, reason="cancelled")
            raise

    async def _execute(self, work: Any) -> None:
        key = (work.platform, work.conversation_id)
        attempt_id = bt.attempt_identity(work.id, work.generation)
        carrier = _payload_trace(work.payload)
        producer_ctx = bt.remote_context(
            carrier.get("traceparent"), carrier.get("tracestate"))
        prev_ctx = bt.attempt_link(bt.last_attempt(work.result))
        wait_ms = _queue_wait_ms(work.payload)
        started_ms = _now_ms()
        # The queue.attempt root is FRESH (empty parent, never activated) —
        # it links to the producer and the previous attempt, and it ends
        # with this attempt, not with the phase's downstream work.
        with bt.attempt_span(
                platform=work.platform, work_kind=work.kind,
                event_id=work.event_id or "", attempt_id=attempt_id,
                generation=work.generation,
                producer=producer_ctx, previous_attempt=prev_ctx,
                stream_id=work.payload.get("stream_id"),
                queue_wait_ms=wait_ms,
                tag_id=work.payload.get("tag_id")) as sp:
            rec = bt.attempt_record(
                generation=work.generation, attempt_id=attempt_id,
                ref=bt.span_ref(sp), started_ms=started_ms)
            write = await bt.record_attempt_started(
                self._store._engine, work_id=work.id,
                generation=work.generation, record=rec)
            if write == "fenced":
                # The row moved to a newer attempt between claim and this
                # write — the live owner proceeds; this claim is stale.
                log.warning("work %s: attempt %s fenced at start",
                            work.id, attempt_id)
                return
            # "unavailable" = telemetry loss only: the work runs untraced,
            # never cancelled for it (the helper's contract).
            self._attempts[work.id] = (work, rec, started_ms)
            if wait_ms is not None:
                bt.observe_queue_wait(self._metrics, platform=work.platform,
                                      wait_ms=float(wait_ms))
            bt.lifecycle(self._metrics, "attempt_started",
                         platform=work.platform,
                         event_id=work.event_id or "", attempt_id=attempt_id,
                         attempts=work.attempts, queue_wait_ms=wait_ms,
                         stream_id=work.payload.get("stream_id"))
            phase = asyncio.ensure_future(self._dispatch(work, key))
            heartbeat = asyncio.ensure_future(
                self._lease_heartbeat(work, key))
            try:
                done, _ = await asyncio.wait(
                    (phase, heartbeat), return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done:
                    # Lease renewal fenced out (reclaim moved the generation)
                    # or a held lock's connection died mid-phase: halt BEFORE
                    # any future effect. Repark FIRST — bumping the generation
                    # while we still hold it fences out every write the dying
                    # phase's unwind could still attempt; if a reclaim already
                    # owns a newer generation the repark is a fenced no-op.
                    heartbeat.exception()  # consume: _LeaseLost, never a raise
                    await self._repark(work, seconds=0, reason="lease_lost")
                    phase.cancel()
                    with contextlib.suppress(asyncio.CancelledError,
                                             Exception):
                        await phase
                    return
                heartbeat.cancel()
                await phase  # its result, or its exception into the handlers
            except asyncio.CancelledError:
                # Runner stop is not completion: return to pending for reclaim.
                await self._repark(work, seconds=0, reason="cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 — the phase boundary must convert ANY fault into a typed terminal/retryable state, not a lost task
                # type name only — an SDK/DB message can carry identifiers or
                # secrets; the bounded record answers "what kind of failure".
                log.warning("bridge work %s (%s) faulted (%s)",
                            work.id, work.kind, type(exc).__name__)
                state = ("failed_retained" if work.attempts >= 5
                         else "retryable_failure")
                await self._finish(work, state,
                                   {"error": type(exc).__name__})
            finally:
                self._attempts.pop(work.id, None)
                for task in (phase, heartbeat):
                    if not task.done() and task.cancelling() == 0:
                        task.cancel()
                # Drain, don't detach: an unwind that outlives this return
                # could still reach the DB — its fenced writes no-op now,
                # but only the drain guarantees it is actually finished.
                await asyncio.gather(phase, heartbeat,
                                     return_exceptions=True)

    async def _dispatch(self, work: Any, key: tuple[str, str]) -> None:
        if work.kind == "ingest":
            await self._run_ingest(work)
        elif work.kind == "turn":
            # Both halves: the shared in-memory slot (fires and admissions
            # see it) and the DB lock (zombie/recreate peers see that).
            if not self._slots.try_acquire(key):
                await self._repark(work, seconds=2, reason="speaking_slot_busy")
                return
            if not await self._db_locks.try_acquire(key):
                self._slots.release_and_dispatch(key)
                await self._repark(work, seconds=2, reason="speaking_lock_busy")
                return
            try:
                if not await self._db_locks.lock_alive(key):
                    raise RuntimeError("speaking lock lost before turn")
                await self._run_turn(work)
            finally:
                await self._db_locks.release(key)
                self._slots.release_and_dispatch(key)
        elif work.kind == "participation":
            await self._run_participation(work)
        elif work.kind == "participation_fire":
            await self._run_participation_fire(work)
        elif work.kind == "memory_feed":
            await self._run_memory_feed(work)
        elif work.kind == "stimulus":
            await self._run_stimulus(work)
        elif work.kind == "receipt":
            await self._run_receipt(work)
        elif work.kind == "trace_debrief":
            await self._run_debrief(work)
        else:
            await self._finish(work, "failed_retained",
                               {"reason": f"unhandled kind {work.kind}"})

    async def _lease_heartbeat(self, work: Any, key: tuple[str, str]) -> None:
        """Renew the claimed lease for the phase's whole run — the liveness
        the `running` state alone cannot show (a hung owner's row would
        otherwise look identical to a live long turn). Renewals are fenced
        by the same tuple the claim and finish use — (id, generation,
        lease_owner, state='running') — so a row reclaimed to a newer
        generation can never be kept alive by its stale owner, and a dead
        owner's row stops renewing and expires on schedule. `lease/4`
        between renewals gives a legitimate long turn three chances before
        lapse — and keeps a 1-second lease honest, where a 1-second floor
        would let the first renewal race the expiry itself. The heartbeat
        starts and stops with the phase.
        """
        interval = max(0.25, self._lease / 4)
        while True:
            await asyncio.sleep(interval)
            if not await self._renew_lease(work):
                raise _LeaseLostError(work.id)
            if (work.kind in ("turn", "participation_fire")
                    and not await self._db_locks.lock_alive(key)):
                raise _LeaseLostError(work.id)

    async def _renew_lease(self, work: Any) -> bool:
        """The heartbeat write — `SqlBridgeStore.renew_lease`, the same
        (id, generation, owner, running, live-lease) fence the claim and
        finish use."""
        try:
            return await self._store.renew_lease(
                work.id, generation=work.generation, owner=self._owner,
                lease_seconds=self._lease)
        except sa.exc.SQLAlchemyError:
            return False

    async def _repark(self, work: Any, *, seconds: float,
                      reason: str = "retry") -> None:
        """Return a claimed row to `pending` WITHOUT handing this attempt's
        ownership forward: the generation bump fences out every later write
        that still carries this attempt's generation — a detached phase that
        swallowed its cancel can never `_finish` over the repark, and the
        `state='running'` predicate means the repark can never overwrite a
        finish that committed first. Repark-vs-finish is strictly ordered:
        whoever commits first owns the row."""
        async with self._store._engine.begin() as conn:
            await conn.execute(sa.text(
                "UPDATE bridge_work SET state='pending',"
                " generation = generation + 1,"
                " due_at = now() + make_interval(secs => :s),"
                " lease_owner=NULL, leased_until=NULL, updated_at=now()"
                " WHERE id = :id AND generation = :g"
                " AND state = 'running'"),
                {"s": seconds, "id": work.id, "g": work.generation})
        bt.lifecycle(self._metrics, "deferred", platform=work.platform,
                     event_id=work.event_id or "", reason=reason,
                     attempt_id=bt.attempt_identity(work.id, work.generation))

    def _attempt_patch(self, work: Any, state: str,
                       result: dict[str, Any] | None) -> dict[str, Any]:
        """`result` plus the terminal attempt record — the fragment every
        fenced finish write merges, so the attempt that STARTED is the
        attempt the receipt shows COMPLETED (or shows only started, if the
        process dies between them)."""
        patch = dict(result or {})
        attempt = self._attempts.get(work.id)
        if attempt is not None and attempt[0] is work:
            patch.update(bt.attempt_finished_fields(
                work.generation, record=attempt[1], outcome=state,
                finished_ms=_now_ms(),
                reason=(patch.get("reason") or patch.get("outcome"))))
        return patch

    def _emit_terminal(self, work: Any, state: str) -> None:
        """Metrics + the bounded lifecycle line for a committed finish —
        emitted AFTER the durable write, so a count never outruns the
        receipt that proves it."""
        if state not in _STATE_EVENT:
            return
        attempt = self._attempts.get(work.id)
        started = attempt[1].get("started_ms") if attempt else None
        duration = (_now_ms() - int(started)) if started else None
        bt.attempt_metrics(self._metrics, platform=work.platform,
                           kind=work.kind, outcome=state,
                           duration_ms=duration)
        bt.lifecycle(self._metrics, _STATE_EVENT[state],
                     platform=work.platform,
                     event_id=work.event_id or "", outcome=state,
                     attempt_id=bt.attempt_identity(work.id, work.generation),
                     duration_ms=duration)

    async def _finish(self, work: Any, state: str,
                      result: dict[str, Any] | None = None) -> bool:
        """Fenced terminal write + the merged attempt record in the SAME
        transaction. Returns whether the write won the fence — callers ACK
        only on True: a stale generation's commit never happened, and the
        live owner discharges (and ACKs) the entry."""
        async with self._store._engine.begin() as conn:
            ok = await self._store.finish_work(
                conn, work.id, generation=work.generation,
                state=state,
                result=self._attempt_patch(work, state, result))
        if not ok:
            log.warning("work %s: completion lost the fence (stale generation)",
                        work.id)
            return False
        self._emit_terminal(work, state)
        return True

    async def _ack(self, work: Any) -> None:
        """XACK the stream entry this work carries — only reached AFTER the
        durable commit that discharges it. Never in a finally. A failed ACK
        leaves the entry pending for reclaim (dedupe makes replay safe) —
        it must NOT rewrite the committed terminal state, so the error is
        observed and swallowed here, not re-raised into `_finish`."""
        stream, sid = work.payload.get("stream"), work.payload.get("stream_id")
        if not (stream and sid):
            return
        links: list[Link] = []
        attempt = self._attempts.get(work.id)
        if attempt is not None and attempt[0] is work:
            links = _link(bt.attempt_link(attempt[1]))
        try:
            with bt.operation_span("ack", platform=work.platform,
                                   links=links, event_id=work.event_id or "",
                                   stream_id=sid):
                await self._redis.xack(stream, CONSUMER_GROUP, sid)
        except Exception:  # noqa: BLE001 — durable state stands; pending entry is the recovery path
            bt.lifecycle(self._metrics, "ack_failed",
                         platform=work.platform,
                         event_id=work.event_id or "", stream_id=sid)
            log.warning("work %s: xack failed; entry stays pending for "
                        "reclaim", work.id)
            return
        bt.lifecycle(self._metrics, "acked", platform=work.platform,
                     event_id=work.event_id or "", stream_id=sid)

    # ---- ingest ---------------------------------------------------------

    async def _run_ingest(self, work: Any) -> None:
        from nora.domain.models import PersonRef

        envelope = Envelope.from_fields(work.payload["envelope"])
        async with self._store.advisory_lock(
                INGEST_NS, work.platform, work.account_key,
                work.conversation_id) as held:
            if not held:
                await self._repark(work, seconds=1)
                return
            event = await self._store.event(envelope.event_id)
            payload = event["payload"] if event is not None else {}
            if payload.get("message_row_id") is not None:
                # Crash-window replay: the audit commit already landed and the
                # handoff snapshot committed WITH it — resume entirely from
                # committed truth. No re-ingest (a second message row), no
                # gate call (a second admission), no re-parse (the snapshot
                # carries the custody-enriched message).
                handoff = payload.get("handoff") or {}
                if "decision" not in handoff:
                    # A row committed before the snapshot shape existed: the
                    # identity of what was decided is unrecoverable truth —
                    # retain it, never guess or re-admit.
                    if await self._finish(
                            work, "failed_retained",
                            {"reason": "committed row predates handoff snapshot"}):
                        await self._ack(work)
                    return
                decision = EngageDecision(**handoff["decision"])
                result_message = message_from_dict(handoff["message"])
                person = (PersonRef(**handoff["person"])
                          if handoff.get("person") else None)
                absorbed = bool(handoff.get("absorbed", True))
                unit = handoff.get("unit")
                row_id = handoff["row_id"]
            else:
                message = message_from_dict(envelope.message)

                async def hook(conn: Any, row_id: int, context: Any) -> None:
                    # The COMPLETE canonical handoff, committed inside the
                    # audit row's own transaction: the custody-enriched
                    # message, its row id, the resolved person, the
                    # admission-time absorption fact and the gate decision
                    # (under `durable` the pipeline gates before the audit for
                    # exactly this). A kill after this commit resumes from
                    # this snapshot alone.
                    handoff = {
                        "message": message_to_dict(context["message"]),
                        "row_id": row_id,
                        "person": _person_dict(context["person"]),
                        "absorbed": context["absorbed"],
                        "decision": _decision_dict(context["decision"]),
                        "unit": context.get("unit"),
                    }
                    await conn.execute(sa.text(
                        "UPDATE bridge_event SET payload = payload || :patch,"
                        " updated_at = now() WHERE id = :eid"),
                        {"patch": json.dumps(
                            {"message_row_id": row_id, "handoff": handoff}),
                         "eid": envelope.event_id})
                result = await self._ingest.run(message=message, tx_hook=hook,
                                                durable=True)
                if result.outcome in FAILURE_OUTCOMES:
                    # A swallowed audit failure is a typed failure, never a skip.
                    state = ("retryable_failure" if work.attempts < 5
                             else "failed_retained")
                    ok = await self._finish(
                        work, state, {"outcome": result.outcome})
                    if ok and state == "failed_retained":
                        # Terminal AND nothing downstream owns the event —
                        # discharge the entry now, not on a 30s reclaim.
                        await self._ack(work)
                    return
                if result.message is None or result.decision is None:
                    reason = result.outcome or "unrecorded"
                    state = ("intentional_skip"
                             if reason in SILENT_OUTCOMES else "failed_retained")
                    async with self._store._engine.begin() as conn:
                        await self._store.set_event_state(
                            conn, envelope.event_id,
                            "intentional_skip" if state == "intentional_skip"
                            else "failed_retained", reason=reason)
                        ok = await self._store.finish_work(
                            conn, work.id, generation=work.generation,
                            state=state, result=self._attempt_patch(
                                work, state, {"outcome": reason}))
                    if ok:
                        self._emit_terminal(work, state)
                        await self._ack(work)
                    return
                decision = result.decision
                result_message = result.message
                person = result.person
                absorbed = result.absorbed
                unit = result.unit
                row_id = result.row_id
            # Obligations commit in ONE transaction, then XACK — durable
            # downstream ownership precedes the ACK, per the approved baseline.
            handed_on = _now_ms()
            downstream = {"trace": envelope.trace, "enqueued_ms": handed_on}
            async with self._store._engine.begin() as conn:
                await self._store.set_event_state(
                    conn, envelope.event_id, "done",
                    admission={"engage": decision.engage,
                               "reason": decision.reason,
                               "relevance_gate": decision.relevance_gate})
                ordinal = 1
                if decision.engage:
                    tag_id = uuid.uuid4().hex[:12]
                    await self._store.set_event_state(
                        conn, envelope.event_id, "done", tag_id=tag_id)
                    await self._store.enqueue_work(
                        conn, event_id=envelope.event_id, kind="turn",
                        ordinal=ordinal, platform=work.platform,
                        account_key=work.account_key,
                        conversation_id=work.conversation_id,
                        payload={
                            "tag_id": tag_id,
                            "stream": work.payload.get("stream"),
                            "stream_id": work.payload.get("stream_id"),
                            "message": message_to_dict(result_message),
                            "decision": {
                                "engage": decision.engage,
                                "reason": decision.reason,
                                "rationale": decision.rationale,
                                "confidence": decision.confidence,
                                "threshold": decision.threshold,
                                "allow_episode": decision.allow_episode,
                                "relevance_gate": decision.relevance_gate,
                            },
                            "admission": None,
                            **downstream,
                        })
                    ordinal += 1
                if decision.relevance_gate and self._participation is not None:
                    # The dispatcher's `note_message` never fires under bridge
                    # mode (no live listener), so the durable obligation owns it.
                    await self._store.enqueue_work(
                        conn, event_id=envelope.event_id,
                        kind="participation", ordinal=ordinal,
                        platform=work.platform,
                        account_key=work.account_key,
                        conversation_id=work.conversation_id,
                        payload={"note_message": message_to_dict(result_message),
                                 **downstream})
                    ordinal += 1
                if self._memory_feed is not None and person is not None:
                    await self._store.enqueue_work(
                        conn, event_id=envelope.event_id,
                        kind="memory_feed", ordinal=ordinal,
                        platform=work.platform,
                        account_key=work.account_key,
                        conversation_id=work.conversation_id,
                        payload={"message": message_to_dict(result_message),
                                 "person": _person_dict(person),
                                 "absorbed": absorbed, "unit": unit,
                                 **downstream})
                    ordinal += 1
                if self._stimulus is not None and row_id is not None:
                    await self._store.enqueue_work(
                        conn, event_id=envelope.event_id,
                        kind="stimulus", ordinal=ordinal,
                        platform=work.platform,
                        account_key=work.account_key,
                        conversation_id=work.conversation_id,
                        payload={"message": message_to_dict(result_message),
                                 "row_id": row_id,
                                 "person_id": person.person_id
                                 if person else None,
                                 "absorbed": absorbed, "unit": unit,
                                 **downstream})
                    ordinal += 1
                # The queued receipt is BRIDGE-owned: the producer emits it
                # once per canonical event at enqueue, so the app never
                # enqueues a second receipt workflow. A terminal-phase
                # reaction (the turn's ✅) is a DIFFERENT action; the bridge's
                # single action ID per canonical event is what makes the
                # queued receipt one effect across retries and rotation.
                ok = await self._store.finish_work(
                    conn, work.id, generation=work.generation,
                    state="done", result=self._attempt_patch(work, "done", {
                        "outcome": "engaged" if decision.engage else "recorded",
                        "reason": decision.reason}))
        if ok:
            self._emit_terminal(work, "done")
            await self._ack(work)

    # ---- turn --------------------------------------------------------------

    async def _run_turn(self, work: Any) -> None:
        payload = work.payload
        owner = ActionOwner(kind="work", id=f"w-{work.id}",
                            generation=work.generation)
        # No blind resend/replay: an earlier attempt that RESERVED or STARTED
        # an action may already have effected it — retain uncertainty instead
        # of running a second model turn.
        prior = await self._store.owner_actions("work", f"w-{work.id}")
        if any(a["state"] in ("started", "confirmed", "uncertain") for a in prior):
            if await self._finish(
                    work, "uncertain",
                    {"reason": "prior attempt started an action"}):
                await self._ack(work)
            return
        persisted = (work.result or {}).get("admission")
        request = EngagementRequest(
            message=message_from_dict(payload["message"]),
            decision=EngageDecision(**payload["decision"]),
            tag_id=payload["tag_id"],
        )
        adapter = self._adapters.get(work.platform)
        bound = (adapter.bind_owner(owner)
                 if adapter is not None and hasattr(adapter, "bind_owner")
                 else adapter)

        async def sink(verdict: str) -> None:
            # AWAITED inside run_durable before any effect: the verdict is
            # durable before the turn may send.
            async with self._store._engine.begin() as conn:
                await self._store.record_admission(
                    conn, work.id, generation=work.generation,
                    admission={"verdict": verdict})

        queue_link = None
        attempt = self._attempts.get(work.id)
        if attempt is not None and attempt[0] is work:
            queue_link = {
                "attempt": attempt[1],
                "event_id": work.event_id or "",
                "attempt_id": bt.attempt_identity(work.id, work.generation),
                "queue_wait_ms": _queue_wait_ms(work.payload),
            }
        disposition = await self._engagement.run_durable(
            request, bound_adapter=bound, persisted_admission=persisted,
            admission_sink=sink, queue_link=queue_link)
        # A vetoed turn is terminal the same way a silent one is — the guards
        # refused, and a re-run would pay a model call to refuse again.
        if disposition in ("send_reply", "recovered", "silent", "vetoed"):
            state, result = "done", {"tag_id": payload["tag_id"],
                                     "disposition": disposition}
        elif disposition in ("rejected", "queue_full"):
            state, result = "intentional_skip", {"admission": disposition}
        else:
            # llm_error/handler_error: retried while attempts remain; the
            # started-action fence above keeps a reclaim from replaying a
            # turn whose effect may already have landed.
            state = ("failed_retained" if work.attempts >= 5
                     else "retryable_failure")
            result = {"disposition": disposition}
        ok = await self._finish(work, state, result)
        if ok and state in WORK_TERMINAL:
            # The ingest handoff already discharged the entry in the common
            # case — this is the early recovery when THAT ack was lost. xack
            # on an already-discharged id is a no-op; a retryable_failure
            # (still an open obligation) never reaches it.
            await self._ack(work)

    # ---- deferred kinds -----------------------------------------------------

    async def _run_participation(self, work: Any) -> None:
        """A persisted participation candidate: the durable mark first, then
        the in-memory note, so a kill between them replays as already-counted
        rather than as a phantom second member."""
        if self._participation is None:
            await self._finish(work, "intentional_skip",
                               {"reason": "participation off"})
            return
        mark = await self._store.mark_candidate(work.id, noted_ms=_now_ms())
        if mark != "folded":
            # A candidate a window already folded must not count again —
            # membership in two windows is the double-turn the mark guards.
            # ponytail: a close committing between this read and the count
            # can still race one in; only during app-replacement overlap, and
            # the harm is one extra gate evaluation, never a lost candidate.
            self._participation.note_candidate(
                work.id, message_from_dict(work.payload["note_message"]))
        await self._finish(work, "done", {"noted": True, "mark": mark})

    def _current_room(self, work: Any, absorbed: bool) -> tuple[Any, str | None]:
        """The deferred-effect permission recheck: `(room, None)` when the
        effect may still run, else `(None, reason)`. The message must have
        been absorbed at admission AND the room must still be admitted,
        absorbed and bound to the SAME unit under the allowlist as it stands
        NOW — any stale or unknown answer refuses, never grants. A moved unit
        is refused, not rerouted: the obligation's scope was pinned at
        admission, and old content landing in a new unit is a disclosure,
        not a delay."""
        if not absorbed or self._allowlist is None:
            return None, "room not absorbed"
        room = self._allowlist.conversation(work.platform, work.conversation_id)
        if room is None:
            return None, "room not listed"
        if not room.absorbed:
            return None, "room not absorbed"
        if room.unit != work.payload.get("unit"):
            return None, "unit binding moved"
        return room, None

    async def _run_memory_feed(self, work: Any) -> None:
        if self._memory_feed is None:
            await self._finish(work, "intentional_skip",
                               {"reason": "memory feed off"})
            return
        from nora.domain.models import PersonRef
        from nora.ingest.stages import memory_feed as memory_feed_stage
        wiring = self._memory_feed
        person = PersonRef(**work.payload["person"])
        # A committed start mark answers BEFORE permission does: once a POST
        # may already have landed, the open question is the effect, not
        # current policy — `feed` has no idempotency key, so the honest
        # terminal is uncertain, never a blind repost or a clean skip.
        if ((work.result or {}).get("effect", {}).get("feed", {})
                .get("started_ms") is not None):
            await self._finish(
                work, "uncertain",
                {"reason": "feed started before owner lost the work"})
            return
        # CURRENT permission, minutes after enqueue: the room must still be
        # absorbed under the SAME unit binding, and the sender must still
        # resolve to the same Honcho peer — a revoked mapping or a moved
        # scope can never be fed under the stale snapshot.
        room, refuse = self._current_room(
            work, bool(work.payload.get("absorbed", True)))
        if room is None:
            await self._finish(work, "intentional_skip", {"reason": refuse})
            return
        current = (self._persons.resolve(
            work.platform, work.payload["message"].get("sender_id", ""))
                   if self._persons is not None else None)
        if (current is None or current.person_id != person.person_id
                or current.honcho_peer_id != person.honcho_peer_id):
            await self._finish(work, "intentional_skip",
                               {"reason": "sender unmapped or changed"})
            return
        started = False

        async def _mark_started() -> None:
            # The fenced pre-POST commit: generation-checked, so a stale
            # claimant raises out of `_feed` before the request exists.
            nonlocal started
            if not await self._store.mark_effect_started(
                    work.id, work.generation, kind="feed", at_ms=_now_ms()):
                raise _LeaseLostError(work.id)
            started = True

        # Durable completion, not fire-and-forget: collect the stage's
        # scheduled coroutines and AWAIT each before finishing the work —
        # a create_task here would let the obligation report done while the
        # feed dies with the process.
        pending: list[Any] = []
        await memory_feed_stage.run(
            message=message_from_dict(work.payload["message"]),
            person=person,
            memory=wiring["memory"], metrics=self._metrics,
            spawn=lambda coro: pending.append(coro), absorbed=True,
            on_effect_start=_mark_started)
        if not pending:
            # The stage declined on its own terms (self-authored, unmapped) —
            # a legitimate non-effect, not a swallowed failure.
            await self._finish(work, "intentional_skip",
                               {"reason": "stage declined"})
            return
        try:
            outcomes = [await coro for coro in pending]
        except _LeaseLostError:
            # The generation fence moved mid-attempt — the row's new owner
            # resolves it; finishing here would write under a dead lease.
            return
        except Exception:  # noqa: BLE001 — the fault classifies on whether the POST boundary was crossed, not on its type
            if started:
                # The mark committed, then something raised — the POST may
                # have landed. Retain, never replay.
                await self._finish(work, "failed_retained",
                                   {"reason": "feed started"})
                return
            # A raise before the POST boundary (ensure_peer, the mark
            # itself) is pre-effect: retry while attempts remain.
            state = ("failed_retained" if work.attempts >= 5
                     else "retryable_failure")
            await self._finish(work, state, {"reason": "feed raised"})
            return
        if all(o == "fed" for o in outcomes):
            await self._finish(work, "done", {"fed": len(outcomes)})
            return
        # `feed` POSTs without an idempotent key: a False may mean the post
        # landed and the response was lost. Blind replay could double-feed,
        # so the honest terminal is retained-uncertain, not retry.
        await self._finish(work, "failed_retained",
                           {"reason": "feed unconfirmed"})

    async def _run_stimulus(self, work: Any) -> None:
        if self._stimulus is None:
            await self._finish(work, "intentional_skip",
                               {"reason": "stimulus off"})
            return
        from nora.ingest.stages import stimulus as stimulus_stage
        room, refuse = self._current_room(
            work, bool(work.payload.get("absorbed", True)))
        if room is None:
            await self._finish(work, "intentional_skip", {"reason": refuse})
            return
        outcome, sid, reason = await stimulus_stage.run_typed(
            message=message_from_dict(work.payload["message"]),
            item="message", row_id=work.payload.get("row_id"),
            person_id=work.payload.get("person_id"),
            wiring=self._stimulus, metrics=self._metrics,
            recorded_at_ms=work.payload.get("enqueued_ms"),
            dedupe=True)
        if outcome == "written":
            await self._finish(work, "done", {"stimulus_id": sid})
        elif outcome == "skipped":
            await self._finish(work, "intentional_skip", {"reason": reason})
        else:
            state = ("failed_retained" if work.attempts >= 5
                     else "retryable_failure")
            await self._finish(work, state, {"reason": reason or "failed"})

    async def _run_participation_fire(self, work: Any) -> None:
        """The claimed window turn: persisted admission first, then the same
        slot/lock/cooldown/speak sequence the live trigger runs — owned by the
        work's generation so a kill can never orphan it."""
        from nora.app.fire import participation_occasion

        if self._participation is None or self._fire_speak is None:
            await self._finish(work, "failed_retained",
                               {"reason": "fire collaborator missing"})
            return
        key = (work.platform, work.conversation_id)
        owner = ActionOwner(kind="work", id=f"w-{work.id}",
                            generation=work.generation)
        prior = await self._store.owner_actions("work", f"w-{work.id}")
        if any(a["state"] in ("started", "confirmed", "uncertain")
               for a in prior):
            if await self._finish(
                    work, "uncertain",
                    {"reason": "prior attempt started an action"}):
                await self._ack(work)
            return
        persisted = (work.result or {}).get("admission")
        if persisted is None:
            # The gate runs ONCE and its verdict is committed before any
            # effect: a kill between admission and speak resumes with the
            # same answer, never a second model call.
            admitted, score, verdict = await self._participation.admitted(
                work.platform, work.conversation_id)
            async with self._store._engine.begin() as conn:
                await self._store.record_admission(
                    conn, work.id, generation=work.generation,
                    admission={"admitted": admitted, "score": score,
                               "verdict": verdict})
            persisted = {"admitted": admitted, "score": score,
                         "verdict": verdict}
        admitted = bool(persisted.get("admitted"))
        score = persisted.get("score")
        verdict = persisted.get("verdict")
        if not admitted:
            # NO-GO or the gate's fail-closed answer: the window was judged,
            # the turn is legitimately not owed.
            if await self._finish(work, "intentional_skip",
                                  {"admission": "no_go"}):
                await self._ack(work)
            return
        if await self._participation.shadow_verdict(
                platform=work.platform, conversation_id=work.conversation_id,
                score=score):
            if await self._finish(work, "done", {"outcome": "shadowed"}):
                await self._ack(work)
            return
        # A tag never waits: busy is an intentional skip (the window is
        # spent), exactly the live trigger's semantics — not a repark.
        if not self._slots.try_acquire(key):
            await self._finish(work, "intentional_skip",
                               {"reason": "speaking_slot_busy"})
            return
        if not await self._db_locks.try_acquire(key):
            self._slots.release_and_dispatch(key)
            await self._finish(work, "intentional_skip",
                               {"reason": "speaking_lock_busy"})
            return
        try:
            if not await self._db_locks.lock_alive(key):
                raise RuntimeError("speaking lock lost before fire")
            last_send = (
                await self._participation_state.last_send_at(
                    platform=work.platform, conversation_id=work.conversation_id)
                if self._participation_state is not None else None)
            now_s = _now_ms() / 1000.0
            send_permitted = (
                last_send is None
                or now_s - last_send / 1000.0
                >= self._participation.cooldown_seconds)
            adapter = self._adapters.get(work.platform)
            bound = (adapter.bind_owner(owner)
                     if adapter is not None and hasattr(adapter, "bind_owner")
                     else adapter)
            outcome = await self._fire_speak(
                participation_occasion(
                    platform=work.platform, conversation_id=work.conversation_id,
                    conversation_type=work.payload.get("conversation_type",
                                                       "group"),
                    relevance_score=score,
                ),
                send_permitted=send_permitted,
                gate_verdict=verdict, gate_score=score,
                adapter=bound)
            if outcome == "sent":
                # The send and the cooldown cursor retire together; the in-
                # memory mirror follows the committed write.
                async with self._store._engine.begin() as conn:
                    await self._store.send_cursor_upsert(
                        conn, platform=work.platform,
                        conversation_id=work.conversation_id,
                        at_ms=_now_ms())
                    ok = await self._store.finish_work(
                        conn, work.id, generation=work.generation,
                        state="done", result=self._attempt_patch(
                            work, "done", {"outcome": "sent"}))
                if ok:
                    self._participation.mark_sent(key)
                    self._emit_terminal(work, "done")
                    await self._ack(work)
            else:
                # Held, silent or failed: the room heard nothing, no cooldown
                # is spent — the same reading the live trigger makes.
                if await self._finish(work, "done", {"outcome": outcome}):
                    await self._ack(work)
        finally:
            await self._db_locks.release(key)
            self._slots.release_and_dispatch(key)

    async def _run_receipt(self, work: Any) -> None:
        """Legacy obligation rows only — the queued receipt is BRIDGE-owned
        (emitted once per canonical event at enqueue). App-side receipt work
        is a duplicate workflow and must not send a second reaction; already
        persisted rows drain as intentional skips."""
        await self._finish(work, "intentional_skip",
                           {"reason": "receipt is bridge-owned"})

    async def _run_debrief(self, work: Any) -> None:
        if self._debrief is None:
            # A persisted obligation means debrief was ENABLED at enqueue time
            # — "off" here is a missing collaborator, a failure to surface,
            # not a silence to bless.
            await self._finish(work, "failed_retained",
                               {"reason": "debrief collaborator missing"})
            return
        await self._debrief(work.payload)
        await self._finish(work, "done", {})

    def stop(self) -> None:
        self._stop.set()
