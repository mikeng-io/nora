"""social_poll: the back-fill's pages and where each member's back-fill stands.

design/cognition-loop/22-social-stream.md §7. A back-fill page is a
`social_poll` row of stage `backfill`, and `cursor` is where the next page
starts (null: the walk ended on that page). A member's back-fill stands at the
cursor of their latest `backfill` row that read a page, so the progress is
read off rows that are never changed, and a restart re-reads no page. A page
whose cursor the poller judged bad is recorded `abandoned`, which ends that
member's back-fill; one that failed unavailable while the bridge answered
nothing else is recorded `unavailable`, which does not count against its
cursor. The table stays APPEND-ONLY
(`nora.infra.sql.history.APPEND_ONLY`).

Follows `0110_memory_social`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0111_social_backfill
Revises: 0110_memory_social
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0111_social_backfill"
down_revision = "0110_memory_social"
branch_labels = None
depends_on = None

_STAGES = "'probe', 'story', 'items', 'item'"
_OUTCOMES = (
    "'unchanged', 'story_due', 'items_due', 'partial', 'count_fell', "
    "'read_ok', 'halted', 'backed_off', 'error'"
)


def _checks(stages: str, outcomes: str) -> None:
    op.drop_constraint("ck_social_poll_stage", "social_poll", type_="check")
    op.create_check_constraint("ck_social_poll_stage", "social_poll", f"stage IN ({stages})")
    op.drop_constraint("ck_social_poll_outcome", "social_poll", type_="check")
    op.create_check_constraint(
        "ck_social_poll_outcome", "social_poll", f"outcome IN ({outcomes})",
    )


def upgrade() -> None:
    op.add_column("social_poll", sa.Column("cursor", sa.Text))
    _checks(f"{_STAGES}, 'backfill'", f"{_OUTCOMES}, 'abandoned', 'unavailable'")


def downgrade() -> None:
    # The older code knows no back-fill stage: a back-fill page is a list
    # read to it, and an abandoned one a failed read.
    op.execute(
        "UPDATE social_poll SET outcome = 'error' "
        "WHERE outcome IN ('abandoned', 'unavailable')"
    )
    op.execute("UPDATE social_poll SET stage = 'items' WHERE stage = 'backfill'")
    _checks(_STAGES, _OUTCOMES)
    op.drop_column("social_poll", "cursor")
