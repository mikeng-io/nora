"""The platform-observation durable ingest path against REAL Postgres +
Redis (docs/design/platform/observations.md, 04).

Every test drives the production seam `test_bridge_queue` drives —
`StreamProducer.enqueue` → `StreamReader._register` → `WorkRunner._execute`
— with the REAL `SqlObservationStore`, `SqlStimulusStore`, harness
`IngestPipeline` and `permissive_policy` wired the way composition wires
them (nora/app/composition.py).

The contract under test (docs/design/platform/observations.md): a co-delivered
message and its observation sidecar are ONE durable source obligation —
the typed `platform_observation` row + folded `platform_object_state`
head + ONE ordinal-90 `stimulus` obligation naming BOTH refs — never two
independent wakes of one event. The utterance's audit + downstream
obligations still commit; the merged stimulus carries its cues, `waits`
and person. A deterministic observation failure gets its own
`failed_retained` obligation rather than riding a `done` marker alone,
and the stream entry is XACKed only after durable ownership commits.
"""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from discord.raw_models import RawThreadDeleteEvent, RawThreadUpdateEvent
from sqlalchemy.ext.asyncio import create_async_engine

from nora.app.bridge_queue import StreamReader, stream_name
from nora.domain.models import EditTarget, NormalizedMessage
from nora.domain.observations import Observation, SourceScope
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.observations import (
    SqlObservationStore,
    relink_arriving_targets,
    resolve_target_message_id,
)
from nora.infra.sql.schema import (
    BRIDGE_WORK,
    MESSAGES,
    PLATFORM_OBJECT_STATE,
    PLATFORM_OBSERVATION,
)
from nora.platform.bridge.envelope import Envelope, canonical_source_key, event_id
from nora.platform.bridge.queue import CONSUMER_GROUP, ensure_group
from nora.platform.bridge.rehydrate import (
    observation_from_dict,
    observation_to_dict,
)
from nora.platform.discord.parser import (
    parse_discord_thread_create,
    parse_discord_thread_delete,
    parse_discord_thread_update,
)
from nora.policy import Policy, permissive_policy
from tests.integration.test_bridge_queue import (
    ACCOUNT,
    CONV,
    KEY,
    PLATFORM,
    _envelope,
    _pending,
    _produce,
    _reader,
    _runner,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def bridge(clean_pg_url, redis_client):
    """The same `bridge` fixture test_bridge_queue defines — a real
    SqlBridgeStore on a fresh database plus the stream's consumer group."""
    store = SqlBridgeStore(create_async_engine(clean_pg_url))
    stream = stream_name(PLATFORM, ACCOUNT)
    await ensure_group(redis_client, stream)
    yield store, redis_client, stream
    await store._engine.dispose()

POLL_CREATE = {
    "question": "lunch?", "state": "open",
    "options": [{"ref": "a", "label": "yes"},
                {"ref": "b", "label": "no"}],
}
MILES_ID = "+852111"  # the harness identity's Signal id for Miles


def _obs(*, kind="poll", object_ref="poll-1", operation="create",
         payload=None, target=None, target_author_id="",
         actor_id="", actor_status="not_supplied",
         occurred_ms=None, received_ms=5000, conversation_id=CONV):
    """A serialized v2 observation — `observation_to_dict`'s exact wire
    shape (`rehydrate.observation_from_dict` re-validates it)."""
    return {
        "contract": 1,
        "kind": kind,
        "scope": {"platform": PLATFORM, "account_key": ACCOUNT,
                  "conversation_id": conversation_id, "thread": ""},
        "object_ref": object_ref,
        "target": target,
        "target_author_id": target_author_id,
        "operation": operation,
        "subject_ref": "",
        "actor_id": actor_id,
        "actor_status": actor_status,
        "occurred_ms": occurred_ms,
        "received_ms": received_ms,
        "time_quality": "bridge_local",
        "source_revision": None,
        "source_position": None,
        "revision_scope": "",
        "coverage": {},
        "payload": payload if payload is not None else POLL_CREATE,
    }


def _obs_envelope(upstream: str, obs: dict, *, kind=None,
                  key_version=1, conv=CONV) -> Envelope:
    """An observation-ONLY envelope (v2, `message=None`). `kind` overrides
    let a test mint the malformed kind-mismatch shape."""
    k = kind or str(obs["kind"])
    sk = canonical_source_key(PLATFORM, ACCOUNT, conv, k, upstream)
    return Envelope(
        platform=PLATFORM, account_key=ACCOUNT, kind=k,
        source_key=sk, event_id=event_id(sk, key=KEY, key_version=key_version),
        conversation_id=conv, conversation_type="group",
        sender_id=str(obs.get("actor_id") or ""), sender_name=None,
        sent_ms=obs.get("occurred_ms"),
        received_ms=int(obs.get("received_ms") or 0),
        queued_ms=int(obs.get("received_ms") or 0) + 1,
        time_quality="bridge_local",
        message=None, observation=obs, admission_rev=1)


def _sidecar_envelope(body: str, ts: int, obs: dict) -> Envelope:
    """A v1-shape message envelope whose v2 observation field co-delivers
    typed evidence — the sidecar case."""
    return replace(_envelope(body, ts), observation=obs)






async def _obs_rows(store):
    async with store._engine.connect() as conn:
        return (await conn.execute(
            sa.select(PLATFORM_OBSERVATION))).mappings().all()


async def _head_rows(store):
    async with store._engine.connect() as conn:
        return (await conn.execute(
            sa.select(PLATFORM_OBJECT_STATE))).mappings().all()


async def _work_rows(store, event_id: str):
    async with store._engine.connect() as conn:
        return (await conn.execute(
            sa.select(BRIDGE_WORK).where(
                BRIDGE_WORK.c.event_id == event_id)
            .order_by(BRIDGE_WORK.c.id))).mappings().all()


async def _claim_run(store, runner, owner="w-test", *, kind="ingest"):
    work = await store.claim_next(owner=owner, lease_seconds=60, kinds=(kind,))
    assert work is not None, "expected a claimable obligation"
    await runner._execute(work)
    return work


async def _register(reader: StreamReader, env: Envelope, sid: str) -> None:
    await reader._redis.xreadgroup(
        CONSUMER_GROUP, reader._consumer, {reader.stream: ">"}, count=1)
    await reader._register(sid, {"e": json.dumps(env.to_fields())})














async def test_observation_only_unwired_store_is_failed_retained(
        bridge, redis_client, harness):
    """A deployment that never wired the observation store: `unavailable`
    (R-27), not `nothing_found` — the obs-only event retains as a
    failure, discharges, and nothing is persisted."""
    store, _, stream = bridge
    obs = _obs(received_ms=5500)
    env = _obs_envelope("obs-unwired", obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _runner(store, redis_client, harness,
                     allowlist=permissive_policy())  # observations=None
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "failed_retained"
    assert event["outcome_reason"] == "observation store unwired"
    assert await _obs_rows(store) == []
    work = await _work_rows(store, env.event_id)
    assert len(work) == 1 and work[0]["state"] == "failed_retained"
    assert await _pending(redis_client, stream) == 0


async def test_sidecar_unwired_store_delivers_utterance_and_retains(
        bridge, redis_client, harness):
    """The same unwired store under a message+sidecar: the utterance
    delivers, the observation refusal is `unavailable:` — and that
    refusal still gets its own durable `failed_retained` obligation."""
    store, _, stream = bridge
    obs = _obs(received_ms=5510)
    env = _sidecar_envelope("deliverable anyway", 5510, obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _runner(store, redis_client, harness,
                     allowlist=permissive_policy())  # observations=None
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "done"
    assert event["admission"]["observation"] == (
        "unavailable:observation store unwired")
    async with store._engine.connect() as conn:
        assert len((await conn.execute(sa.select(MESSAGES))).all()) == 1
    assert await _obs_rows(store) == []
    work = await _work_rows(store, env.event_id)
    retained = [w for w in work
                if w["kind"] == "ingest" and w["ordinal"] == 91]
    assert len(retained) == 1
    assert retained[0]["state"] == "failed_retained"
    assert retained[0]["result"]["reason"] == (
        "unavailable:observation store unwired")
    assert await _pending(redis_client, stream) == 0


class _HookExplodingStore:
    """Wraps the real `SqlObservationStore`: the caller's `tx_hook` runs
    (the bridge-event marker UPDATE + the stimulus enqueue — both inside
    the append's transaction) and THEN raises. Anything the hook wrote
    must roll back WITH the row: a half-committed observation is worse
    than none."""

    def __init__(self, inner: SqlObservationStore) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def append(self, *, observation, event_id, source_digest,
                     target_message_id=None, tx_hook=None):
        async def boom(conn, obs_id):
            if tx_hook is not None:
                await tx_hook(conn, obs_id)
            raise RuntimeError("hook fault")
        return await self._inner.append(
            observation=observation, event_id=event_id,
            source_digest=source_digest,
            target_message_id=target_message_id, tx_hook=boom)














async def test_observation_only_scope_refused_is_intentional_skip(
        bridge, redis_client, harness):
    """A room the allowlist does not absorb never persists evidence —
    `scope_refused` is a legitimate non-admission (intentional_skip),
    not a retained failure and not an implicit store."""
    store, _, stream = bridge
    obs = _obs(received_ms=6300)
    env = _obs_envelope("obs-refused-room", obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _runner(store, redis_client, harness,
                     allowlist=Policy(conversations=()),
                     observations=SqlObservationStore(store._engine))
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "intentional_skip"
    assert event["outcome_reason"] == "scope_refused"
    assert await _obs_rows(store) == []
    work = await _work_rows(store, env.event_id)
    assert len(work) == 1 and work[0]["state"] == "intentional_skip"
    assert await _pending(redis_client, stream) == 0








async def test_target_uniqueness_is_measured_after_author_filtering(bridge):
    store, _, _ = bridge
    audit = SqlRecordStore(store._engine)

    async def put(sender, mid):
        return await audit.append(message=NormalizedMessage(
            platform=PLATFORM, conversation_id=CONV, conversation_type="group",
            sender_id=sender, sender_name=None, timestamp=7200,
            body="generated target", raw={},
            message_id=mid))

    first = await put("author-a", "native-a")
    for i in range(21):
        await put("other-author", f"native-other-{i}")
    late = await put("author-b", "native-b")
    assert await resolve_target_message_id(
        store._engine, platform=PLATFORM, conversation_id=CONV,
        target=EditTarget(sent_ts=7200), target_author_id="author-b") == late
    assert await resolve_target_message_id(
        store._engine, platform=PLATFORM, conversation_id=CONV,
        target=EditTarget(sent_ts=7200), target_author_id="author-a") == first
    # A second answer beyond the old twenty-row page makes it ambiguous.
    await put("author-a", "native-a")
    for target in (EditTarget(sent_ts=7200), EditTarget(message_id="native-a")):
        assert await resolve_target_message_id(
            store._engine, platform=PLATFORM, conversation_id=CONV,
            target=target, target_author_id="author-a") is None


async def test_late_target_relinks_history_without_a_query_per_observation(bridge):
    store, _, _ = bridge
    observations = SqlObservationStore(store._engine)
    scope = SourceScope(platform=PLATFORM, account_key=ACCOUNT, conversation_id=CONV)
    for i in range(32):
        obs = observation_from_dict(_obs(
            kind="message_metadata", object_ref="late-metadata",
            payload={"view_once": True}, target={"message_id": "native-late"},
            target_author_id="author-a", received_ms=7300 + i))
        await observations.append(
            observation=obs, event_id=f"late-event-{i}", source_digest=f"late-source-{i}")
    target_id = await SqlRecordStore(store._engine).append(message=NormalizedMessage(
        platform=PLATFORM, conversation_id=CONV, conversation_type="group",
        sender_id="author-a", sender_name=None, timestamp=7300,
        body="generated late target", raw={},
        message_id="native-late"))
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(store._engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with store._engine.begin() as conn:
            linked = await relink_arriving_targets(
                conn, scope=scope, platform_message_id="native-late", sent_ts=7300,
                sender_id="author-a")
    finally:
        sa.event.remove(store._engine.sync_engine, "before_cursor_execute", record_statement)
    assert linked == 32
    rows = await _obs_rows(store)
    assert len(rows) == 32 and all(r["target_message_id"] == target_id for r in rows)
    assert len(statements) <= 12  # bounded by target addresses, not history length


# ── Discord thread observations: room scope, one wire boundary ───────────────
#
# A Discord thread is a span inside its parent channel's room — the
# `Envelope`/`bridge_work` boundary carries no thread sub-scope, and
# `WorkRunner._observation_scope` binds `expect_scope` to `thread=""`.
# These guards drive the REAL `parse_discord_thread_*` output through the
# bridge's own projection shape into the real ingest seam: create/update/
# delete commit typed state + one stimulus, an unadmitted parent room is
# refused, and a non-empty `scope.thread` on the wire — the defect's exact
# shape — is retained as malformed, proving `expect_scope` was not
# weakened to admit it.

DISCORD_FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "platform_observations" / "discord")
THREAD_PARENT = "123456789012345600/123456789012345601"
THREAD_ID = "123456789012345610"


def _thread_obs(fixture: str) -> Observation:
    """The real `parse_discord_thread_*` output for the generated gateway
    fixtures — the observation `_emit_observation` would hand the sink.
    The SimpleNamespace mirrors `discord.Thread`'s attribute names on
    discord.py 2.7.1 (verified against its `threads.py`); the raw-event
    DTOs are the SDK's own."""
    d = json.loads((DISCORD_FIXTURES / fixture).read_text())["payload"]["d"]
    if fixture == "thread_create.json":
        thread = SimpleNamespace(
            id=int(d["id"]), name=d["name"], parent_id=int(d["parent_id"]),
            owner_id=int(d["owner_id"]), archived=False, locked=False,
            auto_archive_duration=1440, slowmode_delay=0,
            type=SimpleNamespace(value=d["type"]),
            guild=SimpleNamespace(id=int(d["guild_id"])))
        return parse_discord_thread_create(thread, account_key=ACCOUNT)
    if fixture == "thread_update.json":
        return parse_discord_thread_update(
            RawThreadUpdateEvent(d).data, account_key=ACCOUNT)
    return parse_discord_thread_delete(
        RawThreadDeleteEvent(d), account_key=ACCOUNT)


def _emit_projected(obs, upstream: str) -> Envelope:
    """`DiscordBridge._emit_observation`'s room projection, mirrored: the
    envelope's `conversation_id` IS the observation's room scope and the
    serialized observation rides unchanged — the exact shape
    `expect_scope` then binds at ingest."""
    conv = obs.scope.conversation_id
    sk = canonical_source_key(
        obs.scope.platform, ACCOUNT, conv, obs.object_kind, upstream)
    received = 6500
    return Envelope(
        platform=obs.scope.platform, account_key=ACCOUNT,
        kind=obs.object_kind, source_key=sk,
        event_id=event_id(sk, key=KEY, key_version=1),
        conversation_id=conv, conversation_type="group",
        sender_id=obs.actor_id or "", sender_name=None,
        sent_ms=obs.occurred_ms,
        received_ms=received, queued_ms=received + 1,
        time_quality="upstream" if obs.occurred_ms else "bridge_local",
        message=None,
        observation=observation_to_dict(
            replace(obs, received_ms=received)),
        admission_rev=1)




async def test_discord_thread_observation_in_an_unadmitted_room_is_refused(
        bridge, redis_client, harness):
    """A thread event whose parent room is not absorbed never persists
    evidence — the configured-room authorization the repair was
    forbidden to widen, unchanged."""
    store, _, stream = bridge
    obs = _thread_obs("thread_create.json")
    env = _emit_projected(obs, "obs-thread-refused")
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _runner(store, redis_client, harness,
                     allowlist=Policy(conversations=()),
                     observations=SqlObservationStore(store._engine))
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "intentional_skip"
    assert event["outcome_reason"] == "scope_refused"
    assert await _obs_rows(store) == []
    assert await _pending(redis_client, stream) == 0


