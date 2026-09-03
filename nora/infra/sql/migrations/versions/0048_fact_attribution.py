"""fact_candidate.asserted_* and fact.asserted_* -- who said it, and when
(design/dreamer-agent-loop/32-fact-dreamer-schedule.md §3.4, §5 closed
decision 2).

**Why on BOTH tables.** The candidate row carries the claim's attribution —
the `person_id` (or `sender_id`, when the sender mapped to nobody) and the
`ts` of the FIRST message the claim was sighted in, joined from `messages` by
`SqlFactCandidateStore.propose`. The fact row carries the same pair, copied
from the settling candidate at adjudication time (`SqlFactStore.propose`), so
the rendered `<facts>` block can say who said it and when rather than only
the web warrant. `fact_asserter` already records every attribution the join
could make; this pair is the FIRST one, denormalised onto the rows the render
path already reads, so saying «who said it» costs no second join.

**NULLABLE, and NULL is the only "unattributable".** `prune_all` deletes
messages by conversation on a schedule while `observed_in` survives it, so a
candidate whose evidence is gone has no asserter to record — and must not
fabricate one, the same argument migration 0047 made for
`music_session.last_played_*` and 0037 made for `consumed_verdict_tag`. A
sentinel (`''`, `0`) would compare equal to a real blank or epoch and turn
«I could not attribute this» into a false attribution.

**NO BACKFILL, deliberately.** Existing rows get NULL, which means "no
attribution on record". Backfilling by joining `observed_in[1]` to `messages`
would attribute rows whose FIRST message has since been pruned to a LATER
sighting, and an attribution that points at the wrong message is worse than
none — it reads as provenance and is none.

Revision ID: 0048_fact_attribution
Revises: 0047_music_session_last_played
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_fact_attribution"
down_revision = "0047_music_session_last_played"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact_candidate", sa.Column("asserted_by", sa.Text, nullable=True))
    op.add_column("fact_candidate", sa.Column("asserted_at", sa.BigInteger, nullable=True))
    op.add_column("fact", sa.Column("asserted_by", sa.Text, nullable=True))
    op.add_column("fact", sa.Column("asserted_at", sa.BigInteger, nullable=True))


def downgrade() -> None:
    op.drop_column("fact", "asserted_at")
    op.drop_column("fact", "asserted_by")
    op.drop_column("fact_candidate", "asserted_at")
    op.drop_column("fact_candidate", "asserted_by")
