"""Shared contract for the UNION reaction projection. Every implementation
pair runs every test (AGENTS.md, «a contract suite scopes its database to the
MODULE»).

Two implementations: `SqlRecordStore` + `SqlObservationStore` over real
Postgres, and `FakeRecordStore` + `FakeObservationStore` wired to share the
fake's recording clock. The property under test is the coherence design
02-03 rules on: a target's recorded reactions live on TWO append-only
tables — per-actor `message_reaction` deltas and typed
`platform_observation` reaction rows — and every read (the transcript
annotation `reaction_states` folds, the typed `current`/`heads`/`as_of`
reaction head, `record_query`'s hit) must agree on the one
`fold_target_reactions` answer: native clear/clear-emoji semantics,
ordinary add/remove, anonymous-snapshot counts that never invent actors,
honest partial/gap coverage, retained-unresolved targets, and replay as a
no-op.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.models import EditTarget, NormalizedMessage, Touch
from nora.domain.observations import (
    ArtifactPayload,
    Coverage,
    Observation,
    ReactionPayload,
    SourceScope,
    target_key,
)
from nora.domain.record_query import RecordQuery
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.observations import SqlObservationStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.testing.fakes import FakeRecordStore
from nora.testing.observations import FakeObservationStore

NOW = 1_790_000_000_000
SCOPE = SourceScope(platform="discord", account_key="acct",
                    conversation_id="room1")


@pytest.fixture(scope="module")
def _union_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_rx_union_" + uuid4().hex
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
async def stores(request: Any) -> AsyncIterator[Any]:
    """A `(records, observations)` pair reading ONE database: on SQL the
    engine is the link, on the fake the wiring is explicit — both run the
    union `reaction_states` serves."""
    if request.param == "memory":
        records = FakeRecordStore()
        obs = FakeObservationStore(records=records)
        records.observations = obs
        yield records, obs
        return
    engine = create_sql_engine(request.getfixturevalue("_union_db"))
    try:
        yield SqlRecordStore(engine), SqlObservationStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate platform_observation, platform_object_state, "
                "message_reaction, message_edit, message_delete, "
                "messages restart identity cascade"
            ))
        await engine.dispose()


def _touch(*, emoji: str, sender: str, ts: int = NOW,
           removed: bool = False) -> NormalizedMessage:
    return NormalizedMessage(
        platform=SCOPE.platform, conversation_id=SCOPE.conversation_id,
        conversation_type="group", sender_id=sender, sender_name=None,
        timestamp=ts, body=None, raw={},
        touch=Touch(kind="reaction", emoji=emoji, removed=removed,
                    target=EditTarget(message_id="m1")))


def _rx(op: str, *, emoji: str = "👍", covered: tuple = (),
        actor: str | None = None, counts: dict | None = None,
        obj: str = "msg:m1", coverage: Coverage | None = None,
        position: int | None = None, scope_rev: str = "") -> Observation:
    return Observation(
        object_kind="reaction", scope=SCOPE, object_ref=obj, operation=op,
        target=EditTarget(message_id=obj.split(":", 1)[1]),
        payload=ReactionPayload(emoji=emoji, covered_emojis=covered,
                                counts=counts or {}),
        actor_id=actor,
        actor_status="known" if actor else "not_supplied",
        source_position=position, revision_scope=scope_rev,
        coverage=coverage or Coverage(),
        received_ms=NOW)


async def _obs(store: Any, obs: Observation, upstream: str,
               message_id: int | None = None) -> int | None:
    return await store.append(
        observation=obs, event_id=f"ev:{upstream}",
        source_digest=f"d:{upstream}", target_message_id=message_id)


async def _message(records: Any, platform_message_id: str = "m1",
                   sender: str = "author") -> int:
    return await records.append(message=NormalizedMessage(
        platform=SCOPE.platform, conversation_id=SCOPE.conversation_id,
        conversation_type="group", sender_id=sender, sender_name=None,
        timestamp=NOW, body="recorded", raw={}, message_id=platform_message_id))


async def _annotation(records: Any, message_id: int):
    rows = await records.recent(conversation_id=SCOPE.conversation_id,
                                limit=10)
    return next(r.reactions for r in rows if r.id == message_id)


# ---- the union fold itself --------------------------------------------------

async def test_add_clear_add_reads_one_standing_reaction(stores):
    records, obs = stores
    mid = await _message(records)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u1"),
        person_id=None)
    await _obs(obs, _rx("clear"), "c1", mid)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u2"),
        person_id=None)
    ann = await _annotation(records, mid)
    head = await obs.current(scope=SCOPE, kind="reaction",
                             object_ref="msg:m1")
    assert ann is not None and ann.coverage == "recorded"
    assert ann.counts == (("👍", 1),)
    # The pre-clear actor is gone; only the post-clear one stands — and the
    # typed head agrees with the transcript annotation.
    assert head.state["standing"] == {"👍": ["u2"]}
    assert head.state["counts"] == {}


async def test_emoji_scoped_clear_preserves_other_emoji(stores):
    records, obs = stores
    mid = await _message(records)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u1"),
        person_id=None)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="❤️", sender="u1"),
        person_id=None)
    await _obs(obs, _rx("clear", covered=("👍",)), "c2", mid)
    ann = await _annotation(records, mid)
    assert ann is not None and ann.counts == (("❤️", 1),)
    head = await obs.current(scope=SCOPE, kind="reaction",
                             object_ref="msg:m1")
    assert head.state["standing"] == {"❤️": ["u1"]}


async def test_replay_of_a_clear_is_a_noop(stores):
    records, obs = stores
    mid = await _message(records)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u1"),
        person_id=None)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u2"),
        person_id=None)
    await _obs(obs, _rx("clear"), "c1", mid)
    await _obs(obs, _rx("clear"), "c1", mid)  # same source event again
    ann = await _annotation(records, mid)
    # One committed clear row: replay cannot clear twice or inflate the
    # recorded count.
    assert ann is not None and ann.counts == ()
    assert ann.recorded == 3


async def test_anonymous_snapshot_counts_without_invented_actors(stores):
    records, obs = stores
    mid = await _message(records)
    await _obs(obs, _rx("snapshot", counts={"👍": 3}), "s1", mid)
    ann = await _annotation(records, mid)
    head = await obs.current(scope=SCOPE, kind="reaction",
                             object_ref="msg:m1")
    # The count is the recorded aggregate; no reactor identity appears.
    assert ann is not None and ann.counts == (("👍", 3),)
    assert head.state["counts"] == {"👍": 3}
    assert head.state["standing"] == {}
    assert head.state["snapshot"] is True


async def test_incomplete_snapshot_is_partial_not_exact(stores):
    records, obs = stores
    mid = await _message(records)
    await _obs(obs, _rx("snapshot", counts={"👍": 3},
                        coverage=Coverage(complete=False, covered="reactors")),
               "s1", mid)
    ann = await _annotation(records, mid)
    head = await obs.current(scope=SCOPE, kind="reaction",
                             object_ref="msg:m1")
    # An incomplete snapshot never presents as an exact count — the
    # annotation withholds the folded number and says `partial`; the head
    # withholds the same way.
    assert ann is not None and ann.coverage == "partial"
    assert ann.counts == ()
    assert ann.recorded == 1
    assert head.state["counts"] == {}
    assert head.coverage.get("partial") is True


async def test_out_of_order_position_flags_a_gap_not_a_count(stores):
    records, obs = stores
    mid = await _message(records)
    await _obs(obs, _rx("clear", position=3, scope_rev="gw"), "c3", mid)
    # An older-positioned delta arriving after a newer one applied is an
    # ordering gap, retained — never folded into an apparently exact state.
    await _obs(obs, _rx("add", actor="u1", position=2, scope_rev="gw"),
               "a2", mid)
    ann = await _annotation(records, mid)
    head = await obs.current(scope=SCOPE, kind="reaction",
                             object_ref="msg:m1")
    assert ann is not None and ann.coverage == "gap"
    assert ann.counts == ()
    assert head.ordering == "gap"
    assert head.coverage.get("ordering_gap") is True


async def test_unresolved_target_links_into_the_same_projection(stores):
    records, obs = stores
    await _obs(obs, _rx("add", actor="u7", obj="msg:m4"), "a1")
    assert len(await obs.unresolved_targets(scope=SCOPE)) == 1
    # The annotation cannot annotate a message that does not exist; the
    # typed head already carries the retained evidence.
    head = await obs.current(scope=SCOPE, kind="reaction",
                             object_ref="msg:m4")
    assert head.state["standing"] == {"👍": ["u7"]}
    mid = await _message(records, "m4")
    linked = await obs.resolve_targets(
        scope=SCOPE, target_key=target_key(EditTarget(message_id="m4")),
        message_id=mid)
    assert linked == 1
    ann = await _annotation(records, mid)
    head = await obs.current(scope=SCOPE, kind="reaction",
                             object_ref="msg:m4")
    assert ann is not None and ann.counts == (("👍", 1),)
    assert head.state["standing"] == {"👍": ["u7"]}


async def test_as_of_excludes_a_future_clear(stores):
    records, obs = stores
    mid = await _message(records)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u1"),
        person_id=None)
    a1 = await _obs(obs, _rx("add", actor="u5"), "a1", mid)
    await _obs(obs, _rx("clear"), "c2", mid)
    head, more = await obs.as_of(scope=SCOPE, kind="reaction",
                                 object_ref="msg:m1", through_id=a1)
    assert not more
    # The bound predates the clear's commit; the legacy add that committed
    # before the bound is exactly what a replay then shows.
    assert head is not None
    assert sorted(head.state["standing"]["👍"]) == ["u1", "u5"]


async def test_as_of_overflow_reports_more_and_partial(stores):
    records, obs = stores
    mid = await _message(records)
    a1 = await _obs(obs, _rx("add", actor="u1"), "a1", mid)
    await _obs(obs, _rx("add", emoji="❤️", actor="u2"), "a2", mid)
    head, more = await obs.as_of(scope=SCOPE, kind="reaction",
                                 object_ref="msg:m1", through_id=a1 + 1,
                                 limit=1)
    # The bound cut the history: the head folds an earlier point than
    # asked for, and `more`/partial keep it honest.
    assert more is True
    assert head is not None
    assert head.state["standing"] == {}


# ---- the tool surface agrees ------------------------------------------------

async def test_record_query_matches_the_annotation(stores, request):
    """`record_query` reads its hits through the same `reaction_states`
    the transcript annotation does — the union answer, not a second fold."""
    if request.node.callspec.params["stores"] == "memory":
        pytest.skip("no fake record_query — the SQL store is the tool path")
    records, obs = stores
    engine = records._engine
    mid = await _message(records)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u1"),
        person_id=None)
    await _obs(obs, _rx("clear"), "c1", mid)
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u2"),
        person_id=None)
    hits, _more, _near = await SqlRecordQuery(engine).query(
        RecordQuery(
            rooms=((SCOPE.platform, SCOPE.conversation_id),),
            start_ms=0, end_ms=NOW * 2))
    hit = next(h for h in hits if h.row.id == mid)
    ann = await _annotation(records, mid)
    assert hit.reactions == ann.counts == (("👍", 1),)
    assert hit.row.reactions is not None
    assert hit.row.reactions.counts == (("👍", 1),)


async def test_clear_all_removes_unattributed_snapshot_counts(stores):
    records, obs = stores
    mid = await _message(records)
    await _obs(obs, _rx("snapshot", counts={"👍": 3, "❤️": 2}), "snapshot", mid)
    await _obs(obs, _rx("clear"), "clear", mid)
    ann = await _annotation(records, mid)
    head = await obs.current(scope=SCOPE, kind="reaction", object_ref="msg:m1")
    assert ann is not None and ann.coverage == "recorded" and ann.counts == ()
    assert head.state["counts"] == {} and head.state["standing"] == {}
    assert head.state["snapshot"] is False


@pytest.mark.parametrize("typed", [False, True])
async def test_removal_with_unknown_snapshot_actor_overlap_is_partial(stores, typed):
    records, obs = stores
    mid = await _message(records)
    await _obs(obs, _rx("snapshot", counts={"👍": 3}), "snapshot", mid)
    if typed:
        await _obs(obs, _rx("remove", actor="u1"), "remove", mid)
    else:
        await records.append_reaction(
            message_id=mid, message=_touch(emoji="👍", sender="u1", removed=True),
            person_id=None)
    ann = await _annotation(records, mid)
    head = await obs.current(scope=SCOPE, kind="reaction", object_ref="msg:m1")
    assert ann is not None and ann.coverage == "partial" and ann.counts == ()
    assert head.coverage["ambiguous"] is True
    assert head.state["counts"] == {} and head.state["standing"] == {}


async def test_new_legacy_reaction_refreshes_bounded_object_discovery(stores):
    records, obs = stores
    mid = await _message(records)
    await _obs(obs, _rx("clear"), "clear", mid)
    for i in range(18):
        await _obs(obs, Observation(
            object_kind="artifact", scope=SCOPE, object_ref=f"post-{i}",
            operation="create", payload=ArtifactPayload(artifact_kind="post"),
            received_ms=NOW), f"post-{i}")
    assert (await obs.heads(scope=SCOPE, limit=1))[0].kind == "artifact"
    await records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u1"), person_id=None)
    heads = await obs.heads(scope=SCOPE, limit=1)
    assert len(heads) == 1 and heads[0].kind == "reaction"
    assert heads[0].state["standing"] == {"👍": ["u1"]}


async def test_a_waiting_transaction_does_not_backdate_a_later_reaction(stores, request):
    """A transaction starts before the clear, but inserts its add after it.
    PostgreSQL now() would backdate that add; clock_timestamp() must not."""
    import asyncio
    from contextlib import asynccontextmanager

    if request.node.callspec.params["stores"] != "sql":
        pytest.skip("PostgreSQL transaction-start clock property")
    records, obs = stores
    mid = await _message(records)
    real_engine = records._engine
    entered, release = asyncio.Event(), asyncio.Event()

    class WaitingEngine:
        @asynccontextmanager
        async def begin(self):
            async with real_engine.begin() as conn:
                await conn.execute(sa.text("select now()"))
                entered.set()
                await release.wait()
                yield conn

    delayed_records = SqlRecordStore(WaitingEngine())
    pending = asyncio.create_task(delayed_records.append_reaction(
        message_id=mid, message=_touch(emoji="👍", sender="u1"), person_id=None))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await _obs(obs, _rx("clear"), "clear-during-transaction", mid)
    finally:
        release.set()
        await pending
    state = await _annotation(records, mid)
    assert state.coverage == "recorded"
    assert dict(state.counts) == {"👍": 1}
    head = await obs.current(scope=SCOPE, kind="reaction", object_ref="msg:m1")
    assert head.state["standing"] == {"👍": ["u1"]}
