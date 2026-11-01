"""Codex round-1 blockers 1, 4 and 5 — the deferred-effect seam.

Every test drives the production handoff against REAL Postgres + Redis:
`StreamProducer.enqueue` → `StreamReader._register` → `WorkRunner._execute`.
The pipelines are the harness's real `IngestPipeline`; allowlist, persons,
participation state and the speaking slot are the harness's real objects.
Only Honcho, the stimulus store and `FirePath.speak` are doubles — the
shared `nora.testing` fakes or local doubles on the same contracts.

Blocker 1 — deferred memory/stimulus must carry the admission-time
absorption fact AND re-check current policy (room absorbed, room listed,
sender still mapped to the same peer) before the effect runs.

Blocker 4 — a kill between the audit commit and the downstream-obligation
commit must resume from the snapshot the audit transaction persisted, never
re-ingest, never re-run the gate, and never hit `UnboundLocalError`.

Blocker 5 — participation candidates are durable ledger membership: the
note, the window close and the fire all survive a hard kill, and nothing is
reconstructed from all-message history.
"""

import asyncio
import contextlib
import sys
import time
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.app.bridge_queue import DbSpeakingLocks, StreamReader, WorkRunner, stream_name
from nora.app.participation import ParticipationTrigger
from nora.domain.actions import ActionOwner
from nora.domain.media import MediaRef
from nora.domain.models import NormalizedMessage
from nora.engagement.slots import ConversationSlots
from nora.infra.honcho.client import HonchoClient
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.participation_state import SqlParticipationStateStore
from nora.infra.sql.schema import (
    BRIDGE_EVENT,
    BRIDGE_WORK,
    MESSAGES,
    PARTICIPATION_WINDOW_STATE,
    STIMULUS,
)
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.ingest.relevance_gate import GateVerdict
from nora.ingest.stages.stimulus import Wiring
from nora.platform.bridge.envelope import Envelope, canonical_source_key, event_id
from nora.platform.bridge.queue import CONSUMER_GROUP, StreamProducer, ensure_group
from nora.platform.bridge.rehydrate import message_to_dict
from nora.policy import Conversation
from nora.testing import FakeMemoryStore, ScriptedTurn
from tests.fakes import FakeHoncho

pytestmark = pytest.mark.integration

KEY = b"test-correlation-key"
PLATFORM, ACCOUNT = "signal", "+85290000100"
# The harness identity.toml maps +852111 to person `miles`; +00000000002 is
# unmapped — both generated ids inside the declared pseudonym range.
MAPPED_SENDER = "+852111"
UNMAPPED_SENDER = "+00000000002"
ROOM = "g1"          # a listed (absorbed) room in the harness policy.toml


def _msg(body: str, ts: int, *, sender: str = UNMAPPED_SENDER,
         conversation_id: str = ROOM,
         media: tuple[MediaRef, ...] = ()) -> dict:
    return message_to_dict(NormalizedMessage(
        platform=PLATFORM, conversation_id=conversation_id,
        conversation_type="group", sender_id=sender, sender_name="Sender",
        timestamp=ts, body=body, media=media,
        raw={"dataMessage": {"message": body}}, message_id=f"m{ts}"))


def _envelope(body: str, ts: int, *, sender: str = UNMAPPED_SENDER,
              conversation_id: str = ROOM,
              media: tuple[MediaRef, ...] = ()) -> Envelope:
    sk = canonical_source_key(
        PLATFORM, ACCOUNT, conversation_id, "message", f"{ts}")
    return Envelope(
        platform=PLATFORM, account_key=ACCOUNT, kind="message",
        source_key=sk, event_id=event_id(sk, key=KEY, key_version=1),
        conversation_id=conversation_id, conversation_type="group",
        sender_id=sender, sender_name="Sender", sent_ms=ts,
        received_ms=ts + 1, queued_ms=ts + 2, time_quality="upstream",
        message=_msg(body, ts, sender=sender, conversation_id=conversation_id,
                     media=media),
        admission_rev=1)


@pytest.fixture
async def bridge(pg_url, redis_client):
    store = SqlBridgeStore(create_async_engine(pg_url))
    stream = stream_name(PLATFORM, ACCOUNT)
    await ensure_group(redis_client, stream)
    yield store, redis_client, stream
    await store._engine.dispose()


def _runner(store, redis_client, harness, **kw) -> WorkRunner:
    pipeline = harness.handler._dispatcher._ingest
    kw.setdefault("allowlist", pipeline._allowlist)
    kw.setdefault("persons", pipeline._persons)
    kw.setdefault("slots", harness._engagement._slots)
    return WorkRunner(
        store=store, redis=redis_client,
        ingest=pipeline, engagement=harness._engagement,
        db_locks=DbSpeakingLocks(store),
        adapters={PLATFORM: harness.sink}, gate=None,
        metrics=harness.metrics, **kw)


def _state(store) -> SqlParticipationStateStore:
    return SqlParticipationStateStore(store._engine)


async def _register(redis_client, store, stream, env: Envelope) -> None:
    await StreamProducer(redis_client, stream).enqueue(env)
    reader = StreamReader(platform=PLATFORM, account_key=ACCOUNT,
                          redis=redis_client, store=store)
    task = asyncio.create_task(reader._read())
    try:
        for _ in range(150):
            if await store.event(env.event_id) is not None:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"event {env.event_id} never registered")
    finally:
        reader.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _pending(redis_client, stream) -> int:
    info = await redis_client.xpending(stream, CONSUMER_GROUP)
    return info["pending"] if isinstance(info, dict) else info[0]


async def _work_rows(store):
    async with store._engine.connect() as conn:
        return (await conn.execute(sa.select(BRIDGE_WORK))).mappings().all()


async def _enqueue_work(store, *, kind: str, payload: dict,
                        conversation_id: str = ROOM) -> None:
    async with store._engine.begin() as conn:
        await store.enqueue_work(
            conn, event_id=None, kind=kind, ordinal=0, platform=PLATFORM,
            account_key=ACCOUNT, conversation_id=conversation_id,
            payload=payload)


