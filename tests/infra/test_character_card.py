"""Append-only card writes and the bounded spike seam."""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from nora.app.profile_spike import history_days, recorded_sessions, report, window
from nora.domain.character_card import format_line
from nora.domain.profile import ReviseOp
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import alembic_config, run_migrations
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.schema import MESSAGES, PROFILE_LINE
from tests.unit.test_character_card import trait
from tests.unit.test_profile_rules import CITABLE, HUMAN, NOW, _plan

ROOMS = [('signal', 'room-a')]
PERSON = 'person-a'
DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture
async def env(clean_pg_url):
    run_migrations(clean_pg_url)
    engine = create_sql_engine(clean_pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def message(engine, *, ident, ts, body):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MESSAGES).values(
            id=ident, platform='signal', conversation_id='room-a', conversation_type='group',
            sender_id='synthetic-sender', sender_name='Synthetic', person_id=PERSON, ts=ts,
            body=body, is_nora=0, delivery_status='sent',
        ))


async def test_new_content_roundtrips_and_supersedes_without_rewriting_history(env):
    store = SqlProfileStore(env, screen=lambda _: None)
    result = await store.profile_write(
        account=HUMAN, ops=[trait()], citable=CITABLE, now_ms=NOW,
        written_by_session="synthetic", apply_decay=False)
    assert result.status == "ok"
    [line] = await store.account_heads(key=HUMAN.key)
    assert line.content == trait().content and line.strength["occasions"] == 3
    assert format_line(line) == format_line(_plan([trait()]).inserts[0])
    result = await store.profile_write(account=HUMAN,
        ops=[ReviseOp(line="P1", citations=("101", "102", "103"),
                      content={**line.content, "stance": "compares interpretations"})],
        citable=CITABLE, now_ms=NOW + 1, written_by_session="synthetic-next", apply_decay=False)
    assert result.status == "ok"
    [revised] = await store.account_heads(key=HUMAN.key)
    assert revised.supersedes == line.id
    async with env.connect() as conn:
        original = (await conn.execute(sa.select(PROFILE_LINE).where(
            PROFILE_LINE.c.id == line.id))).one()
    assert original.content == dict(line.content) and original.state == "current"


async def test_window_dry_read_is_oldest_first_day_by_day_and_report_counts_only(env):
    start, end = window("2026-10-01", "2026-10-02")
    for n, ts in enumerate((start - 1, start, start + DAY_MS, end), start=1):
        await message(env, ident=n, ts=ts, body="invented book discussion")
    pages = [page async for page in history_days(env, rooms=ROOMS,
        person=PERSON, since=start, until=end)]
    assert [[m.row.id for m in batch] for _, batch in pages] == [[2], [3]]
    async with env.connect() as conn:
        count = (await conn.execute(sa.select(sa.func.count()).select_from(PROFILE_LINE))).scalar()
        assert count == 0
    store = SqlProfileStore(env, screen=lambda _: None)
    assert (await store.profile_write(account=HUMAN, ops=[trait()], citable=CITABLE,
        now_ms=NOW, written_by_session="synthetic", apply_decay=False)).status == "ok"
    result = await report(env, key=HUMAN.key, since=None, until=None)
    assert result["part_kind"] == {"traits/topic": 1} and "card" not in result
    assert result["citations"] == {"resolved": 0, "unresolved": 3}
    assert result["usage_per_person_day"] is None and result["refused"] is None
    assert (await report(env, key=HUMAN.key, since=None, until=None, card=True))["card"]


def test_recorded_usage_and_refusals_do_not_invent_missing_cost():
    rows = recorded_sessions([
        {"type": "GENERATION", "metadata": {"attributes": {"dreamer.key": "invented"}},
         "startTime": "2026-10-01T00:00:00Z", "usageDetails": {"input": 40, "output": 2},
         "costDetails": {"total": 0.01}},
        {"type": "GENERATION", "metadata": {"attributes": {"dreamer.key": "invented"}},
         "startTime": "2026-10-01T00:00:01Z", "model": "invented-model"},
        {"type": "TOOL", "metadata": {"attributes": {"dreamer.key": "invented"}},
         "startTime": "2026-10-01T00:00:02Z",
         "output": {"refusals": [{"reason": "induced_instances"}]}},
    ])
    assert rows[0]["tokens"] == 42 and rows[0]["cost"] == 0.01
    assert rows[1]["tokens"] is None and rows[1]["cost"] is None
    assert rows[2]["refusals"] == ["induced_instances"]


@pytest.mark.fresh_database
def test_migration_up_down_keeps_one_head_and_legacy_rows(clean_pg_url):
    run_migrations(clean_pg_url)
    from nora.infra.sql.migrations_runner import sync_url_for
    sync = sa.create_engine(sync_url_for(clean_pg_url))
    with sync.begin() as conn:
        conn.execute(sa.insert(PROFILE_LINE).values(id="00000000-0000-0000-0000-000000000001",
            profile_key="invented#unit", part="traits", kind="trait", text="legacy prose",
            mode="induced", citations=["1"], state="current", support_count=1,
            unit="invented", written_at=1, written_by_session="synthetic"))
    cfg = alembic_config(clean_pg_url)
    command.downgrade(cfg, "0126_social_backfill_end")
    command.upgrade(cfg, "head")
    with sync.connect() as conn:
        assert conn.execute(sa.select(PROFILE_LINE.c.text)).scalar_one() == "legacy prose"
    sync.dispose()
    from alembic.script import ScriptDirectory
# One head is the property, independent of the current revision name.
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, heads
