"""presence_trips.sequence — the persisted per-trip road-name sequence that
makes corridor clustering accumulate across cycles (Tier 5, design/27 §4,
issue #6; final whole-branch review Critical #1).

Without it `_record_corridors` could only ever cluster ONE tick's in-memory
batch, so `corridor_min_samples` similar trips had to arrive inside a single
~120 s tick — which never happens in steady state, and Tier 5 would have
shipped minting zero corridors forever.

**No data migration.** The column is added NULL-able with no backfill, which
is correct for either starting state:
  - the real one — `presence_trips` does not exist in production at all
    (verified 2026-08-20: the live `nora` database is at revision
    `0020_room_portrait` and has no `presence_trips` table, because Tier 5's
    own `0019_presence_routes` has never been deployed), so there is nothing
    to backfill;
  - and the hypothetical one — a table that somehow already holds rows keeps
    them with `sequence IS NULL`, which
    `unclustered_named_trips_for_subject` excludes. Such a row is honestly
    unclusterable: its address sequence was never recorded and cannot be
    reconstructed, so fail-closed (never clustered) beats guessing.

Revision ID: 0022_presence_trips_sequence
Revises: 0021_presence_routes
Create Date: 2026-08-20

Renumbered at merge time from 0020_presence_trips_sequence — see
0021_presence_routes's own docstring for why.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0022_presence_trips_sequence"
down_revision = "0021_presence_routes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "presence_trips", sa.Column("sequence", pg.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("presence_trips", "sequence")
