"""The 0155 downgrade's data guard, against a real migrated database."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command

from nora.infra.sql.migrations_runner import (
    alembic_config,
    current_revision,
    head_revision,
    run_migrations,
    sync_url_for,
)


@pytest.mark.fresh_database
def test_0155_downgrade_refuses_while_the_graph_holds_data(clean_pg_url):
    """A populated graph cannot downgrade: dropping the five tables would
    lose their rows, and the restored `entity` name would collide with
    new-graph row_history. The refusal covers rows still present and rows
    already deleted — their history remains either way."""
    run_migrations(clean_pg_url)
    engine = sa.create_engine(sync_url_for(clean_pg_url))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO registry (id, unit, kind) "
                "VALUES ('r-1', 'u', 'claim')"
            ))
        with pytest.raises(RuntimeError, match="still holds data"):
            command.downgrade(
                alembic_config(clean_pg_url), "0154_event_revision_citations"
            )
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM registry"))
        with pytest.raises(RuntimeError, match="still holds data"):
            command.downgrade(
                alembic_config(clean_pg_url), "0154_event_revision_citations"
            )
    finally:
        engine.dispose()
    assert current_revision(clean_pg_url) == head_revision()


@pytest.mark.fresh_database
def test_0155_downgrade_round_trips_when_the_graph_is_empty(clean_pg_url):
    """An empty graph downgrades and upgrades cleanly: the five tables go,
    the legacy names come back, and the chain reaches head again."""
    run_migrations(clean_pg_url)
    command.downgrade(
        alembic_config(clean_pg_url), "0154_event_revision_citations"
    )
    engine = sa.create_engine(sync_url_for(clean_pg_url))
    try:
        with engine.connect() as conn:
            names = sa.inspect(conn).get_table_names()
        assert "registry" not in names and "claim" not in names
        assert "legacy_entity" not in names and "entity" in names
    finally:
        engine.dispose()
    run_migrations(clean_pg_url)
    assert current_revision(clean_pg_url) == head_revision()
