"""summons_message_key -- claim a public summons on its CARD, not its comment
(design/instagram-adoption/19, the 2026-09-26 intake).

A summons now arrives as the bridge's SSE `summons` event, a DM card that
names the post but NOT the comment (measured). The ledger row is therefore
claimed on the card's DM message id before anything is read, and the comment
id is bound to it later, once the summoner's comment is found on the post:

* `message_id` is added, NOT NULL and UNIQUE: the new claim key
  (`INSERT ... ON CONFLICT (message_id) DO NOTHING`). A row written by the
  retired `GET /mentions` poller has no card, so it is backfilled as
  `'legacy:' || comment_id`: unique because `comment_id` was, and never equal
  to a real message id.
* `comment_id` becomes NULLABLE (an unresolved, stale or ignored card never
  gets one) and STAYS UNIQUE: NULLs are distinct in Postgres, and two cards
  that resolve to one comment still cannot both be answered.
* the state vocabulary gains `unresolved`: the bridge could not name the
  post the card points at, so nothing is read and nothing is said.

THE VOCABULARY IS WRITTEN OUT rather than read from `nora.domain.summons`,
following `0053_decision_log`: a migration is a frozen historical statement.

>>> RE-POINT AT MERGE TIME. <<< `down_revision` is the head of THIS branch's
tree when it was cut (`0066_instagram_summons`). If another migration landed
on master first, re-point this one onto it and rename nothing else; then
`uv run alembic heads` must print exactly ONE line (AGENTS.md, "Never assume
your migration's parent is unclaimed").

Revision ID: 0067_summons_message_key
Revises: 0066_instagram_summons
Create Date: 2026-09-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_summons_message_key"
down_revision = "0066_instagram_summons"
branch_labels = None
depends_on = None

_STATES_BEFORE = (
    "state IN ('claimed', 'ignored_summoner', 'stale', 'unreadable', "
    "'silent', 'staged', 'flushing', 'published', 'refused', 'capped', "
    "'duplicate', 'ambiguous', 'failed')"
)
_STATES_AFTER = (
    "state IN ('claimed', 'unresolved', 'ignored_summoner', 'stale', "
    "'unreadable', 'silent', 'staged', 'flushing', 'published', 'refused', "
    "'capped', 'duplicate', 'ambiguous', 'failed')"
)


def upgrade() -> None:
    op.add_column("instagram_summons", sa.Column("message_id", sa.Text, nullable=True))
    op.execute(
        "UPDATE instagram_summons SET message_id = 'legacy:' || comment_id "
        "WHERE message_id IS NULL"
    )
    op.alter_column("instagram_summons", "message_id", nullable=False)
    op.create_unique_constraint(
        "instagram_summons_message_id_key", "instagram_summons", ["message_id"],
    )
    op.alter_column("instagram_summons", "comment_id", nullable=True)
    op.drop_constraint("ck_instagram_summons_state", "instagram_summons", type_="check")
    op.create_check_constraint(
        "ck_instagram_summons_state", "instagram_summons", _STATES_AFTER,
    )


def downgrade() -> None:
    # LOSSY, and said so: a row with no comment (an unresolved, stale or
    # ignored card) cannot exist under 0066's NOT NULL comment_id, so it is
    # deleted. `unresolved` rows are among them, so the old CHECK holds.
    op.execute("DELETE FROM instagram_summons WHERE comment_id IS NULL")
    op.drop_constraint("ck_instagram_summons_state", "instagram_summons", type_="check")
    op.create_check_constraint(
        "ck_instagram_summons_state", "instagram_summons", _STATES_BEFORE,
    )
    op.alter_column("instagram_summons", "comment_id", nullable=False)
    op.drop_constraint(
        "instagram_summons_message_id_key", "instagram_summons", type_="unique",
    )
    op.drop_column("instagram_summons", "message_id")
