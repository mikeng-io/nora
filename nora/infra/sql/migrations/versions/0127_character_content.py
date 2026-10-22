"""Structured Character content; existing append-only rows remain readable."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0127_character_content"
down_revision = "0126_social_backfill_end"
branch_labels = None
depends_on = None

OLD = ("(part = 'identity' AND kind IN ('name', 'role', 'relationship', 'attribute', "
       "'instruction', 'purpose', 'norm')) OR (part = 'character' AND kind = 'trait') "
       "OR (part = 'hypothesis' AND kind = 'explanation')")
NEW = OLD.replace("kind = 'trait'", "kind IN ('trait', 'topic', 'habit', 'skill', "
                  "'preference', 'disposition', 'style', 'value', 'social', 'goal')")


def upgrade() -> None:
    for column in (
        sa.Column("content", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("location", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("source_medium", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("strength", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    ):
        op.add_column("persona_line", column)
    op.drop_constraint("ck_persona_line_kind_part", "persona_line", type_="check")
    op.create_check_constraint("ck_persona_line_kind_part", "persona_line", NEW)


def downgrade() -> None:
    # Fail rather than rewrite append-only dimension rows into legacy traits.
    op.drop_constraint("ck_persona_line_kind_part", "persona_line", type_="check")
    op.create_check_constraint("ck_persona_line_kind_part", "persona_line", OLD)
    for name in ("strength", "source_medium", "location", "content"):
        op.drop_column("persona_line", name)
