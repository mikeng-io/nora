"""The Cognition loop eval pushes Record sequences past the source's ids."""

import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import METADATA, ROW_HISTORY
from nora.subject import sandbox_as_of
from scripts.cognition_loop.advance import advance_record, bump_sequences
from tests.integration.test_snapshot_holds_what_was_held import APPEND, ROWS


async def test_bump_sequences_skips_uuid_ids_and_moves_serial_ones(pg_module_url):
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "SELECT setval(pg_get_serial_sequence('messages', 'id'), 1, false)"))
        source_max = (await conn.execute(sa.text(
            "SELECT COALESCE(MAX(id), 0) FROM messages"))).scalar_one()
        assert (await conn.execute(sa.text(
            "SELECT pg_get_serial_sequence('social_item', 'id')"))).scalar_one() is None

    await bump_sequences(engine, engine)

    async with engine.connect() as conn:
        nxt = (await conn.execute(sa.text(
            "SELECT nextval(pg_get_serial_sequence('messages', 'id'))"))).scalar_one()
    assert nxt > source_max
    await engine.dispose()


async def test_advance_supersedes_the_restored_open_version(clean_pg_url, pg_template):
    """A row's open history version, restored at the first wake, is closed by
    the source's later version — never a second open version beside it
    (`uq_row_history_open`) and never skipped by an id conflict."""
    source = create_sql_engine(clean_pg_url)
    messages = METADATA.tables["messages"]
    standing = METADATA.tables["standing"]
    message = ROWS["messages"](0)
    try:
        async with source.begin() as conn:
            await conn.execute(sa.insert(messages).values(message))
            await conn.execute(sa.insert(standing).values(ROWS["standing"](0)))
            t1 = (await conn.execute(
                sa.text("SELECT clock_timestamp()"))).scalar_one()
        async with sandbox_as_of(
            clean_pg_url, t1, template=pg_template,
        ) as (sandbox_url, _seed):
            sandbox = create_sql_engine(sandbox_url)
            try:
                # A non-Record history row in the window spends the source's
                # next row_history id — the collision the verbatim
                # `ON CONFLICT (id)` copy hit.
                async with source.begin() as conn:
                    await conn.execute(
                        sa.update(standing)
                        .where(standing.c.platform == "signal",
                               standing.c.conversation_id == "g0")
                        .values(body="revised position"))
                    await conn.execute(
                        sa.update(messages)
                        .where(messages.c.id == message["id"])
                        .values(body="edited body"))
                    t2 = (await conn.execute(
                        sa.text("SELECT clock_timestamp()"))).scalar_one()
                await bump_sequences(source, sandbox)
                report = await advance_record(
                    source, sandbox,
                    after_ms=int(t1.timestamp() * 1000),
                    at_ms=int(t2.timestamp() * 1000) + 5)
            finally:
                await sandbox.dispose()
            assert report.wrote_only_record()
            async with sandbox.connect() as conn:
                versions = (await conn.execute(
                    sa.select(ROW_HISTORY.c.id, ROW_HISTORY.c.superseded_by)
                    .where(ROW_HISTORY.c.table_name == "messages")
                    .order_by(ROW_HISTORY.c.id))).all()
                body = (await conn.execute(
                    sa.select(messages.c.body)
                    .where(messages.c.id == message["id"]))).scalar_one()
            # v1 closed by v2, v2 open — and no replay-dated third version.
            assert len(versions) == 2
            assert versions[0].superseded_by == versions[1].id
            assert versions[1].superseded_by is None
            assert body == "edited body"
    finally:
        await source.dispose()


