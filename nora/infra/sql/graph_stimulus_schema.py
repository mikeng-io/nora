"""The Cognitive Graph stimulus queue."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from nora.domain.graph_stimuli import STIMULUS_KINDS


def graph_stimulus_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "graph_stimulus", metadata,
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("graph_id", sa.Text, nullable=False),
        sa.Column("occurrence", sa.Text, nullable=False, server_default=""),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("room", sa.Text),
        sa.Column("person", sa.Text),
        sa.Column("cues", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("revision_id", sa.Text),
        sa.Column("dependents", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("unit", "kind", "graph_id", "occurrence", name="pk_graph_stimulus"),
        sa.CheckConstraint(f"kind IN {STIMULUS_KINDS!s}", name="ck_graph_stimulus_kind"),
        sa.ForeignKeyConstraint(
            ("graph_id", "unit"), ("registry.id", "registry.unit"), name="fk_graph_stimulus_row",
        ),
        sa.ForeignKeyConstraint(
            ("revision_id", "unit"), ("registry.id", "registry.unit"),
            name="fk_graph_stimulus_revision",
        ),
        sa.Index("idx_graph_stimulus_eligible", "unit", "eligible_at"),
    )


def graph_stimulus_claim_table(metadata: sa.MetaData) -> sa.Table:
    key = ("unit", "kind", "graph_id", "occurrence")
    return sa.Table(
        "graph_stimulus_claim", metadata,
        *(sa.Column(name, sa.Text, nullable=False) for name in key),
        sa.Column("claimant", sa.Text, nullable=False),
        sa.Column("pass_id", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("claimed", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(*key, "claimant", name="pk_graph_stimulus_claim"),
        sa.ForeignKeyConstraint(key, tuple(f"graph_stimulus.{name}" for name in key),
                                name="fk_graph_stimulus_claim_stimulus"),
        sa.UniqueConstraint("unit", "pass_id", name="uq_graph_stimulus_claim_pass"),
        sa.CheckConstraint("state IN ('claimed', 'closed', 'released')",
                           name="ck_graph_stimulus_claim_state"),
        sa.CheckConstraint("(state = 'claimed') = (settled IS NULL)",
                           name="ck_graph_stimulus_claim_settled"),
    )
