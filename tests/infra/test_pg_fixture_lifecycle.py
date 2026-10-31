"""Real PostgreSQL clone ownership and isolation, including committed state."""

import psycopg
import pytest

from tests.conftest import _pg_database


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