async def test_advance_supersedes_a_version_the_session_wrote(clean_pg_url, pg_template):
    """The session between wakes writes its own open version for a row; the
    world's later version still replaces it — the trigger's own close,
    applied to the sandbox's open head."""
    source = create_sql_engine(clean_pg_url)
    messages = METADATA.tables["messages"]
    standing = METADATA.tables["standing"]
    message = ROWS["messages"](0)
    try:
        async with source.begin() as conn:
            await conn.execute(sa.insert(messages).values(message))
            await conn.execute(sa.insert(standing).values(ROWS["standing"](0)))
            t1 = (await conn.execute(
                sa.text("SELECT clock_timestamp()"))).scalar_one()
        async with sandbox_as_of(
            clean_pg_url, t1, template=pg_template,
        ) as (sandbox_url, _seed):
            sandbox = create_sql_engine(sandbox_url)
            try:
                await bump_sequences(source, sandbox)
                async with sandbox.begin() as conn:
                    await conn.execute(
                        sa.update(messages)
                        .where(messages.c.id == message["id"])
                        .values(body="her own note on it"))
                async with source.begin() as conn:
                    await conn.execute(
                        sa.update(standing)
                        .where(standing.c.platform == "signal",
                               standing.c.conversation_id == "g0")
                        .values(body="revised position"))
                    await conn.execute(
                        sa.update(messages)
                        .where(messages.c.id == message["id"])
                        .values(body="the world's edit"))
                    t2 = (await conn.execute(
                        sa.text("SELECT clock_timestamp()"))).scalar_one()
                await advance_record(
                    source, sandbox,
                    after_ms=int(t1.timestamp() * 1000),
                    at_ms=int(t2.timestamp() * 1000) + 5)
            finally:
                await sandbox.dispose()
            async with sandbox.connect() as conn:
                versions = (await conn.execute(
                    sa.select(ROW_HISTORY.c.id, ROW_HISTORY.c.superseded_by)
                    .where(ROW_HISTORY.c.table_name == "messages")
                    .order_by(ROW_HISTORY.c.id))).all()
                body = (await conn.execute(
                    sa.select(messages.c.body)
                    .where(messages.c.id == message["id"]))).scalar_one()
            # v1 -> her session version (moved aside, still recorded) ->
            # the source's v2 — the only open head. Never duplicated.
            assert len(versions) == 3
            open_versions = [v for v in versions if v.superseded_by is None]
            assert len(open_versions) == 1
            assert versions[0].superseded_by == versions[2].id
            assert versions[2].superseded_by == open_versions[0].id
            assert body == "the world's edit"
    finally:
        await source.dispose()


async def test_advance_counts_exactly_the_rows_it_landed(
    clean_pg_url, pg_template,
):
    """`What arrived` counts are the rows the insert landed, never
    asyncpg's `-1` batch rowcount: three stimuli and two social items
    count exactly, and a replayed window lands nothing new — never a
    negative."""
    source = create_sql_engine(clean_pg_url)
    stimulus = METADATA.tables["stimulus"]
    social_item = METADATA.tables["social_item"]
    try:
        async with source.begin() as conn:
            t1 = (await conn.execute(
                sa.text("SELECT clock_timestamp()"))).scalar_one()
        async with sandbox_as_of(
            clean_pg_url, t1, template=pg_template,
        ) as (sandbox_url, _seed):
            sandbox = create_sql_engine(sandbox_url)
            try:
                async with source.begin() as conn:
                    for i in range(3):
                        await conn.execute(
                            sa.insert(stimulus).values(APPEND["stimulus"](i)))
                    for i in range(2):
                        await conn.execute(
                            sa.insert(social_item).values(
                                APPEND["social_item"](i)))
                    t2 = (await conn.execute(
                        sa.text("SELECT clock_timestamp()"))).scalar_one()
                await bump_sequences(source, sandbox)
                report = await advance_record(
                    source, sandbox,
                    after_ms=int(t1.timestamp() * 1000),
                    at_ms=int(t2.timestamp() * 1000) + 5)
                assert report.written["stimulus"] == 3
                assert report.written["social_item"] == 2
                assert all(n >= 0 for n in report.written.values())
                # The same window again lands nothing: the conflicted
                # rows count 0 and drop out of the receipt entirely.
                again = await advance_record(
                    source, sandbox,
                    after_ms=int(t1.timestamp() * 1000),
                    at_ms=int(t2.timestamp() * 1000) + 5)
                assert "stimulus" not in again.written
                assert "social_item" not in again.written
            finally:
                await sandbox.dispose()
    finally:
        await source.dispose()
