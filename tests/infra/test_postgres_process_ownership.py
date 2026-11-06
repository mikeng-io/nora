"""Generated orphan-exit control: recovery must not kill an existing backend."""
import psycopg
import pytest
from testcontainers.postgres import PostgresContainer


@pytest.mark.parametrize('init', [False, True])
def test_external_orphan_exit_does_not_reach_postmaster(init):
    with PostgresContainer('pgvector/pgvector:pg18', driver='psycopg').with_kwargs(
        init=init, mem_limit='256m',
    ) as pg, psycopg.connect(pg.get_connection_url().replace('+psycopg', '')) as conn:
        backend = conn.execute('SELECT pg_backend_pid()').fetchone()[0]
        container = pg.get_wrapped_container()
        result = container.exec_run(['sh', '-c',
            '(sleep 0.2; exit 2) >/dev/null 2>&1 & exit 0'])
        assert result.exit_code == 0
        if init:
            # This live backend spans the orphan's exit; a recovery would
            # terminate it. The bare control proves the probe sees that.
            assert conn.execute('SELECT pg_sleep(1), pg_backend_pid()').fetchone()[1] == backend
            assert b'terminating any other active server processes' not in container.logs()
        else:
            with pytest.raises(psycopg.OperationalError):
                conn.execute('SELECT pg_sleep(10)')
            assert b'untracked child process' in container.logs()
            assert b'terminating any other active server processes' in container.logs()
