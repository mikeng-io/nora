"""rollup_response.follows — unquoted messages read as answering a send.

The response rollup counted a member's message as a response to one of Nora's
sends only when it quoted that send. In group chat most answers quote nothing,
so answered sends read `unanswered`, and an `ask` would have read `silent`.
`nora.domain.rollup.unquoted_follows` now reads a member's message soon after
her send, or one naming her, as answering it (design/cognition-loop/25-rollup.md
§3). This column keeps those apart from `replies`, which stays «quoted it»: a
reader can tell a quoted answer from an inferred one.

`integer`, NOT NULL, default 0: a build made before this holds no inferred
answer, and 0 says exactly that. The rollup is derived: the analytics process
rebuilds every row when its code version changes (`rollup-2a.2`), so no data
is backfilled here.

Follows `0098_culture_citations`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0099_rollup_response_follows
Revises: 0098_culture_citations
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0099_rollup_response_follows"
down_revision = "0098_culture_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rollup_response",
        sa.Column("follows", sa.Integer, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("rollup_response", "follows")
