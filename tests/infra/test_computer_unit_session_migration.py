"""Host session migration against real Postgres, dry-run first and repeatable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.domain.computer import computer_scope, computer_unit_scope
from nora.infra.sql.computer_events import SqlComputerEventStore
from nora.infra.sql.computer_sessions import SqlComputerSessionStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import COMPUTER_SESSIONS as T
from nora.policy import Conversation, Policy
from scripts.migrate_computer_unit_scope import bind_sessions, mapping_for, session_plan
from tests.contract.test_computer_event_store_contract import _event


@pytest.fixture
def policy():
    return Policy(
        conversations=(
            Conversation(
                platform="signal",
                ids=frozenset({"s", "alias"}),
                persons=frozenset({"miles"}),
                unit="team",
            ),
            Conversation(
                platform="discord",
                ids=frozenset({"d"}),
                persons=frozenset({"another"}),
                unit="team",
            ),
            Conversation(
                platform="whatsapp",
                ids=frozenset({"private"}),
                persons=frozenset({"miles"}),
                unit="private",
            ),
        )
    )


def test_mapping_uses_all_policy_ids_and_unit_names_never_room_membership(policy):
    scopes = [
        computer_scope("signal", "s"),
        computer_scope("signal", "alias"),
        computer_scope("discord", "d"),
        computer_scope("whatsapp", "private"),
    ]
    mapping = mapping_for(policy, scopes)
    assert {mapping[s] for s in scopes[:3]} == {computer_unit_scope("team")}
    assert mapping[scopes[3]] == computer_unit_scope("private")
    with pytest.raises(ValueError, match="unmapped"):
        mapping_for(policy, ["f" * 32])


async def test_multiple_open_legacy_sessions_keep_state_and_audit_then_reuse_one_unit(
    pg_url, policy
):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    store, events = SqlComputerSessionStore(engine), SqlComputerEventStore(engine)
    now = datetime(2026, 10, 21, tzinfo=UTC)
    originals = []
    try:
        for i, (platform, cid) in enumerate(
            [("signal", "s"), ("discord", "d"), ("whatsapp", "private")]
        ):
            session, _ = await store.continue_or_open(
                platform=platform,
                conversation_id=cid,
                scope=computer_scope(platform, cid),
                objective=f"legacy task {i}",
                actor="tag",
                tag_id=f"tag-{i}",
                now=now + timedelta(minutes=i),
            )
            originals.append(session)
        event = _event(session_id=originals[0].id, scope=originals[0].scope, conversation_id="s")
        await events.append(event)
        mapping = mapping_for(policy, [s.scope for s in originals])
        receipt = {"verified": True, "mapping": mapping}
        async with engine.begin() as conn:
            before = (await conn.execute(sa.select(T).order_by(T.c.id))).mappings().all()
            document = {
                "mapping": mapping,
                "moves": mapping,
                "sessions": await session_plan(conn, mapping),
            }
            assert await bind_sessions(conn, document, receipt, apply=False) == 3
            assert (await conn.execute(sa.select(T).order_by(T.c.id))).mappings().all() == before
            with pytest.raises(ValueError, match="verified"):
                await bind_sessions(conn, document, {"verified": False}, apply=True)
            assert await bind_sessions(conn, document, receipt, apply=True) == 3
            assert await bind_sessions(conn, document, receipt, apply=True) == 0
            after = (await conn.execute(sa.select(T).order_by(T.c.id))).mappings().all()
            assert [{k: v for k, v in r.items() if k != "stopped_at_detail"} for r in after] == [
                {k: v for k, v in r.items() if k != "stopped_at_detail"} for r in before
            ]
        unit = await store.for_scope(scope=computer_unit_scope("team"), limit=10)
        assert {s.id for s in unit} == {s.id for s in originals[:2]}
        assert {s.status for s in unit} == {"open"}
        assert {s.resume_prefix for s in unit} == {f"rooms/{s.scope}/" for s in originals[:2]}
        assert await events.for_session(session_id=originals[0].id, limit=10) == [event]
        assert await events.for_scope(
            scope=computer_unit_scope("team"), since=datetime(2020, 1, 1, tzinfo=UTC), limit=10
        ) == [event]
        assert (
            await events.for_scope(
                scope=computer_unit_scope("private"),
                since=datetime(2020, 1, 1, tzinfo=UTC),
                limit=10,
            )
            == []
        )
        continued, opened = await store.continue_or_open(
            platform="whatsapp",
            conversation_id="new-room",
            scope=computer_unit_scope("team"),
            objective="resume",
            actor="fire",
            tag_id="new",
            now=now + timedelta(hours=1),
        )
        assert not opened and continued.id == originals[1].id
        assert continued.conversation_id == "d" and continued.created_by_tag == "tag-1"
        assert len(await store.open_sessions(limit=10)) == 3
    finally:
        await engine.dispose()
