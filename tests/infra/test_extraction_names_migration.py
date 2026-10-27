"""The extraction vocabulary survives reversible migration with its rows intact."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command

from nora.infra.sql.migrations_runner import alembic_config, sync_url_for


def test_extraction_rename_round_trip_preserves_rows_history_and_sql_names(pg_url):
    cfg = alembic_config(pg_url)
    engine = sa.create_engine(sync_url_for(pg_url))
    command.downgrade(cfg, "0128_event_platform")
    command.upgrade(cfg, "0130_profile_names")
    constraints_sql = sa.text(
        "SELECT conrelid::regclass::text, conname FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace "
        "AND conrelid::regclass::text LIKE 'message_reading%' "
        "ORDER BY 1, 2"
    )
    try:
        with engine.begin() as conn:
            original_constraints = conn.execute(constraints_sql).all()
            assert original_constraints
            conn.execute(
                sa.text("""
                INSERT INTO message_reading
                  (id, unit, platform, origin_room, source_ids, context_ids,
                   snapshot, version, model)
                VALUES (70001, 'synthetic-unit', 'signal', 'synthetic-room',
                        '{}', '{}', '{}', 1, 'synthetic');
                INSERT INTO message_reading_attempt(id, reading_id, attempt, model)
                VALUES (70002, 70001, 1, 'synthetic');
                INSERT INTO message_reading_claim
                  (id, reading_id, ordinal, unit, platform, origin_room, source_ids, content)
                VALUES (70003, 70001, 0, 'synthetic-unit', 'signal', 'synthetic-room', '{}', '{}');
                INSERT INTO message_reading_queue
                  (id, reading_id, unit, platform, origin_room, source_key)
                VALUES (70004, 70001, 'synthetic-unit', 'signal', 'synthetic-room', 'synthetic');
            """)
            )
            before = {
                suffix: conn.execute(sa.text(f"SELECT to_jsonb(t) FROM message_reading{suffix} t"))  # noqa: S608 - fixed test identifiers
                .scalars()
                .all()
                for suffix in ("", "_attempt", "_claim", "_queue")
            }
        for _ in range(2):
            command.upgrade(cfg, "head")
            with engine.connect() as conn:
                for suffix, rows in before.items():
                    actual = (
                        conn.execute(sa.text(f"SELECT to_jsonb(t) FROM extraction{suffix} t"))  # noqa: S608 - fixed test identifiers
                        .scalars()
                        .all()
                    )
                    assert actual == [
                        {**{("extraction_id" if k == "reading_id" else k): v
                            for k, v in row.items()},
                         **({"historical": False} if suffix == "_queue" else {}),
                         **({"failure_stage": None} if suffix == "_attempt" else {})}
                        for row in rows
                    ]
                names = (
                    conn.execute(
                        sa.text(
                            "SELECT relname FROM pg_class "
                            "WHERE relnamespace = 'public'::regnamespace "
                            "AND relname LIKE '%message_reading%'"
                        )
                    )
                    .scalars()
                    .all()
                )
                assert names == []
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT count(*) FROM row_history "
                            "WHERE table_name = 'extraction_attempt' "
                            "AND image->>'extraction_id' = '70001'"
                        )
                    ).scalar_one()
                    >= 1
                )
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT count(*) FROM row_history "
                            "WHERE table_name LIKE 'message_reading%'"
                        )
                    ).scalar_one()
                    == 0
                )
                assert (
                    conn.execute(
                        sa.text("SELECT to_regprocedure('extraction_retire(integer)') IS NOT NULL")
                    ).scalar_one()
                    is True
                )
            command.downgrade(cfg, "0130_profile_names")
            with engine.connect() as conn:
                assert conn.execute(constraints_sql).all() == original_constraints
                for suffix, rows in before.items():
                    assert (
                        conn.execute(sa.text(f"SELECT to_jsonb(t) FROM message_reading{suffix} t"))  # noqa: S608 - fixed test identifiers
                        .scalars()
                        .all()
                        == rows
                    )
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def test_terminal_extraction_state_round_trip_preserves_pending_work(pg_url):
    cfg = alembic_config(pg_url)
    engine = sa.create_engine(sync_url_for(pg_url))
    try:
        with engine.begin() as conn:
            batch_id = conn.execute(sa.text("""
                INSERT INTO extraction
                  (unit, platform, origin_room, source_ids, context_ids, snapshot, version, model,
                   state, attempts)
                VALUES ('synthetic-unit', 'signal', 'synthetic-room', '{}', '{}', '{}',
                        1, 'synthetic', 'failed', 5) RETURNING id
            """)).scalar_one()
        command.downgrade(cfg, "0133_standing_ledger")
        with engine.connect() as conn:
            held = conn.execute(sa.text(
                "SELECT state, attempts, snapshot FROM extraction WHERE id = :id"
            ), {"id": batch_id}).one()
            assert held == ("pending", 5, {})
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE extraction SET state = 'failed' WHERE id = :id"),
                         {"id": batch_id})
            assert conn.execute(sa.text(
                "SELECT state FROM extraction WHERE id = :id"
            ), {"id": batch_id}).scalar_one() == "failed"
    finally:
        engine.dispose()
