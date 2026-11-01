"""The platform-observation durable ingest path against REAL Postgres +
Redis (design/platform-observations 02, 04).

Every test drives the production seam `test_bridge_queue` drives —
`StreamProducer.enqueue` → `StreamReader._register` → `WorkRunner._execute`
— with the REAL `SqlObservationStore`, `SqlStimulusStore`, harness
`IngestPipeline` and `permissive_policy` wired the way composition wires
them (nora/app/composition.py).

The contract under test: a co-delivered message and its observation
sidecar are TWO evidence paths — the utterance's audit + downstream
obligations, and the typed `platform_observation` row + folded
`platform_object_state` head + ordinal-90 `stimulus` obligation. Neither
sibling's outcome may silently consume the other's durable work; a
deterministic observation failure gets its own `failed_retained`
obligation rather than riding a `done` marker alone; and the stream entry
is XACKed only after durable ownership commits.
"""

import json
from dataclasses import replace

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.app.bridge_queue import StreamReader, WorkRunner, stream_name
from nora.domain.models import EditTarget, NormalizedMessage
from nora.domain.observations import SourceScope
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.observations import (
    SqlObservationStore,
    relink_arriving_targets,
    resolve_target_message_id,
)
from nora.infra.sql.schema import (
    BRIDGE_EVENT,
    BRIDGE_WORK,
    MESSAGES,
    PLATFORM_OBJECT_STATE,
    PLATFORM_OBSERVATION,
    STIMULUS,
)
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.ingest.stages.stimulus import Wiring as StimulusWiring
from nora.platform.bridge.envelope import Envelope, canonical_source_key, event_id
from nora.platform.bridge.queue import StreamProducer, ensure_group
from nora.platform.bridge.rehydrate import observation_from_dict
from nora.policy import Policy, permissive_policy
from tests.integration.test_bridge_queue import (
    ACCOUNT,
    CONV,
    KEY,
    PLATFORM,
    _envelope,
    _msg,
    _pending,
    _produce,
    _reader,
    _runner,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def bridge(pg_url, redis_client):
    """The same `bridge` fixture test_bridge_queue defines — a real
    SqlBridgeStore on a fresh database plus the stream's consumer group."""
    store = SqlBridgeStore(create_async_engine(pg_url))
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
                  key_version=1) -> Envelope:
    """An observation-ONLY envelope (v2, `message=None`). `kind` overrides
    let a test mint the malformed kind-mismatch shape."""
    k = kind or str(obs["kind"])
    sk = canonical_source_key(PLATFORM, ACCOUNT, CONV, k, upstream)
    return Envelope(
        platform=PLATFORM, account_key=ACCOUNT, kind=k,
        source_key=sk, event_id=event_id(sk, key=KEY, key_version=key_version),
        conversation_id=CONV, conversation_type="group",
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


def _wiring(store, *, self_ids=frozenset()) -> StimulusWiring:
    return StimulusWiring(
        store=SqlStimulusStore(store._engine),
        unit_of=lambda p, c: "nora",
        self_ids=self_ids, reply_quiet_seconds=90.0)


def _obs_runner(store, redis_client, harness, **kw) -> WorkRunner:
    """A WorkRunner wired the way composition wires it for observation
    ingest: allowlist + observation store, plus whatever the test adds
    (`stimulus`, `persons`)."""
    kw.setdefault("allowlist", permissive_policy())
    kw.setdefault("observations", SqlObservationStore(store._engine))
    return _runner(store, redis_client, harness, **kw)


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


async def _claim_run(store, runner, owner="w-test"):
    work = await store.claim_next(owner=owner, lease_seconds=60)
    assert work is not None, "expected a claimable obligation"
    await runner._execute(work)
    return work


async def _register(reader: StreamReader, env: Envelope, sid: str) -> None:
    await reader._register(sid, {"e": json.dumps(env.to_fields())})


async def test_observation_only_records_row_head_and_one_stimulus_then_acks(
        bridge, redis_client, harness):
    """The happy path: an observation-only v2 event commits the
    observation row, the folded head, the replay marker and exactly ONE
    ordinal-90 stimulus obligation, finishes the ingest, THEN acks."""
    store, _, stream = bridge
    obs = _obs(received_ms=5000, actor_id=MILES_ID, actor_status="known")
    env = _obs_envelope("obs-1", obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(
        store, redis_client, harness, stimulus=_wiring(store),
        persons=harness.handler._dispatcher._ingest._persons)
    await _register(reader, env, sid)
    assert await _pending(redis_client, stream) == 1  # registered, not acked
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "done"
    obs_rows = await _obs_rows(store)
    assert len(obs_rows) == 1
    assert obs_rows[0]["kind"] == "poll"
    assert obs_rows[0]["object_ref"] == "poll-1"
    assert obs_rows[0]["event_id"] == env.event_id
    # The folded head and the replay marker committed WITH the row.
    heads = await _head_rows(store)
    assert len(heads) == 1 and heads[0]["applied"] == 1
    assert event["payload"]["observation_row_id"] == obs_rows[0]["id"]
    work = await _work_rows(store, env.event_id)
    by_kind = {(w["kind"], w["ordinal"]): w for w in work}
    assert by_kind[("ingest", 0)]["state"] == "done"
    # Exactly ONE ordinal-90 stimulus obligation, carrying the grounded
    # ref AND both actor identities (platform id + resolved person).
    stim = by_kind[("stimulus", 90)]
    assert stim["payload"]["observation_row_id"] == obs_rows[0]["id"]
    assert stim["payload"]["actor_id"] == MILES_ID
    assert stim["payload"]["actor_person_id"] == "miles"
    assert stim["payload"]["unit"] == "nora"
    assert await _pending(redis_client, stream) == 0  # ack AFTER commits

    # The deferred observation stimulus consumes only grounded evidence:
    # the row it names exists, and its own run is idempotent-safe.
    stim_work = await _claim_run(store, runner)
    assert stim_work.kind == "stimulus"
    work = await _work_rows(store, env.event_id)
    assert next(w for w in work if w["kind"] == "stimulus")["state"] == "done"
    async with store._engine.connect() as conn:
        stimuli = (await conn.execute(sa.select(STIMULUS))).mappings().all()
    assert len(stimuli) == 1
    assert stimuli[0]["kind"] == "observation"
    assert stimuli[0]["item"] == "poll"
    assert stimuli[0]["ref"] == (
        f"platform_observation:{obs_rows[0]['id']}")
    assert stimuli[0]["unit"] == "nora"
    assert stimuli[0]["person_id"] == "miles"


async def test_observation_replay_and_rotated_alias_do_not_duplicate(
        bridge, redis_client, harness):
    """Redelivery of the same entry AND a rotated-correlation-key alias of
    the same source: the canonical event resolves, no second event row,
    no second ingest obligation, no second observation row or head."""
    store, redis, stream = bridge
    obs = _obs(received_ms=5100)
    env = _obs_envelope("obs-replay", obs, key_version=1)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness)
    await _register(reader, env, sid)
    await _claim_run(store, runner)
    assert await _pending(redis_client, stream) == 0

    # Redelivery of the SAME stream entry: registers against the existing
    # event — no new obligation, nothing re-appends.
    await _register(reader, env, sid)
    # A rotated correlation key mints a NEW alias event_id for the same
    # canonical source. The producer's Lua suppresses the second XADD and
    # returns the canonical id; a hand-forged alias entry must resolve to
    # the canonical event and discharge without registering anything.
    alias = _obs_envelope("obs-replay", obs, key_version=2)
    assert alias.event_id != env.event_id
    sid2, canonical = await StreamProducer(redis, stream).enqueue(alias)
    assert sid2 == sid and canonical == env.event_id
    await reader._register("9-9", {"e": json.dumps(alias.to_fields())})

    assert len(await _obs_rows(store)) == 1
    heads = await _head_rows(store)
    assert len(heads) == 1 and heads[0]["applied"] == 1
    work = await _work_rows(store, env.event_id)
    kinds = [(w["kind"], w["ordinal"]) for w in work]
    assert kinds.count(("ingest", 0)) == 1
    assert kinds.count(("stimulus", 90)) == 1
    async with store._engine.connect() as conn:
        events = (await conn.execute(sa.select(BRIDGE_EVENT))).all()
    assert len(events) == 1  # the alias mapped, never a second event row


async def test_message_sidecar_keeps_both_evidence_paths(
        bridge, redis_client, harness):
    """A message envelope carrying an observation sidecar: the utterance
    commits its audit + downstream obligations, the sidecar commits its
    row + head + ordinal-90 stimulus — one event, two durable facts, and
    the utterance's completion does not consume the observation's work."""
    store, _, stream = bridge
    obs = _obs(received_ms=5200, actor_id=MILES_ID, actor_status="known")
    env = _sidecar_envelope("a plain message", 5200, obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(
        store, redis_client, harness, stimulus=_wiring(store),
        persons=harness.handler._dispatcher._ingest._persons)
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "done"
    assert event["admission"]["observation"] == "ok"
    msg_row_id = event["payload"]["message_row_id"]
    assert msg_row_id
    obs_rows = await _obs_rows(store)
    assert len(obs_rows) == 1
    assert obs_rows[0]["event_id"] == env.event_id
    heads = await _head_rows(store)
    assert len(heads) == 1 and heads[0]["applied"] == 1
    # The message's own stimulus (ordinal 1, wired above) AND the
    # observation's (ordinal 90) are distinct obligations — the sidecar's
    # wake was never folded into the utterance's.
    work = await _work_rows(store, env.event_id)
    kinds = [(w["kind"], w["ordinal"]) for w in work]
    assert ("ingest", 0) in kinds
    assert ("stimulus", 90) in kinds
    stim90 = next(w for w in work if w["ordinal"] == 90)
    assert stim90["payload"]["observation_row_id"] == obs_rows[0]["id"]
    assert stim90["payload"]["actor_id"] == MILES_ID
    assert stim90["payload"]["actor_person_id"] == "miles"
    assert await _pending(redis_client, stream) == 0


async def test_malformed_sidecar_is_retained_while_utterance_completes(
        bridge, redis_client, harness):
    """A sidecar observation whose scope does not match the envelope's is
    a DETERMINISTIC refusal: the utterance still delivers and completes,
    `admission.observation` records the refusal, and the refusal gets its
    own terminal `failed_retained` obligation — inspectable, never a
    silent consumption under the sibling's `done`."""
    store, _, stream = bridge
    bad = _obs(received_ms=5300, conversation_id="other-room")
    env = _sidecar_envelope("a message with a bad sidecar", 5300, bad)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness,
                         stimulus=_wiring(store))
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "done"
    assert event["admission"]["observation"].startswith("refused:")
    async with store._engine.connect() as conn:
        assert len((await conn.execute(sa.select(MESSAGES))).all()) == 1
    assert await _obs_rows(store) == []
    assert await _head_rows(store) == []
    work = await _work_rows(store, env.event_id)
    retained = [w for w in work
                if w["kind"] == "ingest" and w["ordinal"] == 91]
    assert len(retained) == 1
    assert retained[0]["state"] == "failed_retained"
    assert retained[0]["result"]["reason"].startswith("refused:")
    assert retained[0]["payload"]["part"] == "observation"
    # The retained row is terminal — the event discharges, nothing retries.
    assert await _pending(redis_client, stream) == 0


async def test_observation_only_malformed_is_failed_retained(
        bridge, redis_client, harness):
    """An observation-only event that fails the wire contract (scope
    mismatch) is a retained failure — the event says what it could not
    record, durably; the entry still discharges."""
    store, _, stream = bridge
    bad = _obs(received_ms=5400, conversation_id="other-room")
    env = _obs_envelope("obs-bad-scope", bad)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness)
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "failed_retained"
    assert event["outcome_reason"].startswith("malformed observation")
    assert await _obs_rows(store) == []
    work = await _work_rows(store, env.event_id)
    assert len(work) == 1
    assert work[0]["state"] == "failed_retained"
    assert await _pending(redis_client, stream) == 0


