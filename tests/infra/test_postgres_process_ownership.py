"""Generated orphan-exit control: recovery must not kill an existing backend."""
import time

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

        def wait_for(predicate):
            deadline = time.monotonic() + 10
            while not predicate():
                assert time.monotonic() < deadline, container.logs().decode()
                time.sleep(0.02)

        result = container.exec_run(['sh', '-c',
            'mkfifo /run/orphan-exit; '
            "sh -c 'read value < /run/orphan-exit; exit 2' >/dev/null 2>&1 & "
            'echo $!'])
        assert result.exit_code == 0
        orphan = int(result.output)
        wait_for(lambda: container.exec_run(['sh', '-c',
            f"awk '/^PPid:/ {{print $2}}' /proc/{orphan}/status"]).output.strip() == b'1')
        assert container.exec_run(['sh', '-c', 'echo exit > /run/orphan-exit']).exit_code == 0
        if init:
            wait_for(lambda: container.exec_run(
                ['test', '!', '-e', f'/proc/{orphan}']).exit_code == 0)
            # The same live backend spans adoption, exit and reaping.
            assert conn.execute('SELECT pg_backend_pid()').fetchone()[0] == backend
            assert b'terminating any other active server processes' not in container.logs()
        else:
            # A socket close can precede Docker's delivery of the recovery log.
            marker = f'untracked child process (PID {orphan}) exited with exit code 2'.encode()
            wait_for(lambda: marker in container.logs()
                     and b'terminating any other active server processes' in container.logs())
            with pytest.raises(psycopg.OperationalError):
                conn.execute('SELECT pg_backend_pid()')
