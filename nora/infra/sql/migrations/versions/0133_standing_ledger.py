"""Add Standing ledger heads and append-only typed revision chains."""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0133_standing_ledger"
down_revision = "0132_turn_records"
branch_labels = None
depends_on = None
METADATA = sa.MetaData()


def _derived(table: str, *, per_row: bool) -> list[Any]:
    """The columns every derived row carries (migration `0073_lineage`), with
    their CHECKs.

    `lineage` is the format written on every Rollup row
    (`nora.domain.lineage`): a JSON array of `[kind, subject, origin]` triples
    on a row with lineage per row (`per_row`, a note), an object of such
    arrays per section otherwise (Standing, culture). The empty default is
    every section unknown, which renders only in the row's origin room, so a
    writer that forgets it is on the fail-closed side. `reasoning` and
    `confidence` are NULL for "not stated".
    `as_of` is epoch ms, the clock the neighbouring `written_at`/`updated_at`
    columns use.
    """
    shape, shape_empty = ("array", "[]") if per_row else ("object", "{}")
    return [
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("confidence", sa.Text, nullable=True),
        sa.Column(
            "lineage", pg.JSONB, nullable=False, server_default=sa.text(f"'{shape_empty}'::jsonb")
        ),
        sa.Column(
            "exclude_persons", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("as_of", sa.BigInteger, nullable=True),
        sa.CheckConstraint(
            "reasoning is null or reasoning in "
            "('computed', 'told', 'deduced', 'induced', 'abduced', 'said')",
            name=f"{table}_reasoning_is_a_mode_check",
        ),
        sa.CheckConstraint(
            "confidence is null or confidence in "
            f"({', '.join(repr(c) for c in ('low', 'medium', 'high'))})",
            name=f"{table}_confidence_is_a_level_check",
        ),
        sa.CheckConstraint(
            f"jsonb_typeof(lineage) = '{shape}'",
            name=f"{table}_lineage_shape_check",
        ),
    ]


def _ledger_table(name: str, kind: str, *, history: bool = False) -> sa.Table:
    columns: list[Any] = [
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False, primary_key=history),
        sa.Column("parent_revision", sa.Integer),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("topic" if kind == "thread" else "subject", sa.Text, nullable=False),
        sa.Column(
            "made_at" if kind == "commitment" else "opened_at", sa.BigInteger, nullable=False
        ),
        sa.Column("last_revised_at", sa.BigInteger, nullable=False),
        sa.Column("closed_at", sa.BigInteger),
    ]
    if history:
        columns.append(
            sa.Column(
                "recorded_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("clock_timestamp()"),
            )
        )
    if kind != "thread":
        columns.append(sa.Column("body", sa.Text, nullable=False))
    if kind == "decision":
        columns.append(sa.Column("note_id", sa.Text))
    elif kind == "commitment":
        columns.extend(
            [
                sa.Column("owed_to", pg.ARRAY(sa.Text), nullable=False),
                sa.Column("due_at", sa.BigInteger),
            ]
        )
    else:
        columns.extend(
            [
                sa.Column("note_ids", pg.ARRAY(sa.Text), nullable=False),
                sa.Column("decision_ids", pg.ARRAY(sa.Text), nullable=False),
                sa.Column("message_ids", pg.ARRAY(sa.Integer), nullable=False),
                sa.Column("spans", pg.JSONB, nullable=False),
                sa.Column("last_moved_at", sa.BigInteger),
            ]
        )
    statuses = {
        "decision": "'open', 'closed', 'dropped'",
        "commitment": "'open', 'done', 'released'",
        "thread": "'open'",
    }[kind]
    table = sa.Table(
        name,
        METADATA,
        *columns,
        *_derived(name, per_row=True),
        sa.CheckConstraint("scope in ('room', 'unit')", name=f"{name}_scope_check"),
        sa.CheckConstraint(f"status in ({statuses})", name=f"{name}_status_check"),
        sa.CheckConstraint("revision > 0", name=f"{name}_revision_check"),
        sa.CheckConstraint(
            f"length(btrim({'topic' if kind == 'thread' else 'subject'})) > 0 AND "
            f"position(chr(10) in {'topic' if kind == 'thread' else 'subject'}) = 0",
            name=f"{name}_subject_check",
        ),
        sa.CheckConstraint("length(btrim(unit)) > 0", name=f"{name}_unit_check"),
        sa.CheckConstraint(
            "(revision = 1 AND parent_revision IS NULL) OR "
            "(revision > 1 AND parent_revision = revision - 1)",
            name=f"{name}_parent_check",
        ),
    )
    sa.Index(
        f"idx_{name}_scope", table.c.unit, table.c.scope, table.c.platform, table.c.conversation_id
    )
    return table


LEDGER_DECISION = _ledger_table("standing_decision", "decision")
LEDGER_COMMITMENT = _ledger_table("standing_commitment", "commitment")
LEDGER_THREAD = _ledger_table("standing_thread", "thread")
LEDGER_DECISION_REVISION = _ledger_table("standing_decision_revision", "decision", history=True)
LEDGER_COMMITMENT_REVISION = _ledger_table(
    "standing_commitment_revision", "commitment", history=True
)
LEDGER_THREAD_REVISION = _ledger_table("standing_thread_revision", "thread", history=True)


def upgrade() -> None:
    for table in METADATA.sorted_tables:
        op.create_table(
            table.name,
            *(c._copy() for c in table.columns),
            *(
                sa.CheckConstraint(str(c.sqltext), name=c.name)
                for c in table.constraints
                if isinstance(c, sa.CheckConstraint)
            ),
        )
        for index in table.indexes:
            op.create_index(index.name, table.name, [c.name for c in index.columns])

    for name in ("standing_decision", "standing_commitment", "standing_thread"):
        op.execute(
            f"CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON {name} FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
        )
        op.execute(
            f"CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON {name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
        )


def downgrade() -> None:
    for table in reversed(METADATA.sorted_tables):
        op.drop_table(table.name)
