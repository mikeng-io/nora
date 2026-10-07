"""culture_revision.citations — what each account revision rests on.

`character_revise` (design/self-growth/10-character.md §3, C2a) checks that every
citation of a revision resolves to a row its session was handed: a Record message
id (`"4812"`) or a Rollup response episode (`"response:4790"`, by her send's id).
This column keeps what was checked, so a revision can be re-derived from its
evidence and the share of revisions that cite anything can be read back.

`text[]`, NOT NULL, default empty: every row written before the verb has none, and
an empty array says exactly that. Nothing else changes; `culture_revision` stays
append-only.

Follows `0097_otp`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0098_culture_citations
Revises: 0097_otp
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0098_culture_citations"
down_revision = "0097_otp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "culture_revision",
        sa.Column(
            "citations", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )


def downgrade() -> None:
    op.drop_column("culture_revision", "citations")
