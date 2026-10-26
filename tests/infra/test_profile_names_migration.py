"""Synthetic, reversible ADR 0029 migration, including overlapping cursor keys."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import command

from nora.infra.sql.migrations_runner import alembic_config, sync_url_for

OLD_TABLES = ("culture", "culture_revision", "persona", "persona_line", "persona_dispute")


def _rows(conn, tables):
    return {table: conn.execute(sa.text(
        f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY to_jsonb(t)::text'  # noqa: S608 - frozen migration/test identifiers
    )).scalars().all() for table in tables}


def test_upgrade_downgrade_upgrade_preserves_every_row_and_cursor(pg_url):
    cfg = alembic_config(pg_url)
    command.downgrade(cfg, "0129_message_readings")
    engine = sa.create_engine(sync_url_for(pg_url))
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("""
                INSERT INTO culture_revision(id, culture_key, body, written_at, established_by)
                VALUES ('00000000-0000-0000-0000-000000000001', 'unit-a#character:ada',
                        'Synthetic prose.', 10, 'dream:character-person:synthetic');
                INSERT INTO culture(key, head_revision_id, updated_at)
                VALUES ('unit-a#character:ada', '00000000-0000-0000-0000-000000000001', 10);
                INSERT INTO persona(key, entity_kind, unit, updated_at)
                VALUES ('self', 'nora', 'unit-a', 10);
                INSERT INTO persona_line
                  (id, persona_key, part, kind, text, mode, citations, state, support_count,
                   unit, written_at, written_by_session, told_by, draws_on,
                   prediction, prediction_observable, prediction_window_end, verdict)
                VALUES
                  ('00000000-0000-0000-0000-000000000002', 'self',
                   'identity', 'role', 'Synthetic profile.', 'told', ARRAY['1'], 'current', 1,
                   'unit-a', 10, 'dream:persona-person:synthetic', 'ada', '{}',
                   NULL, NULL, NULL, NULL),
                  ('00000000-0000-0000-0000-000000000003', 'self',
                   'character', 'trait', 'Synthetic trait.', 'induced', ARRAY['1'], 'current', 1,
                   'unit-a', 10, 'dream:persona-person:synthetic', NULL, '{}',
                   NULL, NULL, NULL, NULL),
                  ('00000000-0000-0000-0000-000000000004', 'self',
                   'hypothesis', 'explanation', 'Synthetic explanation.', 'abduced',
                   ARRAY['1'], 'current', 1, 'unit-a', 10, 'dream:persona-person:synthetic',
                   NULL, ARRAY['00000000-0000-0000-0000-000000000003'::uuid],
                   'Synthetic prediction.', 'synthetic', 100, 'open');
                INSERT INTO persona_dispute
                  (id, persona_key, line_id, disputed_by, kind, claim, message_id, unit, written_at)
                VALUES ('00000000-0000-0000-0000-000000000005', 'self',
                        '00000000-0000-0000-0000-000000000002', 'ada', 'dispute',
                        'Synthetic dispute.', 1, 'unit-a', 10);
                INSERT INTO memory_node(kind, source_id, text, state, projected_at, unit)
                VALUES ('character', 'unit-a#character:ada',
                        'Synthetic prose.', 'live', now(), 'unit-a'),
                       ('persona_line', '00000000-0000-0000-0000-000000000002',
                        'Synthetic profile.', 'live', now(), 'unit-a');
                INSERT INTO memory_edge(src_kind, src_id, rel, dst_kind, dst_id, provenance)
                VALUES ('persona_line', '00000000-0000-0000-0000-000000000002', 'draws_on',
                        'persona_line', '00000000-0000-0000-0000-000000000003',
                        'persona_line.draws_on'),
                       ('character', 'unit-a#character:ada',
                        'about', 'person', 'ada', 'culture.key');
                INSERT INTO reasoning_strip_log
                  (id, path, model, reasoning_effort, model_calls, continuation_calls,
                   strip_calls, stripped_blocks)
                VALUES ('00000000-0000-0000-0000-000000000006', 'dreamer:persona-person',
                        'synthetic', 'low', 2, 1, 0, 0);
                INSERT INTO memory_projection_queue(kind, source_id, last_error)
                VALUES ('persona_line', '00000000-0000-0000-0000-000000000002', 'synthetic');
                INSERT INTO doctrine_revision
                  (corpus, content_sha256, assembled_sha256, files, hostname, loaded_at)
                VALUES ('dreamer', repeat('a',64), repeat('b',64),
                        '{"objectives/persona-person.md":"synthetic-typed",
                          "objectives/character-person.md":"synthetic-prose",
                          "objectives/culture.md":"synthetic-room"}',
                        'synthetic', now());
            """)
            )
            for name in (
                "culture",
                "character-person",
                "character-unit",
                "character-nora",
                "persona-person",
                "persona-room",
                "persona-unit",
                "persona-nora",
                "persona-verdict",
                "persona-person:history",
                "persona-person:source:message",
                "persona-unit:persona",
                "character-person:social",
            ):
                conn.execute(
                    sa.text(
                        "INSERT INTO consolidation_watermark "
                        "(consolidator_name, scope_id, last_message_id) "
                        "VALUES (:name, 'same-scope', 42)"
                    ),
                    {"name": name},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO dream_schedule_state "
                        "(adaptor_name, scope_id, last_fired_at) VALUES (:name, 'same-scope', 99)"
                    ),
                    {"name": name},
                )
            tables = (
                *OLD_TABLES,
                "consolidation_watermark",
                "dream_schedule_state",
                "memory_node",
                "memory_edge",
                "memory_projection_queue",
                "doctrine_revision",
                "reasoning_strip_log",
                "row_history",
            )
            before = _rows(conn, tables)
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert set(conn.execute(sa.text("SELECT part FROM profile_line")).scalars()) == {
                "facts",
                "traits",
                "guesses",
            }
            assert conn.execute(sa.text("SELECT count(*) FROM profile_line")).scalar_one() == 3
            assert conn.execute(sa.text("SELECT key FROM profile")).scalar_one() == "nora"
            assert set(conn.execute(sa.text(
                "SELECT profile_key FROM profile_line")).scalars()) == {"nora"}
            assert conn.execute(sa.text(
                "SELECT profile_key FROM profile_dispute")).scalar_one() == "nora"
            assert conn.execute(sa.text(
                "SELECT last_message_id FROM consolidation_watermark "
                "WHERE consolidator_name='profile-unit:profile'")).scalar_one() == 42
            assert conn.execute(sa.text("SELECT count(*) FROM culture")).scalar_one() == 1
            assert conn.execute(sa.text("SELECT count(*) FROM row_history")).scalar_one() == len(
                before["row_history"]
            )
            assert (
                conn.execute(
                    sa.text(
                        "SELECT last_message_id FROM consolidation_watermark "
                        "WHERE consolidator_name='profile-person:source:message'"
                    )
                ).scalar_one()
                == 42
            )
            assert (
                conn.execute(
                    sa.text(
                        "SELECT count(*) FROM memory_node "
                        "WHERE kind IN ('profile_line','character')"
                    )
                ).scalar_one()
                == 2
            )
            for table, column in (("dream_schedule_state", "adaptor_name"),
                                  ("consolidation_watermark", "consolidator_name")):
                for field in ("row_key", "image"):
                    names = conn.execute(sa.text(
                        f"SELECT {field}->>'{column}' FROM row_history "  # noqa: S608 - frozen fixture identifiers
                        "WHERE table_name = :table"
                    ), {"table": table}).scalars().all()
                    assert "profile-person" in names
                    assert "character-person" in names
                    assert not any(name.startswith("persona-")
                                   for name in names)
        command.downgrade(cfg, "0129_message_readings")
        with engine.connect() as conn:
            assert _rows(conn, tables) == before
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM profile_line")).scalar_one() == 3
            assert conn.execute(sa.text("SELECT key FROM profile")).scalar_one() == "nora"
            assert set(conn.execute(sa.text(
                "SELECT profile_key FROM profile_line")).scalars()) == {"nora"}
            assert conn.execute(sa.text(
                "SELECT profile_key FROM profile_dispute")).scalar_one() == "nora"
            assert conn.execute(sa.text(
                "SELECT last_message_id FROM consolidation_watermark "
                "WHERE consolidator_name='profile-unit:profile'")).scalar_one() == 42
    finally:
        engine.dispose()