async def _finish_state(store, work_id: int) -> dict:
    async with store._engine.connect() as conn:
        return dict((await conn.execute(sa.select(BRIDGE_WORK).where(
            BRIDGE_WORK.c.id == work_id))).mappings().one())


_PERSON_DICT = {"person_id": "miles", "name": "Miles",
                "honcho_peer_id": "miles"}


def _work_payload(ts: int, *, absorbed: bool,
                  person: dict | None = _PERSON_DICT,
                  sender: str = MAPPED_SENDER, conversation_id: str = ROOM,
                  unit: str | None = "nora"):
    return {"message": _msg("hello room", ts, sender=sender,
                            conversation_id=conversation_id),
            "person": person, "absorbed": absorbed, "row_id": 1,
            "unit": unit}


def _stimulus_wiring(store, *, fail: bool = False) -> Wiring:
    class _Store(SqlStimulusStore):
        async def append(self, **kw):
            if fail:
                raise RuntimeError("stimulus write lost")
            return await super().append(**kw)

    return Wiring(store=_Store(store._engine),
                  unit_of=lambda *_a, **_k: "room-g1",
                  self_ids=frozenset(), reply_quiet_seconds=0)


def _listed_policy(tmp_path):
    """A REAL `Policy` — the harness's pipeline allowlist is `permissive_policy`,
    which synthesizes a fresh room per call and can never express a revoke.
    The deferred recheck must read a policy object whose rooms can change."""
    from nora.policy import load_policy
    path = tmp_path / "policy.toml"
    path.write_text(
        'default = "deny"\n'
        '[[conversations]]\nplatform = "signal"\nid = "g1"\n'
        'label = "g1"\npersons = "*"\nunit = "room-g1"\n')
    return load_policy(path)


# ---------------------------------------------------------------------------
# Blocker 1 — deferred memory/stimulus honour current permission.
# ---------------------------------------------------------------------------


async def test_ingest_obligations_carry_the_admission_absorption_fact(
        bridge, redis_client, harness):
    """The pipeline's `absorbed` fact must reach the deferred works: a listed
    room's message enqueues memory+stimulus obligations carrying
    `absorbed=True` and the resolved person, not a constant."""
    store, _, stream = bridge
    await _register(redis_client, store, stream,
                    _envelope("hello room", 5000, sender=MAPPED_SENDER))
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho},
                     stimulus=_stimulus_wiring(store))
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    rows = {r["kind"]: r for r in await _work_rows(store)}
    feed, stim = rows["memory_feed"]["payload"], rows["stimulus"]["payload"]
    assert feed["absorbed"] is True and stim["absorbed"] is True
    assert feed["person"]["person_id"] == "miles"
    # Executing them actually feeds and writes — the positive control.
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    assert harness.honcho.fed != []
    async with store._engine.connect() as conn:
        assert (await conn.execute(sa.select(STIMULUS))).all() != []


async def test_mention_only_room_obligation_never_feeds_or_stimulates(
        bridge, redis_client, harness):
    """The reviewed defect, on the exact seam: a mention-only room's work
    payload carries `absorbed=False`, so the deferred executor must skip
    BOTH effects — under the constant `absorbed=True` wiring this reached
    Honcho."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5001, absorbed=False))
    await _enqueue_work(store, kind="stimulus",
                        payload=_work_payload(5001, absorbed=False))
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho},
                     stimulus=_stimulus_wiring(store))
    for _ in range(2):
        await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    rows = {r["kind"]: r for r in await _work_rows(store)}
    assert rows["memory_feed"]["state"] == "intentional_skip"
    assert rows["stimulus"]["state"] == "intentional_skip"
    assert rows["memory_feed"]["result"]["reason"] == "room not absorbed"
    assert harness.honcho.fed == []
    async with store._engine.connect() as conn:
        assert (await conn.execute(sa.select(STIMULUS))).all() == []


async def test_absorption_revoked_after_enqueue_skips_with_typed_result(
        bridge, redis_client, harness, tmp_path):
    """Admitted absorbed, revoked before the effect ran: the work payload
    says `True`, but the room in the CURRENT allowlist is no longer absorbed
    — an intentional skip under the current truth, not a stale feed."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5002, absorbed=True,
                                              unit="room-g1"))
    policy = _listed_policy(tmp_path)
    # The room still exists but is now discovered mention-only: absorbed
    # flips False under the CURRENT policy, exactly as a runtime revoke does.
    policy._by_id[(PLATFORM, ROOM)] = Conversation(
        platform=PLATFORM, ids=frozenset({ROOM}), persons=None,
        primary_id=ROOM, unit="room-g1", discovered=True)
    assert policy.conversation(PLATFORM, ROOM).absorbed is False
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho},
                     allowlist=policy)
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "intentional_skip"
    assert row["result"]["reason"] == "room not absorbed"
    assert harness.honcho.fed == []


async def test_room_revoked_after_enqueue_skips(
        bridge, redis_client, harness, tmp_path):
    """The room itself was removed from the allowlist after enqueue:
    `conversation()` answers None and the work fails closed."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5003, absorbed=True,
                                              unit="room-g1"))
    policy = _listed_policy(tmp_path)
    del policy._by_id[(PLATFORM, ROOM)]
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho},
                     allowlist=policy)
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "intentional_skip"
    assert harness.honcho.fed == []


async def test_person_unmapped_after_enqueue_skips_feed(
        bridge, redis_client, harness, tmp_path):
    """The sender's person mapping was withdrawn between enqueue and effect:
    the CURRENT persons no longer resolves the id — no feed under a stale
    snapshot."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5004, absorbed=True))
    identity = tmp_path / "identity-empty.toml"
    identity.write_text("")
    from nora.persons import load_persons
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho},
                     persons=load_persons(identity))
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "intentional_skip"
    assert row["result"]["reason"] == "sender unmapped or changed"
    assert harness.honcho.fed == []


