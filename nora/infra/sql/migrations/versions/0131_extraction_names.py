"""Rename Record message readings to extraction, including structural restore keys.

Revision ID: 0131_extraction_names
Revises: 0130_profile_names
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0131_extraction_names"
down_revision = "0130_profile_names"
branch_labels = None
depends_on = None

TABLES = ("extraction", "extraction_queue", "extraction_claim", "extraction_attempt")


def _functions(*, reverse: bool = False) -> None:
    def _execute(sql: str) -> None:
        if reverse:
            sql = (
                sql.replace("extraction_id", "reading_id")
                .replace("extraction_retire", "message_readings_retire")
                .replace("extraction_source_changed", "message_readings_source_changed")
                .replace("extraction_changed", "message_readings_changed")
                .replace("extraction", "message_reading")
            )
        op.execute(sql)

    _execute("""
        CREATE FUNCTION extraction_retire(source integer) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE retired bigint[];
        BEGIN
            WITH changed AS (
                UPDATE extraction SET state = 'retired', retired_at = now()
                WHERE state <> 'retired'
                  AND (source = ANY(source_ids) OR source = ANY(context_ids))
                RETURNING id
            ) SELECT array_agg(id) INTO retired FROM changed;
            UPDATE extraction_claim SET retired_at = now()
              WHERE extraction_id = ANY(retired) AND retired_at IS NULL;
            UPDATE extraction_queue SET extraction_id = NULL
              WHERE extraction_id = ANY(retired);
        END $$
    """)
    _execute("""
        CREATE FUNCTION extraction_source_changed() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'messages' THEN
                IF TG_OP = 'DELETE' THEN
                    PERFORM extraction_retire(OLD.id);
                    RETURN OLD;
                END IF;
                IF NEW.hidden_at IS DISTINCT FROM OLD.hidden_at
                   OR NEW.body IS DISTINCT FROM OLD.body
                   OR NEW.person_id IS DISTINCT FROM OLD.person_id
                   OR NEW.ts IS DISTINCT FROM OLD.ts
                   OR NEW.mention_persons_json IS DISTINCT FROM OLD.mention_persons_json THEN
                    PERFORM extraction_retire(NEW.id);
                END IF;
            ELSE
                PERFORM id FROM messages WHERE id = NEW.message_id FOR UPDATE;
                PERFORM extraction_retire(NEW.message_id);
            END IF;
            RETURN NEW;
        END $$
    """)
    _execute(
        "CREATE TRIGGER extraction_changed BEFORE UPDATE OR DELETE "
        "ON messages FOR EACH ROW EXECUTE FUNCTION extraction_source_changed()"
    )
    for table in ("message_edit", "message_delete"):
        _execute(
            f"CREATE TRIGGER extraction_changed BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION extraction_source_changed()"
        )


def _migrate(*, reverse: bool) -> None:
    conn = op.get_bind()
    old_prefix, new_prefix = (
        ("extraction", "message_reading") if reverse else ("message_reading", "extraction")
    )
    old_function = "extraction" if reverse else "message_readings"
    for table in ("messages", "message_edit", "message_delete"):
        op.execute(f"DROP TRIGGER {old_function}_changed ON {table}")
    op.execute(f"DROP FUNCTION {old_function}_source_changed()")
    op.execute(f"DROP FUNCTION {old_function}_retire(integer)")
    op.execute("SET LOCAL nora.row_history = 'off'")
    for target in TABLES:
        source = target.replace("extraction", old_prefix)
        destination = target.replace("extraction", new_prefix)
        constraints = (
            conn.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = CAST(:table AS regclass)"
                ),
                {"table": source},
            )
            .scalars()
            .all()
        )
        indexes = (
            conn.execute(
                sa.text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                    "AND tablename = :table "
                    "AND indexname NOT IN (SELECT conname FROM pg_constraint "
                    "WHERE conrelid = CAST(:table AS regclass))"
                ),
                {"table": source},
            )
            .scalars()
            .all()
        )
        sequence = conn.execute(
            sa.text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": source}
        ).scalar_one()
        op.rename_table(source, destination)
        old_column, new_column = (
            ("extraction_id", "reading_id") if reverse else ("reading_id", "extraction_id")
        )
        if target != "extraction":
            op.alter_column(destination, old_column, new_column_name=new_column)
        for name in constraints:
            # Only child tables rename a column. On downgrade, replace it
            # before the prefix replacement consumes extraction_id.
            if reverse and target != "extraction":
                new_name = name.replace(old_column, new_column).replace(old_prefix, new_prefix)
            else:
                new_name = name.replace(old_prefix, new_prefix).replace(old_column, new_column)
            op.execute(f'ALTER TABLE "{destination}" RENAME CONSTRAINT "{name}" TO "{new_name}"')
        for name in indexes:
            new_name = name.replace(old_prefix, new_prefix)
            op.execute(f'ALTER INDEX "{name}" RENAME TO "{new_name}"')
        if sequence:
            name = sequence.split(".")[-1]
            new_name = name.replace(old_prefix, new_prefix)
            op.execute(f'ALTER SEQUENCE "{name}" RENAME TO "{new_name}"')
        conn.execute(
            sa.text("UPDATE row_history SET table_name = :new WHERE table_name = :old"),
            {"new": destination, "old": source},
        )
        if target != "extraction":
            for field in ("image", "row_key"):
                conn.execute(
                    sa.text(
                        f"UPDATE row_history SET {field} = ({field} - :old) "  # noqa: S608 - frozen migration identifiers
                        f"|| jsonb_build_object(:new, {field}->:old) "
                        f"WHERE table_name = :table AND jsonb_exists({field}, :old)"
                    ).bindparams(
                        sa.bindparam("old", type_=sa.Text), sa.bindparam("new", type_=sa.Text)
                    ),
                    {"table": destination, "old": old_column, "new": new_column},
                )
    op.execute("SET LOCAL nora.row_history = 'on'")


def upgrade() -> None:
    _migrate(reverse=False)
    _functions()


def downgrade() -> None:
    _migrate(reverse=True)
    _functions(reverse=True)
