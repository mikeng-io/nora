"""Append-only card writes and migration compatibility."""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from nora.domain.character_card import format_line
from nora.domain.profile import ReviseOp
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import alembic_config, run_migrations
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.schema import PROFILE_LINE
from tests.unit.test_character_card import trait
from tests.unit.test_profile_rules import CITABLE, HUMAN, NOW, _plan


@pytest.fixture
async def env(clean_pg_url):
    run_migrations(clean_pg_url)
    engine = create_sql_engine(clean_pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


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