async def test_observation_only_kind_mismatch_is_failed_retained(
        bridge, redis_client, harness):
    """A typed envelope naming `poll` whose observation says `room` —
    payload-kind consistency failure, refused and retained."""
    store, _, stream = bridge
    obs = _obs(kind="room", object_ref="room-1", operation="snapshot",
               payload={"fields": {"name": "x"}}, received_ms=5410)
    env = _obs_envelope("obs-kind-mismatch", obs, kind="poll")
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness)
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "failed_retained"
    assert "poll" in event["outcome_reason"]
    assert "room" in event["outcome_reason"]
    assert await _obs_rows(store) == []
    work = await _work_rows(store, env.event_id)
    assert len(work) == 1 and work[0]["state"] == "failed_retained"
    assert await _pending(redis_client, stream) == 0


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


async def test_observation_append_tx_rolls_back_row_marker_and_obligation(
        bridge, redis_client, harness):
    """The hook ran — the marker UPDATE and the stimulus enqueue — then
    the append raised: EVERYTHING rolls back (row, head, marker,
    obligation), the work retries, and the retry commits them together."""
    store, _, stream = bridge
    obs = _obs(received_ms=5600)
    env = _obs_envelope("obs-hook-fail", obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    real = SqlObservationStore(store._engine)
    runner = _obs_runner(store, redis_client, harness,
                         observations=_HookExplodingStore(real),
                         stimulus=_wiring(store))
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "open"          # nothing finished
    assert "observation_row_id" not in event["payload"]
    assert await _obs_rows(store) == []
    assert await _head_rows(store) == []
    work = await _work_rows(store, env.event_id)
    assert len(work) == 1 and work[0]["state"] == "retryable_failure"
    assert await _pending(redis_client, stream) == 1  # still pending

    # Restore the honest store: the retry replays the append clean —
    # row + head + marker + obligation commit as one, then ack.
    runner._observations = real
    await _claim_run(store, runner)
    event = await store.event(env.event_id)
    assert event["state"] == "done"
    obs_rows = await _obs_rows(store)
    assert len(obs_rows) == 1
    assert len(await _head_rows(store)) == 1
    assert event["payload"]["observation_row_id"] == obs_rows[0]["id"]
    work = await _work_rows(store, env.event_id)
    kinds = [(w["kind"], w["ordinal"]) for w in work]
    assert ("ingest", 0) in kinds and ("stimulus", 90) in kinds
    assert await _pending(redis_client, stream) == 0


async def test_kill_after_append_before_finish_recovers_from_marker(
        bridge, redis_client, harness, monkeypatch):
    """The append transaction committed (row + head + marker + stimulus
    obligation) and the runner died before the terminal finish: the
    event stays open, the ingest obligation retries, and the retry
    resumes from the committed marker — no second row, no second
    obligation."""
    store, _, stream = bridge
    obs = _obs(received_ms=5700)
    env = _obs_envelope("obs-kill-window", obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness,
                         stimulus=_wiring(store))
    await _register(reader, env, sid)

    real_set_state = store.set_event_state
    killed = []

    async def die_once(conn, event_id, state, **kw):
        if not killed:
            killed.append(1)
            raise RuntimeError("killed between append and finish")
        return await real_set_state(conn, event_id, state, **kw)

    monkeypatch.setattr(store, "set_event_state", die_once)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    assert event["state"] == "open"
    # Everything the append transaction owned is durable: the row, the
    # folded head, the replay marker AND the stimulus obligation.
    obs_rows = await _obs_rows(store)
    assert len(obs_rows) == 1
    assert event["payload"]["observation_row_id"] == obs_rows[0]["id"]
    work = await _work_rows(store, env.event_id)
    ingest = next(w for w in work if w["kind"] == "ingest")
    assert ingest["state"] == "retryable_failure"
    stim = [w for w in work if w["ordinal"] == 90]
    assert len(stim) == 1 and stim[0]["state"] == "pending"
    assert await _pending(redis_client, stream) == 1

    monkeypatch.undo()
    work = await _claim_run(store, runner)
    assert work.kind == "ingest"  # the marker replay: finish, no re-append
    event = await store.event(env.event_id)
    assert event["state"] == "done"
    assert len(await _obs_rows(store)) == 1
    work = await _claim_run(store, runner)
    assert work.kind == "stimulus"
    work = await _work_rows(store, env.event_id)
    assert all(w["state"] in ("done", "intentional_skip") for w in work)
    assert await _pending(redis_client, stream) == 0


async def test_ack_failure_leaves_entry_pending_until_reclaim(
        bridge, redis_client, harness, monkeypatch):
    """The XACK itself fails AFTER the durable commits: the entry stays
    pending (the work row is already terminal — a lost ACK, not lost
    work), the remaining obligations still run, and re-registration
    discharges the entry once the event owns nothing open."""
    store, _, stream = bridge
    obs = _obs(received_ms=5800)
    env = _obs_envelope("obs-ack-fail", obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness,
                         stimulus=_wiring(store))
    await _register(reader, env, sid)

    async def noack(*a, **kw):
        raise RuntimeError("redis gone")

    monkeypatch.setattr(redis_client, "xack", noack)
    await _claim_run(store, runner)

    # Durable truth says done; only the stream entry pends for reclaim.
    event = await store.event(env.event_id)
    assert event["state"] == "done"
    assert len(await _obs_rows(store)) == 1
    work = await _work_rows(store, env.event_id)
    ingest = next(w for w in work if w["kind"] == "ingest")
    assert ingest["state"] == "done"
    assert await _pending(redis_client, stream) == 1

    # The remaining obligation still runs — its own commit is durable
    # even though the stream entry it never owned stays pending.
    monkeypatch.undo()
    stim = await _claim_run(store, runner)
    assert stim.kind == "stimulus"
    assert await _pending(redis_client, stream) == 1  # entry still waits

    # Re-registration sees the event's obligations all terminal and
    # discharges — the approved recovery for a lost ACK.
    await _register(reader, env, sid)
    assert await _pending(redis_client, stream) == 0


async def test_target_resolves_when_the_message_arrived_first(
        bridge, redis_client, harness):
    """The observation lands after its target: `resolve_target_message_id`
    names the committed row at append time."""
    store, _, stream = bridge
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness)

    msg_env = _envelope("target first", 6000)
    sid = await _produce(redis_client, stream, msg_env)
    await _register(reader, msg_env, sid)
    await _claim_run(store, runner)
    msg_row_id = (await store.event(msg_env.event_id))["payload"][
        "message_row_id"]

    obs = _obs(kind="message_metadata", object_ref="mm-1",
               operation="create", payload={"view_once": True},
               received_ms=6010, target={"message_id": "m6000"})
    env = _obs_envelope("obs-mm-target", obs)
    sid = await _produce(redis_client, stream, env)
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    rows = await _obs_rows(store)
    assert len(rows) == 1
    assert rows[0]["target_message_id"] == msg_row_id


