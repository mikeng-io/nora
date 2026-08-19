"""presence_stays, presence_derived_places, analytics_watermark — the
DERIVED layer (docs/superpowers/specs/2026-08-18-presence-analytics-
substrate-design.md §2, DEC-115). Two hand-written indexes because their
DESC/composite shape is autogenerate-blind, the same reason
`idx_presence_member_ts` is hand-written (design/19 §8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_presence_analytics"
down_revision = "0014_lang_obs_scope_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presence_stays",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("circle_id", sa.Text, nullable=False),
        sa.Column("started_ts", sa.BigInteger, nullable=False),
        sa.Column("ended_ts", sa.BigInteger, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("radius_m", sa.Float, nullable=False),
        sa.Column("fix_count", sa.Integer, nullable=False),
        sa.Column("place_id", sa.Integer, nullable=True),
    )
    op.execute(
        "CREATE INDEX idx_presence_stays_subject_ts "
        "ON presence_stays (subject_id, started_ts DESC)"
    )
    op.create_table(
        "presence_derived_places",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("circle_id", sa.Text, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("radius_m", sa.Float, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("label", sa.Text, nullable=True),
        sa.Column("saved_place_id", sa.Text, nullable=True),
        sa.Column("first_seen_ts", sa.BigInteger, nullable=False),
        sa.Column("last_seen_ts", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "kind IN ('home','work','unlabeled')", name="ck_derived_places_kind"
        ),
    )
    op.create_index(
        "idx_presence_derived_places_subject_kind",
        "presence_derived_places",
        ["subject_id", "kind"],
    )
    op.create_table(
        "analytics_watermark",
        sa.Column("deriver_name", sa.Text, primary_key=True),
        sa.Column("subject_id", sa.Text, primary_key=True),
        sa.Column("last_fix_id", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_cluster_id", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("analytics_watermark")
    op.drop_index(
        "idx_presence_derived_places_subject_kind",
        table_name="presence_derived_places",
    )
    op.drop_table("presence_derived_places")
    op.execute("DROP INDEX idx_presence_stays_subject_ts")
    op.drop_table("presence_stays")
