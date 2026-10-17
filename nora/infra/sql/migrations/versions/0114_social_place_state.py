"""social_item_state: a `place` state carrying an item's new tagged place;
social_poll: the `relist` stage.

design/cognition-loop/22-social-stream.md §5. `social_item` is APPEND-ONLY, so
a tag the author adds, edits or removes after the item was recorded is a new
`social_item_state` row, state `place`, its `place` the new place (NULL: the
tag was removed). It hides nothing; only `gone` and `hidden` do. A `relist`
row is one page of the once-only re-walk that re-lists a member's recorded
items so their places are compared, its `cursor` where the next page starts.

Follows `0113_memory_social_location`. `uv run alembic heads` must print
exactly ONE line.

Revision ID: 0114_social_place_state
Revises: 0113_memory_social_location
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0114_social_place_state"
down_revision = "0113_memory_social_location"
branch_labels = None
depends_on = None

_STAGES = "'probe', 'story', 'items', 'item', 'backfill'"
_STATES = "'gone', 'hidden'"


def _checks(stages: str, states: str) -> None:
    op.drop_constraint("ck_social_poll_stage", "social_poll", type_="check")
    op.create_check_constraint("ck_social_poll_stage", "social_poll", f"stage IN ({stages})")
    op.drop_constraint("ck_social_item_state_state", "social_item_state", type_="check")
    op.create_check_constraint(
        "ck_social_item_state_state", "social_item_state", f"state IN ({states})",
    )


def upgrade() -> None:
    op.add_column("social_item_state", sa.Column("place", sa.Text))
    _checks(f"{_STAGES}, 'relist'", f"{_STATES}, 'place'")
    op.create_check_constraint(
        "ck_social_item_state_place", "social_item_state", "place IS NULL OR state = 'place'",
    )


def downgrade() -> None:
    # The older code reads any state row as hiding the item, so a place row
    # must go; a relist page is a back-fill page to it, which it would replay
    # as one, so those go too.
    op.execute("DELETE FROM social_item_state WHERE state = 'place'")
    op.execute("DELETE FROM social_poll WHERE stage = 'relist'")
    op.drop_constraint("ck_social_item_state_place", "social_item_state", type_="check")
    _checks(_STAGES, _STATES)
    op.drop_column("social_item_state", "place")
