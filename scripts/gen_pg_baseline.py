"""Regenerate tests/infra/pg_schema_baseline.sql — the schema-parity oracle.

Run deliberately, only when the schema legitimately changes:

    uv run python scripts/gen_pg_baseline.py

It spins a throwaway `postgres:16-alpine`, runs `alembic upgrade head`, takes a
`pg_dump --schema-only --no-owner --no-privileges` via `docker exec`, normalises
it with the SAME function `tests/infra/test_schema_parity.py` uses, and writes
the result. Using the test's own normaliser is what keeps the baseline and the
gate from silently disagreeing.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from testcontainers.postgres import PostgresContainer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nora.infra.sql.migrations_runner import run_migrations  # noqa: E402
from tests.infra.test_schema_parity import (  # noqa: E402
    BASELINE,
    _dump_schema,
    _normalise,
)


@dataclass(frozen=True)
class _Admin:
    host: str
    port: int
    user: str
    password: str
    container_id: str


def main() -> int:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        admin = _Admin(
            host=pg.get_container_host_ip(),
            port=int(pg.get_exposed_port(5432)),
            user=pg.username,
            password=pg.password,
            container_id=pg.get_wrapped_container().id,
        )
        url = (
            f"postgresql+asyncpg://{admin.user}:{admin.password}"
            f"@{admin.host}:{admin.port}/{pg.dbname}"
        )
        run_migrations(url)
        normalised = _normalise(_dump_schema(admin, pg.dbname))
        BASELINE.write_text(normalised + "\n")
        print(f"wrote {BASELINE} ({len(normalised.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
