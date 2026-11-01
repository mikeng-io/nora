"""Shared contract for the observation store. Every implementation runs every
test (AGENTS.md, «a contract suite scopes its database to the MODULE»).

Two implementations: `nora.infra.sql.observations.SqlObservationStore` over
real Postgres, and `nora.testing.observations.FakeObservationStore`. The
properties under test are the ones design/platform-observations 02 rules on:
replay dedupe by source digest, one committed row+head write, folded
current/as-of state from ONE fold, retained-and-relinkable unresolved
targets, partial-vs-complete snapshot semantics, and honest ordering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.models import EditTarget, NormalizedMessage
from nora.domain.observations import (
    Coverage,
    MembershipEntry,
    MembershipPayload,
    Observation,
    PollPayload,
    ReactionPayload,
    SourceScope,
)
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.observations import SqlObservationStore
from nora.testing.observations import FakeObservationStore

NOW = 1_790_000_000_000
SCOPE = SourceScope(platform="discord", account_key="acct",
                    conversation_id="room1")


@pytest.fixture(scope="module")
def _obs_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_observation_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield FakeObservationStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_obs_db"))
    try:
        yield SqlObservationStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate platform_observation, platform_object_state, "
                "messages restart identity cascade"
            ))
        await engine.dispose()


def _obs(kind: str = "poll", *, object_ref: str = "poll-1",
         operation: str = "create", payload=None, scope: SourceScope = SCOPE,
         **kw) -> Observation:
    if payload is None:
        payload = PollPayload(question="lunch?",
                              options=(("a", "yes"), ("b", "no")),
                              state="open")
    return Observation(object_kind=kind, scope=scope, object_ref=object_ref,
                       operation=operation, payload=payload,
                       received_ms=NOW, **kw)


def _digest(obs: Observation, upstream: str) -> str:
    """A stand-in for `canonical_digest`: the dedupe identity the caller
    computes. The store requires uniqueness, not a particular algorithm."""
    import hashlib
    key = ":".join([obs.scope.platform, obs.scope.account_key,
                    obs.scope.conversation_id, obs.object_kind, upstream])
    return hashlib.sha256(key.encode()).hexdigest()


async def _append(store: Any, obs: Observation, upstream: str = "u1",
                  event: str | None = None, **kw) -> int | None:
    return await store.append(
        observation=obs, event_id=event or f"ev:{upstream}",
        source_digest=_digest(obs, upstream), **kw)


async def test_append_records_and_replay_is_a_noop(store):
    obs = _obs()
    rid = await _append(store, obs, "u1")
    assert isinstance(rid, int)
    assert await _append(store, obs, "u1") is None  # same source event again
    head = await store.current(scope=SCOPE, kind="poll", object_ref="poll-1")
    assert head is not None and head.applied == 1
    assert head.state["question"] == "lunch?"


async def test_current_is_none_when_nothing_recorded(store):
    assert await store.current(scope=SCOPE, kind="poll",
                             object_ref="nope") is None
    assert await store.heads(scope=SCOPE) == []


async def test_incremental_head_equals_as_of_rebuild(store):
    ids = []
    ids.append(await _append(store, _obs(), "u1"))
    ids.append(await _append(store, _obs(
        operation="add", actor_id="v1", actor_status="known",
        payload=PollPayload(voter_ref="v1", option_ref="a")), "u2"))
    ids.append(await _append(store, _obs(
        operation="revise", payload=PollPayload(state="closed",
                                                close_ms=NOW)), "u3"))
    head = await store.current(scope=SCOPE, kind="poll", object_ref="poll-1")
    replay, more = await store.as_of(scope=SCOPE, kind="poll",
                                     object_ref="poll-1", through_id=max(ids))
    assert not more
    assert replay == head
    assert head.state["votes"] == {"v1": ["a"]}
    assert head.state["state"] == "closed"


async def test_as_of_boundaries(store):
    i1 = await _append(store, _obs(), "u1")
    i2 = await _append(store, _obs(
        operation="add", actor_id="v1", actor_status="known",
        payload=PollPayload(voter_ref="v1", option_ref="a")), "u2")
    early, more = await store.as_of(scope=SCOPE, kind="poll",
                                    object_ref="poll-1", through_id=i1)
    assert not more
    assert early.state["votes"] == {} and early.applied == 1
    later, more = await store.as_of(scope=SCOPE, kind="poll",
                                    object_ref="poll-1", through_id=i2)
    assert not more
    assert later.state["votes"] == {"v1": ["a"]}


async def test_response_before_definition_stays_unresolved(store):
    await _append(store, _obs(
        operation="add", actor_id="v1", actor_status="known",
        payload=PollPayload(voter_ref="v1", option_ref="a")), "u1")
    head = await store.current(scope=SCOPE, kind="poll", object_ref="poll-1")
    assert head.coverage.get("awaiting_definition") is True
    assert head.state["options"] == {}
    await _append(store, _obs(), "u2")
    head = await store.current(scope=SCOPE, kind="poll", object_ref="poll-1")
    assert head.coverage.get("awaiting_definition") is not True
    assert head.state["votes"] == {"v1": ["a"]}


async def test_out_of_order_delta_is_a_gap_not_a_lie(store):
    await _append(store, _obs(
        source_position=3, revision_scope="gw",
        payload=PollPayload(question="q", options=(("a", "A"),),
                            state="open")), "u1")
    # An older positioned delta arrives after a newer one applied.
    await _append(store, _obs(
        operation="add", actor_id="v1", actor_status="known",
        source_position=2, revision_scope="gw",
        payload=PollPayload(voter_ref="v1", option_ref="a")), "u2")
    head = await store.current(scope=SCOPE, kind="poll", object_ref="poll-1")
    assert head.ordering == "gap"
    assert head.coverage.get("ordering_gap") is True
    # History retains it; replay applies it at its own position.
    replay, more = await store.as_of(scope=SCOPE, kind="poll",
                                     object_ref="poll-1", through_id=2)
    assert not more
    assert replay.state["votes"] == {"v1": ["a"]}
    assert replay.ordering == "ordered"


async def test_partial_snapshot_merges_without_erasing(store):
    scope = SourceScope(platform="discord", account_key="acct",
                        conversation_id="room1")
    await _append(store, _obs(
        kind="membership", object_ref="channel:c1", operation="add",
        subject_ref="u0", payload=MembershipPayload(role="mod")), "u1")
    await _append(store, _obs(
        kind="membership", object_ref="channel:c1", operation="snapshot",
        payload=MembershipPayload(covered="channel",
                                  members=(MembershipEntry("u1"),)),
        coverage=Coverage(complete=False, covered="members")), "u2")
    head = await store.current(scope=scope, kind="membership",
                               object_ref="channel:c1")
    assert set(head.state["members"]) == {"u0", "u1"}
    # A complete snapshot of the same collection replaces it.
    await _append(store, _obs(
        kind="membership", object_ref="channel:c1", operation="snapshot",
        payload=MembershipPayload(covered="channel",
                                  members=(MembershipEntry("u9"),)),
        coverage=Coverage(complete=True, covered="members")), "u3")
    head = await store.current(scope=scope, kind="membership",
                               object_ref="channel:c1")
    assert set(head.state["members"]) == {"u9"}


async def test_anonymous_actor_records_no_invented_identity(store):
    await _append(store, _obs(
        kind="reaction", object_ref="msg:m1", operation="add",
        target=EditTarget(message_id="m1"), actor_status="anonymous",
        payload=ReactionPayload(emoji="👍")), "u1")
    head = await store.current(scope=SCOPE, kind="reaction",
                               object_ref="msg:m1")
    assert head.state["counts"] == {"👍": 1}
    assert head.state["standing"] == {}  # nobody's identity was made up


async def test_unresolved_target_is_retained_and_relinks_once(store, request):
    obs = _obs(kind="reaction", object_ref="msg:m42", operation="add",
               target=EditTarget(message_id="m42"), actor_id="u1",
               actor_status="known", payload=ReactionPayload(emoji="👍"))
    await _append(store, obs, "u1")
    unresolved = await store.unresolved_targets(scope=SCOPE)
    assert len(unresolved) == 1
    # The target arrives later: a real messages.id on SQL, any int on the
    # fake — insert the row first so the FK holds on both.
    message_id = await _insert_message(store, platform_message_id="m42")
    linked = await store.resolve_targets(
        scope=SCOPE, target_key="id:m42", message_id=message_id)
    assert linked == 1
    assert await store.resolve_targets(
        scope=SCOPE, target_key="id:m42", message_id=message_id) == 0
    assert await store.unresolved_targets(scope=SCOPE) == []


async def _insert_message(
    store: Any, *, platform_message_id: str, scope: SourceScope = SCOPE,
    sender_id: str = "u1",
) -> int:
    """A valid target row under the same scope as its observation."""
    engine = getattr(store, "_engine", None)
    if engine is None:
        return 77  # fake: any id
    return await SqlRecordStore(engine).append(message=NormalizedMessage(
        platform=scope.platform, conversation_id=scope.conversation_id,
        conversation_type="group", sender_id=sender_id, sender_name=None,
        timestamp=NOW, body="generated target", raw={}, message_id=platform_message_id))


async def test_tx_hook_runs_inside_the_write(store):
    seen: list[Any] = []

    def hook(conn: Any, obs_id: int) -> None:
        seen.append(obs_id)

    rid = await _append(store, _obs(), "u1", tx_hook=hook)
    assert seen == [rid]


async def test_scopes_are_isolated(store):
    other = SourceScope(platform="discord", account_key="acct",
                        conversation_id="room2")
    await _append(store, _obs(scope=other), "u1")
    assert await store.current(scope=SCOPE, kind="poll",
                               object_ref="poll-1") is None
    assert await store.heads(scope=SCOPE) == []
    assert len(await store.heads(scope=other)) == 1


async def test_stale_snapshot_never_overlays_newer_state(store):
    await _append(store, _obs(source_position=1, revision_scope="gw"), "u1")
    await _append(store, _obs(
        operation="add", actor_id="v1", actor_status="known",
        source_position=3, revision_scope="gw",
        payload=PollPayload(voter_ref="v1", option_ref="a")), "u2")
    await _append(store, _obs(
        operation="snapshot", source_position=2, revision_scope="gw",
        payload=PollPayload(option_counts={"a": 0}, voter_count=0)), "u3")
    head = await store.current(scope=SCOPE, kind="poll", object_ref="poll-1")
    assert head.state["votes"] == {"v1": ["a"]}
    assert head.state["option_counts"] == {}
    assert head.coverage.get("superseded_snapshots") == 1


async def test_timestamp_target_resolution_keeps_authors_separate(store):
    from nora.domain.observations import target_key

    scope = SourceScope(platform="signal", account_key="acct", conversation_id="room")
    target = EditTarget(sent_ts=NOW)
    for suffix, author in [("a", "author-a"), ("b", "author-b")]:
        await _append(
            store,
            _obs(scope=scope, object_ref="poll-" + suffix, target=target, target_author_id=author),
            suffix,
        )
    message_id = await _insert_message(
        store, platform_message_id="target-a", scope=scope, sender_id="author-a")
    resolved = await store.resolve_targets(
        scope=scope, target_key=target_key(target, author_id="author-a"),
        message_id=message_id)
    assert resolved == 1
    remaining = await store.unresolved_targets(scope=scope)
    assert [r.observation.object_ref for r in remaining] == ["poll-b"]
    assert remaining[0].observation.target_author_id == "author-b"


async def test_async_tx_hook_joins_the_append(store):
    """An awaitable hook is awaited inside the write — a downstream
    obligation that itself awaits commits with the observation, not after."""
    seen: list[Any] = []

    async def hook(conn: Any, obs_id: int) -> None:
        seen.append(obs_id)

    rid = await _append(store, _obs(), "u1", tx_hook=hook)
    assert seen == [rid]


async def test_async_tx_hook_failure_fails_the_append(store):
    async def hook(conn: Any, obs_id: int) -> None:
        raise RuntimeError("downstream obligation failed")

    with pytest.raises(RuntimeError):
        await _append(store, _obs(), "u1", tx_hook=hook)
    # The append rolled back with its hook: nothing recorded, replayable.
    assert await store.current(scope=SCOPE, kind="poll",
                             object_ref="poll-1") is None


async def test_as_of_is_bounded_and_reports_truncation(store):
    await _append(store, _obs(), "u1")
    i2 = await _append(store, _obs(
        operation="add", actor_id="v1", actor_status="known",
        payload=PollPayload(voter_ref="v1", option_ref="a")), "u2")
    head, more = await store.as_of(scope=SCOPE, kind="poll",
                                   object_ref="poll-1", through_id=i2, limit=1)
    # The bound cut the history: the head folds the EARLIEST limit rows, an
    # earlier point than requested — `more` is what keeps that honest.
    assert more is True
    assert head is not None and head.applied == 1
    assert head.state["votes"] == {}


async def test_by_ids_reads_only_inside_the_scope(store):
    i1 = await _append(store, _obs(), "u1")
    other = SourceScope(platform="discord", account_key="acct",
                        conversation_id="room2")
    i2 = await _append(store, _obs(scope=other), "u2")
    rows = await store.by_ids(scope=SCOPE, ids=(i1, i2))
    # The other scope's id never resolves through this scope's read.
    assert [r.id for r in rows] == [i1]
    assert await store.by_ids(scope=SCOPE, ids=()) == []