async def test_memory_feed_false_is_uncertain_not_done(
        bridge, redis_client, harness):
    """`memory.feed` returning False means the POST may have landed and the
    response was lost — `failed_retained`, never `done`. The reviewed
    defect marked it done."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5005, absorbed=True))
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": FakeMemoryStore(fail=True)})
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "failed_retained"
    assert row["result"]["reason"] == "feed unconfirmed"


async def test_sender_account_unmapped_while_person_remains_skips_feed(
        bridge, redis_client, harness):
    from nora.domain.models import PersonRef
    from nora.persons import Persons

    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5300, absorbed=True))
    person = PersonRef(**_PERSON_DICT)
    persons = Persons(by_person={person.person_id: person}, index={})
    assert person.person_id in persons.by_person
    assert persons.resolve(PLATFORM, MAPPED_SENDER) is None
    runner = _runner(store, redis_client, harness, persons=persons,
                     memory_feed={"memory": harness.honcho})
    work = await store.claim_next(owner=runner._owner, lease_seconds=60)
    await runner._execute(work)
    assert (await _finish_state(store, work.id))["state"] == "intentional_skip"
    assert harness.honcho.fed == []


async def test_stimulus_missing_append_receipt_is_retryable(
        bridge, redis_client, harness):
    store, _, _ = bridge

    class UnconfirmedStore(SqlStimulusStore):
        async def append(self, *, stimulus, recorded_at_ms):
            assert stimulus.ref == "messages:1"
            await super().append(stimulus=stimulus, recorded_at_ms=recorded_at_ms)
            return None

    wiring = Wiring(store=UnconfirmedStore(store._engine),
                    unit_of=lambda *_a, **_k: "room-g1",
                    self_ids=frozenset(), reply_quiet_seconds=0)
    await _enqueue_work(store, kind="stimulus",
                        payload=_work_payload(5301, absorbed=True, person=None))
    runner = _runner(store, redis_client, harness, stimulus=wiring)
    work = await store.claim_next(owner=runner._owner, lease_seconds=60)
    await runner._execute(work)
    assert (await _finish_state(store, work.id))["state"] == "retryable_failure"
    reclaimed = await store.claim_next(owner=runner._owner, lease_seconds=60)
    await runner._execute(reclaimed)
    assert (await _finish_state(store, work.id))["state"] == "done"
    async with store._engine.connect() as conn:
        assert len((await conn.execute(sa.select(STIMULUS))).all()) == 1


async def test_participation_fire_heartbeat_detects_killed_speaking_backend(
        bridge, redis_client, harness):
    from nora.app.bridge_queue import _LeaseLostError

    store, _, _ = bridge
    await _enqueue_work(store, kind="participation_fire", payload={})
    runner = _runner(store, redis_client, harness, lease_seconds=1)
    work = await store.claim_next(owner=runner._owner, lease_seconds=10)
    key = (PLATFORM, ROOM)
    locks = runner._db_locks
    assert await locks.try_acquire(key)
    try:
        assert await locks.lock_alive(key)
        pid = (await locks._in_flight[key].execute(
            sa.text("SELECT pg_backend_pid()"))).scalar_one()
        async with store._engine.connect() as conn:
            assert (await conn.execute(
                sa.text("SELECT pg_terminate_backend(:pid)"),
                {"pid": pid})).scalar_one()
        with pytest.raises(_LeaseLostError):
            await asyncio.wait_for(runner._lease_heartbeat(work, key), timeout=0.8)
    finally:
        await locks.release(key)


async def test_memory_feed_exception_is_retryable_not_done(
        bridge, redis_client, harness):
    """A raise BEFORE the non-idempotent POST boundary (here `ensure_peer`)
    is pre-effect: nothing non-idempotent started, so retryable is the
    honest answer — the work is owed until the typed answer exists."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5006, absorbed=True))

    class _Down:
        async def ensure_peer(self, **_k):
            raise RuntimeError("honcho unreachable")

        async def feed(self, **_k):
            raise AssertionError("feed ran after the peer check failed")

    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": _Down()})
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "retryable_failure"


