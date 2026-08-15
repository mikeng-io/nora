"""sticker_index + media_descriptions — the two-level media dedup cache
(sticker spec §4.1).

Revision ID: 0012_sticker_cache
Revises: 0011_presence_speed
Create Date: 2026-08-15

Numbered 0012, not 0011, and chained onto 0011 rather than 0010 — both facts
are the residue of a collision worth recording, because the same shape will
recur every time two branches add a migration in the same week.

This branch originally wrote 0011_sticker_cache off 0010. A presence-speed
migration was in flight at the same time, also numbered 0011, also off 0010:
two revisions, one parent, which is alembic multi-head and makes it refuse to
run at all. The rename to 0012 yielded the number while presence was still
UNCOMMITTED, and at that moment down_revision had to stay 0010 — pointing at
a revision that existed only as an untracked file on somebody else's checkout
would have broken every other checkout with "Can't locate revision".

0011_presence_speed has since landed on master (246d503), still chained to
0010. So the two-head problem arrived from the other direction exactly as
predicted, and this is the repair: with 0011 now real and reachable, 0012
chains onto it and master is a single line again, 0010 → 0011 → 0012.

The lesson, if you are the next person here: renumbering the FILE is
cosmetic. Alembic reads the revision graph, so it is down_revision that
decides whether there is one head or two, and it can only point at a revision
that is actually committed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_sticker_cache"
down_revision = "0011_presence_speed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sticker_index",
        sa.Column("platform", sa.Text, primary_key=True),
        sa.Column("fetch_token", sa.Text, primary_key=True),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False, server_default=""),
        sa.Column("size", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("hits", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "media_descriptions",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("media_descriptions")
    op.drop_table("sticker_index")
