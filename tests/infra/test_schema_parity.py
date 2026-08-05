"""The schema gate (DEC-11).

`alembic check` guards column presence and type, and — contrary to the spec's
original claim, corrected 2026-08-05 — server defaults. It is provably blind to
the CHECK constraint and to the `ts DESC` index ordering, and it reports
permanent phantom drift on the expression index. This file is what actually
guarantees the schema, so it asserts against SQLite's own catalogue rather than
against SQLAlchemy's opinion of it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import METADATA

PROD_DB = Path(__file__).resolve().parents[2] / "data" / "messages.db"


@pytest.fixture
def fresh_db(tmp_path) -> Path:
    """A database built from the metadata alone — no Alembic involved."""
    path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{path}")
    METADATA.create_all(engine)
    engine.dispose()
    return path


def _table_sql(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
    assert row is not None, "no messages table"
    return row[0]


def _index_xinfo(path: Path, index: str) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute(f"PRAGMA index_xinfo({index})").fetchall()


def _table_info(path: Path) -> dict[str, tuple]:
    with sqlite3.connect(path) as conn:
        return {r[1]: r for r in conn.execute("PRAGMA table_info(messages)")}


def test_check_constraint_is_present(fresh_db):
    """alembic check is blind to this — proven 2026-08-05."""
    assert "CHECK (conversation_type IN ('group','dm'))" in _table_sql(fresh_db)


def test_autoincrement_is_present(fresh_db):
    """Without sqlite_autoincrement=True, rowids are reused and every fresh test
    database silently stops matching stamped production."""
    assert "AUTOINCREMENT" in _table_sql(fresh_db)


def test_id_is_nullable_exactly_as_production_is(fresh_db):
    """SQLite's `INTEGER PRIMARY KEY AUTOINCREMENT` has notnull=0. Declaring
    primary_key=True without nullable=True makes `alembic check` fail on the
    production database while passing in CI."""
    assert _table_info(fresh_db)["id"][3] == 0


def test_conv_ts_index_orders_ts_descending(fresh_db):
    """alembic check is blind to this — dropping DESC produced an EMPTY diff."""
    xinfo = _index_xinfo(fresh_db, "idx_messages_conv_ts")
    named = {r[2]: r for r in xinfo if r[2] is not None}
    assert named["conversation_id"][3] == 0, "conversation_id must be ASC"
    assert named["ts"][3] == 1, "ts must be DESC"


def test_created_at_server_default_is_datetime_now(fresh_db):
    assert "datetime('now')" in _table_sql(fresh_db)


def test_is_nora_and_delivery_status_defaults(fresh_db):
    sql = _table_sql(fresh_db)
    assert "DEFAULT 0" in sql or "DEFAULT (0)" in sql
    assert "'pending'" in sql


def test_columns_match_production_exactly(fresh_db):
    """The live database is the oracle: it has 0 rows but the real shape."""
    if not PROD_DB.exists():
        pytest.skip("no production database on this host")
    assert set(_table_info(fresh_db)) == set(_table_info(PROD_DB))
    for name, prod_row in _table_info(PROD_DB).items():
        fresh_row = _table_info(fresh_db)[name]
        assert fresh_row[2] == prod_row[2], f"{name}: type differs"
        assert fresh_row[3] == prod_row[3], f"{name}: nullability differs"


def test_indexes_match_production_exactly(fresh_db):
    if not PROD_DB.exists():
        pytest.skip("no production database on this host")
    for index in ("idx_messages_conv_ts", "idx_messages_is_nora"):
        assert _index_xinfo(fresh_db, index) == _index_xinfo(PROD_DB, index), index


def test_the_column_order_of_a_fresh_database_matches_a_migrated_one(fresh_db, tmp_path):
    """The append-position rule, enforced instead of merely commented.

    `ALTER TABLE ADD COLUMN` appends, so a migrated database has every added
    column at the end. Declaring one mid-table in `schema.py` makes a fresh
    database order its columns differently, which shifts `ts`'s column index —
    the failure `test_indexes_match_production_exactly` caught for `person_id`,
    and which is invisible on a host with no production database at all. This
    catches it from the metadata alone.
    """
    migrated = tmp_path / "migrated.db"
    run_migrations(migrated)
    assert list(_table_info(fresh_db)) == list(_table_info(migrated))


def test_quoted_ts_is_the_last_declared_column(fresh_db):
    """`person_id` was last; `quoted_ts` is now, and anything after it must be
    appended in turn. Spelled out so the next column's author sees it fail."""
    assert list(_table_info(fresh_db))[-1] == "quoted_ts"