async def test_target_links_when_the_message_arrives_later(
        bridge, redis_client, harness):
    """The observation lands BEFORE its target: the append retains
    `target_message_id NULL`, and the message's own ingest transaction
    re-links it — the link commits with the arrival that made it
    possible, in the same row commit."""
    store, _, stream = bridge
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness)

    obs = _obs(kind="message_metadata", object_ref="mm-2",
               operation="create", payload={"view_once": True},
               received_ms=6100, target={"message_id": "m6100"})
    env = _obs_envelope("obs-mm-early", obs)
    sid = await _produce(redis_client, stream, env)
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    rows = await _obs_rows(store)
    assert len(rows) == 1
    assert rows[0]["target_message_id"] is None  # retained unresolved

    msg_env = _envelope("the target arrives", 6100)
    sid = await _produce(redis_client, stream, msg_env)
    await _register(reader, msg_env, sid)
    await _claim_run(store, runner)
    msg_row_id = (await store.event(msg_env.event_id))["payload"][
        "message_row_id"]

    rows = await _obs_rows(store)
    assert len(rows) == 1
    assert rows[0]["target_message_id"] == msg_row_id


async def test_ambiguous_timestamp_target_stays_unresolved(
        bridge, redis_client, harness):
    """Two senders posted at the same timestamp: a bare `ts:` target has
    two provable answers and resolves to NEITHER — an arbitrary same-
    timestamp pick would bind evidence to a row it may not name. An
    author-qualified target of the same timestamp resolves the one that
    author sent."""
    store, _, stream = bridge
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(
        store, redis_client, harness,
        persons=harness.handler._dispatcher._ingest._persons)

    async def message_at(body, ts, sender, upstream):
        m = _msg(body, ts)
        m["sender_id"] = sender
        m["message_id"] = f"m-{upstream}"
        sk = canonical_source_key(PLATFORM, ACCOUNT, CONV, "message",
                                  upstream)
        env = Envelope(
            platform=PLATFORM, account_key=ACCOUNT, kind="message",
            source_key=sk,
            event_id=event_id(sk, key=KEY, key_version=1),
            conversation_id=CONV, conversation_type="group",
            sender_id=sender, sender_name="Sender", sent_ms=ts,
            received_ms=ts + 1, queued_ms=ts + 2,
            time_quality="upstream", message=m, admission_rev=1)
        sid = await _produce(redis_client, stream, env)
        await _register(reader, env, sid)
        await _claim_run(store, runner)
        return env

    env_a = await message_at("A at 6200", 6200, "+10000000001", "amb-a")
    await message_at("B at 6200", 6200, "+10000000002", "amb-b")
    row_a = (await store.event(env_a.event_id))["payload"]["message_row_id"]

    # Bare timestamp: two provable answers → neither.
    obs = _obs(kind="message_metadata", object_ref="mm-amb",
               operation="create", payload={"view_once": True},
               received_ms=6210, target={"sent_ts": 6200})
    env = _obs_envelope("obs-amb", obs)
    sid = await _produce(redis_client, stream, env)
    await _register(reader, env, sid)
    await _claim_run(store, runner)
    rows = await _obs_rows(store)
    assert len(rows) == 1
    assert rows[0]["target_message_id"] is None

    # The author-qualified target of the SAME timestamp resolves the one
    # that author sent — the author column is the disambiguator, not row
    # order.
    obs = _obs(kind="message_metadata", object_ref="mm-amb-a",
               operation="create", payload={"view_once": True},
               received_ms=6211, target={"sent_ts": 6200},
               target_author_id="+10000000001")
    env = _obs_envelope("obs-amb-author", obs)
    sid = await _produce(redis_client, stream, env)
    await _register(reader, env, sid)
    await _claim_run(store, runner)
    rows = await _obs_rows(store)
    assert len(rows) == 2
    assert rows[1]["target_message_id"] == row_a


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


