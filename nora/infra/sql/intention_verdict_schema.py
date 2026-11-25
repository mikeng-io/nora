"""The computed head aggregate for one ended achieve intention."""

import sqlalchemy as sa


def intention_verdict_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "intention_verdict", metadata,
        sa.Column("intention_id", sa.Text, nullable=False),
        sa.Column("ending", sa.Text, nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("witness_id", sa.Text),
        sa.Column("form", sa.Text, nullable=False),
        sa.Column("owed_to", sa.Text, nullable=False),
        sa.Column("condition_form", sa.Text),
        sa.CheckConstraint(
            "ending IN ('achieved', 'dropped', 'leaked', 'unknown')",
            name="ck_intention_verdict_ending",
        ),
        sa.CheckConstraint("form = 'achieve'", name="ck_intention_verdict_form"),
        sa.CheckConstraint(
            "condition_form IN ('act_event', 'identity_event', 'predicate', 'deadline', 'untyped')",
            name="ck_intention_verdict_condition_form",
        ),
    )


def attach_verdict_constraints(table: sa.Table) -> None:
    """Attach constraints that use the shared aggregate planes."""
    for constraint in (
        sa.PrimaryKeyConstraint("intention_id", "unit", name="pk_intention_verdict"),
        sa.ForeignKeyConstraint(
            ["intention_id", "unit"], ["intention.id", "intention.unit"],
            name="fk_intention_verdict_intention",
        ),
        sa.ForeignKeyConstraint(
            ["witness_id", "unit"], ["registry.id", "registry.unit"],
            name="fk_intention_verdict_witness",
        ),
    ):
        table.append_constraint(constraint)
    sa.Index("idx_intention_verdict_unit_ended", table.c.unit, table.c.ended_at)
