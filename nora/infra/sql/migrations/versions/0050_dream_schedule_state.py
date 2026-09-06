"""dream_schedule_state — a watermark-free scheduled adaptor's clock, in the
database instead of in process memory.

**The defect this closes was measured, not predicted.** `_dream_adaptor_once`
kept a watermark-free adaptor's last-fire time in `dream_loop`'s own
`dict[str, float]`, stamped with `time.monotonic()`. The dict is function-local
to one `dream_loop` call, so every restart began with an empty clock, and the
branch's own comment says what follows: *"The first tick fires (no recorded
fire), then the interval holds."* On 2026-09-06 the `nora` container was
force-recreated twice inside two hours — the routine cost of a bind-mounted
`config.toml` that git rewrote with a new inode, which `restart` does not pick
up. A daily adaptor on that clock fires once per deploy, not once per day.

It is also **structurally unmeasurable**: `nora_dream_run_total` counts a run,
not the reason for it, so a clock that reset and a clock that legitimately
elapsed produce the same series. Nothing in the metric layer could have caught
this, which is why it is a table rather than a longer comment.

**A separate table, not a column on `consolidation_watermark`.** That table's
`last_message_id` is load-bearing for `watermark_is_behind_the_record`, the
fail-closed check that reports `nora_dream_evidence_lost_total` when a cursor
falls below the record's oldest surviving row. A watermark-free adaptor has no
message cursor to put there; writing 0 would trip that check on every tick
forever, and teaching the check to skip such rows would make one table answer
two questions. `analytics_watermark` is the precedent for a second, narrower
watermark table with its own key.

**`last_fired_at` is epoch milliseconds on the WALL clock.** The fire
condition it feeds is a daily SLOT — `floor((t - offset) / interval)`, so a
schedule means "once per UTC day, at this offset" rather than "once per 86400
elapsed seconds" — and a slot is arithmetic over an absolute instant.
`monotonic()` cannot express it, and a `DateTime` column would be the only
bigint timestamp in this schema rendered as one.

**No backfill, and none is possible.** The clock this replaces never persisted
anything, so there is no prior state to carry. Every `(adaptor, scope)` pair
starts absent, which the reader maps to «never fired» — the same first-tick-
fires behaviour as before, once, and then a real daily cadence that survives
the next deploy.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_dream_schedule_state"
down_revision = "0049_note_candidate_create"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dream_schedule_state",
        sa.Column("adaptor_name", sa.Text, primary_key=True),
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("last_fired_at", sa.BigInteger, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("dream_schedule_state")