async def test_self_authored_observation_raises_no_stimulus(
        bridge, redis_client, harness):
    """The echo rule on the platform actor id: an observation whose
    `actor_id` is one of Nora's own still records the row + head, but
    its stimulus obligation intentionally skips — self_ids are
    platform-level ids, and the check must run against `actor_id`, not
    the resolved `actor_person_id`."""
    store, _, stream = bridge
    obs = _obs(received_ms=6400, actor_id=ACCOUNT, actor_status="known")
    env = _obs_envelope("obs-self", obs)
    sid = await _produce(redis_client, stream, env)
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness,
                         stimulus=_wiring(store,
                                          self_ids=frozenset({ACCOUNT})))
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    assert len(await _obs_rows(store)) == 1
    work = await _work_rows(store, env.event_id)
    stim = next(w for w in work if w["kind"] == "stimulus")
    assert stim["payload"]["actor_id"] == ACCOUNT
    work = await _claim_run(store, runner)
    assert work.kind == "stimulus"
    work = await _work_rows(store, env.event_id)
    stim = next(w for w in work if w["kind"] == "stimulus")
    assert stim["state"] == "intentional_skip"
    assert stim["result"]["reason"] == "self_authored"
    async with store._engine.connect() as conn:
        assert (await conn.execute(sa.select(STIMULUS))).all() == []
    assert await _pending(redis_client, stream) == 0


