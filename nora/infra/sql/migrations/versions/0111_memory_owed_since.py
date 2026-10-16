"""memory_node.owed_since: when a node's vector became owed.

design/unified-memory/65-build-step-3-vectors.md §4.5. `memory_search` treats
a node waiting for its vector for under three worker ticks as fresh (read by
its words) and an older wait as a backlog (`partial`). `projected_at` cannot
measure that wait: every re-projection resets it, also when the input the
vector is made from did not change, so a failing node re-projected regularly
would look fresh forever and a rebuild would make a whole backlog fresh. This
column is written with the row and reset only where the projector clears the
vector (`_cleared_when`).

Existing rows take the earlier of `projected_at` and the node's own instant
(`at_ms`), the nearest bound on when their input was written that the table
holds: a line said a minute ago stays fresh, an old node re-projected since
does not.

Follows `0110_memory_social`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0111_memory_owed_since
Revises: 0110_memory_social
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0111_memory_owed_since"
down_revision = "0110_memory_social"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memory_node", sa.Column("owed_since", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE memory_node SET owed_since = "
        "LEAST(projected_at, to_timestamp(at_ms / 1000.0))"
    )
    op.alter_column(
        "memory_node", "owed_since", nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def downgrade() -> None:
    # Derived bookkeeping: the older code measures nothing by it.
    op.drop_column("memory_node", "owed_since")
