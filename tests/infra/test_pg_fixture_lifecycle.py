"""Real PostgreSQL clone ownership and isolation, including committed state."""

import psycopg
import pytest
from psycopg import sql

from nora.domain.media import MediaBlob
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from tests.conftest import _pg_database


def _row_and_sequence_state(conn):
    tables = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    ).fetchall()
    rows = {
        table: conn.execute(sql.SQL(
            "SELECT to_jsonb(t)::text FROM {} t ORDER BY 1"
        ).format(sql.Identifier(table))).fetchall()
        for (table,) in tables
    }
    sequences = conn.execute(
        "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
        "AND relkind = 'S' ORDER BY relname"
    ).fetchall()
    states = {
        name: conn.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(
            sql.Identifier(name)
        )).fetchone()
        for (name,) in sequences
    }
    return rows, states


@pytest.fixture(scope="module")
def record_template_state(pg_admin, pg_template):
    with psycopg.connect(pg_admin.dsn(pg_template)) as conn:
        return _row_and_sequence_state(conn)


@pytest.mark.parametrize("attempt", [0, 1])
async def test_record_reuse_resets_committed_state(
    record_pg_url, pg_module_url, pg_admin, record_template_state, attempt,
):
    assert record_pg_url == pg_module_url
    dsn = pg_admin.dsn(record_pg_url.rsplit('/', 1)[1])
    with psycopg.connect(dsn) as conn:
        # Catalogue-wide observation, independent of the reset's table list.
        assert _row_and_sequence_state(conn) == record_template_state
        assert conn.execute("SELECT current_setting('nora.row_history', true)").fetchone() != (
            'off',
        )
    message = NormalizedMessage(
        "signal", "generated-room", "group", "generated-sender", "Generated", attempt,
        "generated record", {},
        media_blobs=(MediaBlob(sha256='a' * 64, media_type='image/png', size=1, filename=None),),
    )
    store = SqlRecordStore(create_sql_engine(record_pg_url))
    try:
        assert await store.append(message=message, person_id="generated-person") == 1
        await store.append_edit(message_id=1, message=message)
    finally:
        await store.aclose()
    # Separate connection sees committed rows, projection and history: no outer rollback.
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM messages").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM message_edit").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM memory_node").fetchone()[0] > 1
        assert conn.execute("SELECT count(*) FROM memory_edge").fetchone()[0] > 0
        assert conn.execute("SELECT count(*) FROM row_history").fetchone()[0] > 0
        conn.execute(
            "INSERT INTO memory_projection_queue (kind, source_id, last_error) "
            "VALUES ('message', 'generated-retry', 'generated failure')"
        )


def test_module_clone_isolation_and_exception_cleanup(pg_admin, pg_template):
    def connect(url):
        return psycopg.connect(pg_admin.dsn(url.rsplit('/', 1)[1]), autocommit=True)

    with psycopg.connect(pg_admin.dsn(pg_template)) as template:
        seeds = template.execute("SELECT * FROM memory_index_state").fetchall()
        version = template.execute("SELECT version_num FROM alembic_version").fetchone()
    assert seeds and version
    urls = []
    with (
        pytest.raises(RuntimeError, match="generated failure"),
        _pg_database(pg_admin, pg_template) as first,
    ):
        urls.append(first)
        with connect(first) as conn:
            assert conn.execute("SELECT * FROM memory_index_state").fetchall() == seeds
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == version
            conn.execute("CREATE TABLE generated_isolation (id bigserial PRIMARY KEY)")
            assert conn.execute(
                "INSERT INTO generated_isolation DEFAULT VALUES RETURNING id"
            ).fetchone() == (1,)
            conn.execute("SELECT pg_advisory_lock(472)")
        # Separate connection sees committed state; no outer rollback hides commits.
        with connect(first) as conn:
            assert conn.execute("SELECT count(*) FROM generated_isolation").fetchone() == (1,)
        with _pg_database(pg_admin, pg_template) as second:
            urls.append(second)
            assert first != second
            with connect(second) as conn:
                assert conn.execute(
                    "SELECT to_regclass('generated_isolation')"
                ).fetchone() == (None,)
                assert conn.execute("SELECT * FROM memory_index_state").fetchall() == seeds
        straggler = connect(first)
        assert straggler.execute("SELECT 1").fetchone() == (1,)
        raise RuntimeError("generated failure")
    try:
        with pytest.raises(psycopg.OperationalError):
            straggler.execute("SELECT 1")
    finally:
        straggler.close()
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db)) as admin:
        names = [url.rsplit('/', 1)[1] for url in urls]
        assert admin.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)", (names,)
        ).fetchall() == []
        assert admin.execute(
            "SELECT datname FROM pg_database WHERE datname = %s", (pg_template,)
        ).fetchone() == (pg_template,)