async def test_feed_exception_after_the_start_mark_is_retained(
        bridge, redis_client, harness):
    """A raise AFTER the start mark commits is a POST that may have landed:
    `feed` has no idempotency key, so the work is retained, not retried."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5303, absorbed=True))

    class _Lost:
        async def ensure_peer(self, **_k):
            return None

        async def feed(self, **_k):
            raise RuntimeError("response lost")

    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": _Lost()})
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "failed_retained"
    assert row["result"]["reason"] == "feed started"


async def test_feed_acceptance_before_finish_is_not_reposted_on_reclaim(
        bridge, redis_client, harness):
    store, _, _ = bridge
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5302, absorbed=True))
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho})
    finish = store.finish_work
    fail_once = True

    async def lose_completion(conn, work_id, **kw):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("completion unavailable after feed acceptance")
        return await finish(conn, work_id, **kw)

    store.finish_work = lose_completion
    work = await store.claim_next(owner=runner._owner, lease_seconds=60)
    try:
        await runner._execute(work)
    finally:
        store.finish_work = finish
    assert len(harness.honcho.fed) == 1
    replacement = _runner(store, redis_client, harness,
                          memory_feed={"memory": harness.honcho})
    reclaimed = await store.claim_next(owner=replacement._owner, lease_seconds=60)
    assert reclaimed is not None
    await replacement._execute(reclaimed)
    assert len(harness.honcho.fed) == 1
    assert (await _finish_state(store, work.id))["state"] in (
        "uncertain", "failed_retained")


_FEED_WORKER = '''
import asyncio
import sys

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import create_async_engine

from nora.app.bridge_queue import DbSpeakingLocks, WorkRunner
from nora.domain.models import PersonRef
from nora.infra.honcho.client import HonchoClient
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy


async def main() -> None:
    dsn, redis_url, honcho_url, workspace = sys.argv[1:5]
    engine = create_async_engine(dsn)
    store = SqlBridgeStore(engine)
    redis = aioredis.Redis.from_url(redis_url)
    person = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
    runner = WorkRunner(
        store=store, redis=redis, ingest=None, engagement=None, slots=None,
        db_locks=DbSpeakingLocks(store), adapters={}, gate=None,
        metrics=Metrics(),
        memory_feed={"memory": HonchoClient(honcho_url, workspace)},
        allowlist=permissive_policy(),
        persons=Persons(by_person={"miles": person},
                        index={("signal", "+852111"): "miles"}),
        lease_seconds=3)
    work = await store.claim_next(owner=runner._owner, lease_seconds=3)
    if work is not None:
        await runner._execute(work)
    await engine.dispose()
    await redis.aclose()


asyncio.run(main())
'''

_HONCHO_WORKSPACE = "it-ws"


async def test_subprocess_kill_after_feed_acceptance_reposts_nothing(
        bridge, redis_client, harness, tmp_path, pg_url, _redis_container):
    """The real fault window, end to end: a separate PROCESS claims the feed
    work, its POST lands on the fake Honcho (server-side accepted), and the
    process is SIGKILLed before its finish commit — the response never
    returned, exactly the uncertain shape. A fresh worker process (here the
    in-process replacement runner) must see the committed start mark and
    retain the work WITHOUT reposting; a never-started work must still feed.
    """
    store, _, _ = bridge
    fake = FakeHoncho()
    url = await fake.start()
    try:
        fake.seed(_HONCHO_WORKSPACE, created_at="2026-01-01T00:00:00+00:00")
        fake.hold_message_response = True
        await _enqueue_work(store, kind="memory_feed",
                            payload=_work_payload(5400, absorbed=True))
        script = tmp_path / "feed_worker.py"
        script.write_text(_FEED_WORKER)
        redis_url = (
            f"redis://{_redis_container.get_container_host_ip()}:"
            f"{_redis_container.get_exposed_port(6379)}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script), pg_url, redis_url, url,
            _HONCHO_WORKSPACE)
        # The POST landing is the proof the process crossed the boundary:
        # the write is recorded server-side while its response is held.
        for _ in range(300):
            if fake._message_posts:
                break
            if proc.returncode is not None:
                raise AssertionError(
                    f"worker exited {proc.returncode} before the POST")
            await asyncio.sleep(0.1)
        assert fake._message_posts == 1
        proc.kill()
        await proc.wait()
        assert proc.returncode == -9
        # The window is closed: the held handler answers (into nothing) and
        # every later POST — the control's — responds normally.
        fake.hold_message_response = False
        fake.release_message_response.set()
        # The dead owner's 3s lease lapses; expire_leases reparks the row.
        await asyncio.sleep(3.5)
        await store.expire_leases()
        runner = _runner(
            store, redis_client, harness,
            memory_feed={"memory": HonchoClient(url, _HONCHO_WORKSPACE)})
        reclaimed = await store.claim_next(owner=runner._owner,
                                         lease_seconds=30)
        assert reclaimed is not None
        await runner._execute(reclaimed)
        assert fake._message_posts == 1            # retained, never reposted
        row = await _finish_state(store, reclaimed.id)
        assert row["state"] == "uncertain"
        # Positive control: a work whose start was never committed still
        # runs its effect exactly once under the same replacement runner.
        await _enqueue_work(store, kind="memory_feed",
                            payload=_work_payload(5401, absorbed=True))
        fresh = await store.claim_next(owner=runner._owner, lease_seconds=30)
        assert fresh is not None
        await runner._execute(fresh)
        assert fake._message_posts == 2
        assert (await _finish_state(store, fresh.id))["state"] == "done"
    finally:
        fake.release_message_response.set()
        await fake.close()


async def test_stimulus_append_exception_is_retryable_not_done(
        bridge, redis_client, harness):
    """The reviewed defect's second half: the stage swallowed the append
    exception and the runner marked the work `done`. Now it retries."""
    store, _, _ = bridge
    await _enqueue_work(store, kind="stimulus",
                        payload=_work_payload(5007, absorbed=True, person=None))
    runner = _runner(store, redis_client, harness,
                     stimulus=_stimulus_wiring(store, fail=True))
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "retryable_failure"


async def test_stimulus_reclaim_dedupes_the_message_row(
        bridge, redis_client, harness):
    """A kill after the stimulus append committed but before `done`: the
    replay must find the row by (unit, kind, ref), not write a second one —
    `kind="message"` carries no unique index."""
    store, _, _ = bridge
    wiring = _stimulus_wiring(store)
    await _enqueue_work(store, kind="stimulus",
                        payload=_work_payload(5008, absorbed=True, person=None))
    runner = _runner(store, redis_client, harness, stimulus=wiring)

    real_finish = store.finish_work
    fired = {"once": False}

    async def kill_once(conn, wid, **kw):
        if not fired["once"]:
            fired["once"] = True
            raise RuntimeError("killed after append")
        return await real_finish(conn, wid, **kw)

    store.finish_work = kill_once
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)   # append lands, finish dies → retryable
    store.finish_work = real_finish
    work = await store.claim_next(owner="w", lease_seconds=60)
    assert work.kind == "stimulus"
    await runner._execute(work)   # dedupe hit → the SAME row id → done
    row = await _finish_state(store, work.id)
    assert row["state"] == "done"
    assert row["result"]["stimulus_id"] is not None
    async with store._engine.connect() as conn:
        assert len((await conn.execute(sa.select(STIMULUS))).all()) == 1


# ---------------------------------------------------------------------------
# Blocker 4 — resume from the committed handoff snapshot.
# ---------------------------------------------------------------------------


async def test_kill_between_audit_and_handoff_resumes_from_snapshot(
        bridge, redis_client, harness, monkeypatch):
    """The real fault window the probe reproduces: audit commits, the
    downstream-obligation commit dies. The reclaim must resume from the
    snapshot the audit transaction persisted — one message row, no
    re-ingest, no second gate call, every obligation committed once."""
    store, _, stream = bridge
    env = _envelope("hello room", 5100, sender=MAPPED_SENDER)
    await _register(redis_client, store, stream, env)
    pipeline = harness.handler._dispatcher._ingest
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho},
                     stimulus=_stimulus_wiring(store))

    ingest_calls = {"n": 0}
    real_ingest_run = pipeline.run

    class IngestSpy:
        async def run(self, **kw):
            ingest_calls["n"] += 1
            return await real_ingest_run(**kw)

    runner._ingest = IngestSpy()

    import nora.ingest.stages.gate as gate_module
    gate_calls = {"n": 0}
    real_gate_run = gate_module.run

    async def counting_gate(**kw):
        gate_calls["n"] += 1
        return await real_gate_run(**kw)

    monkeypatch.setattr(gate_module, "run", counting_gate)

    real_enqueue = store.enqueue_work
    fired = {"once": False}

    async def kill_once(conn, **kw):
        if not fired["once"]:
            fired["once"] = True
            raise RuntimeError("killed after audit commit")
        return await real_enqueue(conn, **kw)

    monkeypatch.setattr(store, "enqueue_work", kill_once)
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    assert ingest_calls["n"] == 1 and gate_calls["n"] == 1
    async with store._engine.connect() as conn:
        assert len((await conn.execute(sa.select(MESSAGES))).all()) == 1
    assert (await _finish_state(store, work.id))["state"] \
        == "retryable_failure"

    # The committed event carries the COMPLETE handoff snapshot.
    event = await store.event(env.event_id)
    handoff = event["payload"]["handoff"]
    assert handoff["row_id"] and handoff["decision"]["reason"]
    assert handoff["absorbed"] is True
    assert handoff["person"]["person_id"] == "miles"

    monkeypatch.undo()  # enqueue_work + gate restored for the replay
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    assert ingest_calls["n"] == 1   # no re-ingest, no second audit row
    assert gate_calls["n"] == 1     # the committed decision is the truth
    async with store._engine.connect() as conn:
        assert len((await conn.execute(sa.select(MESSAGES))).all()) == 1
    assert (await _finish_state(store, work.id))["state"] == "done"
    kinds = {r["kind"] for r in await _work_rows(store)}
    assert {"memory_feed", "stimulus"} <= kinds
    assert await _pending(redis_client, stream) == 0


async def test_legacy_event_without_handoff_fails_closed(
        bridge, redis_client, harness):
    """A committed row that predates the snapshot cannot be reconstructed
    safely — `failed_retained`, never a guessed re-admission."""
    store, _, stream = bridge
    env = _envelope("old", 5101)
    await _register(redis_client, store, stream, env)
    # The event committed under an older shape: row stamped, no handoff.
    async with store._engine.begin() as conn:
        await conn.execute(sa.update(BRIDGE_EVENT).where(
            BRIDGE_EVENT.c.id == env.event_id).values(
            payload={"envelope": env.to_fields(), "message_row_id": 1}))
    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": harness.honcho})
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "failed_retained"
    assert row["result"]["reason"] \
        == "committed row predates handoff snapshot"


# ---------------------------------------------------------------------------
# Blocker 5 — participation membership, close and fire are durable.
# ---------------------------------------------------------------------------


def _trigger(store, harness, *, clock, speak=None, gate=None) -> ParticipationTrigger:
    async def _default_speak(*_a, **_k):
        return "sent"

    trigger = ParticipationTrigger(
        speak=speak or _default_speak,
        slots=harness._engagement._slots, settle_seconds=60,
        cooldown_seconds=1800,
        gate=gate, clock=clock, store=_state(store))
    trigger.set_close_handoff(store.close_participation_window)
    return trigger


async def _participation_work(store, ts: int,
                              conversation_id: str = ROOM) -> None:
    await _enqueue_work(
        store, kind="participation", conversation_id=conversation_id,
        payload={"note_message": _msg("unprompted thought", ts,
                                      conversation_id=conversation_id),
                 "absorbed": True})


async def test_candidate_note_survives_kill_and_replays_once(
        bridge, redis_client, harness):
    """Kill after `mark_candidate` commits but before `done`: the reclaim
    re-runs the note — the dedupe id keeps the room count at ONE, and the
    ledger, not memory, holds the membership."""
    store, _, _ = bridge
    trigger = _trigger(store, harness, clock=lambda: 1000.0)
    await _participation_work(store, 5200)
    runner = _runner(store, redis_client, harness, participation=trigger)

    real_finish = store.finish_work
    fired = {"once": False}

    async def kill_once(conn, wid, **kw):
        if not fired["once"]:
            fired["once"] = True
            raise RuntimeError("killed after candidate mark")
        return await real_finish(conn, wid, **kw)

    store.finish_work = kill_once
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    assert (await _finish_state(store, work.id))["state"] == "retryable_failure"
    store.finish_work = real_finish
    work = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(work)
    row = await _finish_state(store, work.id)
    assert row["state"] == "done"
    assert row["result"]["candidate"]["noted_ms"]
    assert trigger._unjudged[(PLATFORM, ROOM)] == 1  # noted exactly once
    assert await _state(store).unfolded_candidates() != []


async def test_close_handoff_commits_watermark_fold_and_fire_atomically(
        bridge, redis_client, harness):
    """The durable close: ONE commit writes the window watermark, folds the
    noted candidates and enqueues `participation_fire` — a judged window is
    never persisted without its owned turn."""
    store, _, _ = bridge
    now = [1000.0]
    trigger = _trigger(store, harness, clock=lambda: now[0])
    await _participation_work(store, 5201)
    runner = _runner(store, redis_client, harness, participation=trigger)
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))

    now[0] += 61.0  # past settle: the window is due
    assert await trigger.close_due_windows() == 1
    rows = {r["kind"]: r for r in await _work_rows(store)}
    assert rows["participation_fire"]["state"] == "pending"
    candidate = rows["participation"]["result"]["candidate"]
    assert candidate["folded_ms"] == 1061000
    async with store._engine.connect() as conn:
        window = (await conn.execute(sa.select(
            PARTICIPATION_WINDOW_STATE))).mappings().one()
    assert window["last_window_end_at"] == 1061000


async def test_ambient_message_does_not_invent_membership(
        bridge, redis_client, harness):
    """A second message that was never noted as a candidate must NOT join
    the window: membership comes from the work ledger, not from all-message
    history."""
    store, _, _ = bridge
    now = [1000.0]
    trigger = _trigger(store, harness, clock=lambda: now[0])
    await _participation_work(store, 5202)
    # An ambient row that reached the transcript WITHOUT a participation
    # obligation — the reconstruction the blocker forbids must not see it.
    ambient = await harness.store.append(message=NormalizedMessage(
        platform=PLATFORM, conversation_id=ROOM, conversation_type="group",
        sender_id=UNMAPPED_SENDER, sender_name="Sender", timestamp=5202,
        body="ambient chatter",
        raw={"dataMessage": {"message": "ambient chatter"}},
        message_id="m-ambient"))
    assert ambient
    runner = _runner(store, redis_client, harness, participation=trigger)
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    now[0] += 61.0
    await trigger.close_due_windows()
    assert await _state(store).unfolded_candidates() == []
    rows = [r for r in await _work_rows(store)
            if r["kind"] == "participation"]
    assert len(rows) == 1  # exactly the noted candidate — never invented


@pytest.mark.parametrize(("outcome", "expected_state"), [
    ("sent", "done"),
    ("silent", "done"),
    ("failed_error", "failed_retained"),
    ("failed_timeout", "failed_retained"),
    ("failed_policy", "failed_retained"),
])
async def test_participation_fire_runs_admitted_turn_once(
        bridge, redis_client, harness, outcome, expected_state):
    """The claimed fire work: gate admits (once, persisted), slot and DB
    lock taken, the canonical participation occasion speaks, the send and
    the cooldown cursor commit together."""
    store, _, _ = bridge
    now = [1000.0]
    speaks: list[dict] = []
    gate_calls = {"n": 0}

    async def speak(occasion, **kw):
        speaks.append(kw)
        return outcome

    class Gate:
        async def decide(self, *, platform, conversation_id):
            gate_calls["n"] += 1
            return GateVerdict(verdict="go", score=0.9, source="model")

    trigger = _trigger(store, harness, clock=lambda: now[0], speak=speak, gate=Gate())
    await _participation_work(store, 5203)
    runner = _runner(store, redis_client, harness,
                     participation=trigger, fire_speak=speak,
                     participation_state=_state(store))
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    now[0] += 61.0
    await trigger.close_due_windows()
    fire = await store.claim_next(owner="w", lease_seconds=60)
    assert fire.kind == "participation_fire"
    await runner._execute(fire)
    row = await _finish_state(store, fire.id)
    assert row["state"] == expected_state
    assert row["result"]["outcome"] == outcome
    assert row["result"]["telemetry_last_attempt"]["outcome"] == expected_state
    if expected_state == "failed_retained":
        assert row["result"]["reason"] == outcome
    assert gate_calls["n"] == 1
    assert len(speaks) == 1 and speaks[0]["send_permitted"] is True
    last_send = await _state(store).last_send_at(
        platform=PLATFORM, conversation_id=ROOM)
    assert (last_send is not None) == (outcome == "sent")
    assert await store.claim_next(owner="replacement", lease_seconds=60) is None


async def test_fire_reclaim_uses_persisted_admission_no_second_gate(
        bridge, redis_client, harness):
    """Kill after the admission verdict committed but before the slot: the
    reclaim must reuse the persisted verdict — the gate runs ONCE per fire
    work, and the turn is not re-judged."""
    store, _, _ = bridge
    now = [1000.0]
    speaks: list[str] = []
    gate_calls = {"n": 0}

    async def speak(*_a, **_k):
        speaks.append("sent")
        return "sent"

    class Gate:
        async def decide(self, *, platform, conversation_id):
            gate_calls["n"] += 1
            return GateVerdict(verdict="go", score=None, source="model")

    slots = ConversationSlots(
        maxlen=4, spawn=lambda coro: asyncio.ensure_future(coro))
    real_try = slots.try_acquire
    fired = {"once": False}

    def kill_once(key):
        if not fired["once"]:
            fired["once"] = True
            raise RuntimeError("killed after admission commit")
        return real_try(key)

    trigger = _trigger(store, harness, clock=lambda: now[0], speak=speak, gate=Gate())
    await _participation_work(store, 5204)
    runner = _runner(store, redis_client, harness,
                     participation=trigger, fire_speak=speak,
                     participation_state=_state(store), slots=slots)
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    now[0] += 61.0
    await trigger.close_due_windows()
    slots.try_acquire = kill_once
    fire = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(fire)      # admission commits, slot call dies
    assert gate_calls["n"] == 1
    slots.try_acquire = real_try
    fire = await store.claim_next(owner="w", lease_seconds=60)
    await runner._execute(fire)      # persisted admission — no re-gate
    assert gate_calls["n"] == 1
    assert speaks == ["sent"]
    assert (await _finish_state(store, fire.id))["state"] == "done"


async def test_fire_reclaim_after_started_action_is_uncertain(
        bridge, redis_client, harness):
    """A prior fire attempt that STARTED the send fences the reclaim — the
    outbound action is uncertain, never blindly replayed."""
    store, _, _ = bridge
    now = [1000.0]
    speaks: list[str] = []

    async def speak(*_a, **_k):
        speaks.append("sent")
        return "sent"

    trigger = _trigger(store, harness, clock=lambda: now[0], speak=speak)
    await _participation_work(store, 5205)
    runner = _runner(store, redis_client, harness,
                     participation=trigger, fire_speak=speak,
                     participation_state=_state(store))
    await runner._execute(await store.claim_next(owner="w", lease_seconds=60))
    now[0] += 61.0
    await trigger.close_due_windows()
    fire = await store.claim_next(owner="w", lease_seconds=60)
    async with store._engine.begin() as conn:
        granted, _ = await store.reserve_action(
            conn, action_id=f"w-{fire.id}:send:0", platform=PLATFORM,
            account_key=ACCOUNT, owner_kind="work", owner_id=f"w-{fire.id}",
            owner_generation=fire.generation, op="send",
            request={"text": "partial"}, message_id=None)
        assert granted == "execute"  # the row is `started` from here
    await runner._execute(fire)
    row = await _finish_state(store, fire.id)
    assert row["state"] == "uncertain"
    assert speaks == []


async def test_replacement_app_recovers_window_and_fires_once(
        bridge, redis_client, harness_factory):
    """App replacement between candidate and fire. App A notes the
    candidate, an ambient non-candidate lands in the transcript LATER than
    the close, A's close commits (watermark + fold + fire, one transaction)
    then dies mid-tick. App B — a FRESH trigger and runner over the same
    ledger — sees the committed watermark, evaluates nothing twice and the
    fire speaks once through the REAL FirePath, its send signed with the
    work's own (id, generation). The ambient row never re-times the window
    and never invents membership."""
    store, _, _ = bridge
    now = [time.time()]
    h = await harness_factory(participation_script=[
        ScriptedTurn(tool_calls=[("send_reply", {"text": "quiet thought"})])])
    real_speak = h.participation_trigger._speak
    trig_a = _trigger(store, h, clock=lambda: now[0], speak=real_speak)
    runner_a = _runner(store, redis_client, h,
                       participation=trig_a, fire_speak=real_speak,
                       participation_state=_state(store))
    await _participation_work(store, 5600)
    await runner_a._execute(
        await store.claim_next(owner=runner_a._owner, lease_seconds=60))

    real_close = store.close_participation_window
    died = {"once": False}

    async def close_then_die(*args):
        await real_close(*args)
        if not died["once"]:
            died["once"] = True
            raise RuntimeError("app A died after the close committed")

    trig_a.set_close_handoff(close_then_die)
    now[0] += 61.0
    with pytest.raises(RuntimeError, match="app A died"):
        await trig_a.close_due_windows()
    # Ambient chatter that was never a candidate, landing strictly AFTER the
    # window committed: hydration must not let it re-open or re-time
    # anything — under an all-messages cursor this room would read due.
    assert await h.store.append(message=NormalizedMessage(
        platform=PLATFORM, conversation_id=ROOM, conversation_type="group",
        sender_id=UNMAPPED_SENDER, sender_name="Sender",
        timestamp=int(now[0] * 1000) + 500, body="ambient chatter",
        raw={"dataMessage": {"message": "ambient chatter"}},
        message_id="m-ambient-5600"))

    # App B: new trigger and runner objects — A's in-memory counts are gone.
    trig_b = _trigger(store, h, clock=lambda: now[0], speak=real_speak)
    now[0] += 61.0   # quiet long enough that an ambient-seeded cursor
    #                  would read this room due again
    assert await trig_b.close_due_windows() == 0
    fires = [r for r in await _work_rows(store)
             if r["kind"] == "participation_fire"]
    assert len(fires) == 1

    runner_b = _runner(store, redis_client, h,
                       participation=trig_b, fire_speak=real_speak,
                       participation_state=_state(store))
    fire = await store.claim_next(owner=runner_b._owner, lease_seconds=60)
    assert fire is not None and fire.kind == "participation_fire"
    await runner_b._execute(fire)
    assert h.sink.texts == ["quiet thought"]
    assert h.sink.sent_owners == [
        ActionOwner(kind="work", id=f"w-{fire.id}",
                    generation=fire.generation)]
    assert (await _finish_state(store, fire.id))["state"] == "done"


async def test_busy_tag_skips_fire_and_other_room_is_unaffected(
        bridge, redis_client, harness_factory):
    """A tag holding the speaking slot spends the fire as an intentional
    skip — the live trigger's exact semantics — while a second room's own
    window, candidate and fire run untouched beside it."""
    store, _, _ = bridge
    now = [time.time()]
    h = await harness_factory(participation_script=[
        ScriptedTurn(tool_calls=[("send_reply", {"text": "quiet thought"})])])
    real_speak = h.participation_trigger._speak
    trigger = _trigger(store, h, clock=lambda: now[0], speak=real_speak)
    runner = _runner(store, redis_client, h,
                     participation=trigger, fire_speak=real_speak,
                     participation_state=_state(store))
    await _participation_work(store, 5700)
    await _participation_work(store, 5701, conversation_id="g2")
    for _ in range(2):
        await runner._execute(
            await store.claim_next(owner=runner._owner, lease_seconds=60))
    now[0] += 61.0
    assert await trigger.close_due_windows() == 2
    # The tag owns g1's slot for the whole attempt window.
    held = (PLATFORM, ROOM)
    assert h._engagement._slots.try_acquire(held)
    try:
        fires: dict[str, Any] = {}
        for _ in range(2):
            work = await store.claim_next(owner=runner._owner,
                                          lease_seconds=60)
            assert work is not None
            await runner._execute(work)
            fires[work.conversation_id] = work
        g1 = await _finish_state(store, fires[ROOM].id)
        assert g1["state"] == "intentional_skip"
        assert g1["result"]["reason"] == "speaking_slot_busy"
        g2 = await _finish_state(store, fires["g2"].id)
        assert g2["state"] == "done"
        assert [t.conversation_id for t, _ in h.sink.sent] == ["g2"]
        assert h.sink.sent_owners == [
            ActionOwner(kind="work", id=f"w-{fires['g2'].id}",
                        generation=fires["g2"].generation)]
    finally:
        h._engagement._slots.release_and_dispatch(held)


async def test_handoff_carries_custody_identity_and_unit_through_replacement(
        bridge, redis_client, harness_factory, monkeypatch):
    """The committed snapshot is the WHOLE admission truth, not a body:
    the custody-enriched message (its media handle), the resolved person,
    the room's unit and the gate's decision. A fresh processor object graph
    resumes from it — one audit row, one gate call, exactly one obligation
    of each kind the decision owes."""
    store, _, stream = bridge
    harness = await harness_factory(participation_script=[
        ScriptedTurn(tool_calls=[("send_reply", {"text": "t"})])])
    env = _envelope("look at this", 5702, sender=MAPPED_SENDER,
                    media=(MediaRef(platform_id="att-1",
                                    media_type="image/png"),))
    await _register(redis_client, store, stream, env)
    pipeline = harness.handler._dispatcher._ingest
    ingest_calls = {"n": 0}
    real_ingest_run = pipeline.run

    class IngestSpy:
        async def run(self, **kw):
            ingest_calls["n"] += 1
            return await real_ingest_run(**kw)

    import nora.ingest.stages.gate as gate_module
    gate_calls = {"n": 0}
    real_gate_run = gate_module.run

    async def counting_gate(**kw):
        gate_calls["n"] += 1
        return await real_gate_run(**kw)

    monkeypatch.setattr(gate_module, "run", counting_gate)
    real_enqueue = store.enqueue_work
    fired = {"once": False}

    async def kill_once(conn, **kw):
        if not fired["once"]:
            fired["once"] = True
            raise RuntimeError("killed after audit commit")
        return await real_enqueue(conn, **kw)

    monkeypatch.setattr(store, "enqueue_work", kill_once)
    runner_a = _runner(store, redis_client, harness,
                       memory_feed={"memory": harness.honcho},
                       stimulus=_stimulus_wiring(store),
                       participation=_trigger(store, harness,
                                              clock=time.time))
    runner_a._ingest = IngestSpy()
    ingest_work = await store.claim_next(owner="w", lease_seconds=60)
    await runner_a._execute(ingest_work)
    assert (await _finish_state(store, ingest_work.id)
            )["state"] == "retryable_failure"

    event = await store.event(env.event_id)
    handoff = event["payload"]["handoff"]
    # Custody continuation, not a body-only snapshot.
    assert handoff["message"]["media"][0]["platform_id"] == "att-1"
    assert handoff["person"]["person_id"] == "miles"
    assert handoff["absorbed"] is True and handoff["unit"] == "nora"
    assert handoff["decision"]["relevance_gate"] is True

    monkeypatch.undo()
    runner_b = _runner(store, redis_client, harness,
                       memory_feed={"memory": harness.honcho},
                       stimulus=_stimulus_wiring(store),
                       participation=_trigger(store, harness,
                                              clock=time.time))
    await runner_b._execute(await store.claim_next(owner="w",
                                                   lease_seconds=60))
    assert ingest_calls["n"] == 1 and gate_calls["n"] == 1
    async with store._engine.connect() as conn:
        assert len((await conn.execute(sa.select(MESSAGES))).all()) == 1
    rows = [r for r in await _work_rows(store) if r["kind"] != "ingest"]
    kinds = [r["kind"] for r in rows]
    assert sorted(kinds) == ["memory_feed", "participation", "stimulus"]
    note = next(r for r in rows if r["kind"] == "participation")
    assert note["payload"]["note_message"]["media"][0]["platform_id"] \
        == "att-1"
    feed = next(r for r in rows if r["kind"] == "memory_feed")
    assert feed["payload"]["message"]["media"][0]["platform_id"] == "att-1"
    assert feed["payload"]["unit"] == "nora"
    assert await _pending(redis_client, stream) == 0


async def test_moved_unit_binding_refuses_deferred_effects(
        bridge, redis_client, harness, tmp_path):
    """The pinned-scope defence: the room's unit binding moved between
    enqueue and drain — writing the content under the NEW unit is a
    disclosure, not a delay. A store that routes by the CURRENT binding at
    call time proves nothing reached the moved scope: both deferred
    effects refuse with `unit binding moved`."""
    store, _, _ = bridge
    policy = _listed_policy(tmp_path)
    await _enqueue_work(store, kind="memory_feed",
                        payload=_work_payload(5703, absorbed=True,
                                              unit="room-g1"))
    await _enqueue_work(store, kind="stimulus",
                        payload=_work_payload(5703, absorbed=True,
                                              unit="room-g1", person=None))
    convo = policy.conversation(PLATFORM, ROOM)
    assert convo is not None and convo.unit == "room-g1"
    import dataclasses
    policy._by_id[(PLATFORM, ROOM)] = dataclasses.replace(
        convo, unit="room-g2")

    routed: list[tuple[str | None, str]] = []

    class RoutedProbe(FakeMemoryStore):
        async def feed(self, *, peer_id, text, conversation_id,
                       metadata=None):
            room = policy.conversation(PLATFORM, conversation_id)
            routed.append((room.unit if room else None, text))
            return await super().feed(
                peer_id=peer_id, text=text,
                conversation_id=conversation_id, metadata=metadata)

    runner = _runner(store, redis_client, harness,
                     memory_feed={"memory": RoutedProbe()},
                     stimulus=_stimulus_wiring(store), allowlist=policy)
    for _ in range(2):
        await runner._execute(await store.claim_next(owner=runner._owner,
                                                     lease_seconds=60))
    rows = {r["kind"]: r for r in await _work_rows(store)}
    for kind in ("memory_feed", "stimulus"):
        assert rows[kind]["state"] == "intentional_skip"
        assert rows[kind]["result"]["reason"] == "unit binding moved"
    assert routed == []   # nothing reached the moved scope at all
    async with store._engine.connect() as conn:
        assert (await conn.execute(sa.select(STIMULUS))).all() == []