async def test_reaction_sibling_does_not_consume_its_sidecar(
        bridge, redis_client, harness):
    """A touch (reaction) envelope carrying a sidecar: the sibling's
    outcome is terminal for the UTTERANCE path only. `ingest_ended`
    commits the sibling's answer first, then the sidecar appends its own
    row + head + stimulus obligation — a silent-path sibling can never
    discharge unseen observation work."""
    store, _, stream = bridge
    reader = _reader(redis_client, store, stream)
    runner = _obs_runner(store, redis_client, harness,
                         stimulus=_wiring(store))

    # The reaction's target must exist first.
    msg_env = _envelope("react to me", 7000)
    sid = await _produce(redis_client, stream, msg_env)
    await _register(reader, msg_env, sid)
    await _claim_run(store, runner)

    m = _msg("", 7001)
    m["touch"] = {"kind": "reaction",
                  "target": {"message_id": "m7000", "sent_ts": None,
                             "mentions_known": True},
                  "target_author": "", "emoji": "+", "removed": False}
    m["body"] = ""
    sk = canonical_source_key(PLATFORM, ACCOUNT, CONV, "reaction", "r-1")
    env = Envelope(
        platform=PLATFORM, account_key=ACCOUNT, kind="reaction",
        source_key=sk, event_id=event_id(sk, key=KEY, key_version=1),
        conversation_id=CONV, conversation_type="group",
        sender_id="+00000000002", sender_name="Sender", sent_ms=7001,
        received_ms=7002, queued_ms=7003, time_quality="upstream",
        message=m, observation=_obs(received_ms=7001), admission_rev=1)
    sid = await _produce(redis_client, stream, env)
    await _register(reader, env, sid)
    await _claim_run(store, runner)

    event = await store.event(env.event_id)
    # The touch's terminal answer committed first (the replay marker);
    # the sidecar's durable path committed regardless.
    assert event["state"] == "intentional_skip"
    assert event["payload"]["ingest_ended"]["outcome"] == "reaction_recorded"
    obs_rows = await _obs_rows(store)
    assert len(obs_rows) == 1
    heads = await _head_rows(store)
    assert len(heads) == 1
    work = await _work_rows(store, env.event_id)
    kinds = [(w["kind"], w["ordinal"]) for w in work]
    assert ("stimulus", 90) in kinds
    assert event["admission"]["observation"] == "ok"
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
