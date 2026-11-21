"""The computed tally keyed to the claim head the intentions rested on."""

import sqlalchemy as sa


def lesson_tally_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "lesson_tally", metadata,
        sa.Column("claim_id", sa.Text, nullable=False),
        sa.Column("support", sa.Integer, nullable=False),
        sa.Column("contradiction", sa.Integer, nullable=False),
        sa.CheckConstraint("support >= 0 AND contradiction >= 0", name="ck_lesson_tally_counts"),
    )


def attach_lesson_tally_constraints(table: sa.Table) -> None:
    table.append_constraint(sa.PrimaryKeyConstraint("claim_id", "unit", name="pk_lesson_tally"))
    table.append_constraint(sa.ForeignKeyConstraint(
        ["claim_id", "unit"], ["claim.id", "claim.unit"], name="fk_lesson_tally_claim",
    ))
