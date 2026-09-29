"""cognition_session / cognition_session_close / cognition_would_write --
the Cognition adaptor's session and its dry-run write verbs, shadow
(design/cognition-loop/70-migration.md §2 row 3a: "the Cognition adaptor's
session and agenda; its note, event and Standing verbs in dry run (would-
write rows recorded, nothing committed)"; 30-cognition.md §1-§2; migration
step 3a).

WHAT THIS ADDS, additive only, three new tables:

`cognition_session` (new): one row per session, written once at its start
and never revised -- 30-cognition.md §1: "a session is replayable. Its row
records its inputs: the situation and agenda as handed, the stimulus ids it
claimed, the doctrine sha256, the skill versions injected, the config
revision in force, and the model id and route." `situation`/`agenda` are
the JSON shapes `nora.domain.cognition_session.Situation.as_dict` and
`AgendaItem.as_dict` (per item) build; `from_stimulus_id`/
`through_stimulus_id` are the consumed range, the same pair
`cognition_wake` already carries one table over. `mode` is `'shadow'`
only at this step, mirroring `cognition_wake`'s own
`ck_cognition_wake_mode` (0069/0070) -- a `'live'` value arrives with
cut-over (step 4) as its own migration, not backfilled here. `wake_id`
references the `cognition_wake` row (the would-wake) this session ran on;
nullable, because a test or a replay may construct a session with no
underlying wake row.

`cognition_session_close` (new): one row per agenda item's close
(30-cognition.md §2 "Closing" -- `written`, `no_change`, `waiting`,
`covered`, or code's own `struck` at `max_strikes`), append-only, one
session to many closes.

`cognition_would_write` (new): one row per write-verb call in shadow
(`nora.domain.cognition_write.WouldWrite`, as recorded) -- what it WOULD
have done, never what it did: `outcome` is `would_commit` (carrying
`candidate`, the row that would have been written) or `refused` (carrying
`refusal_reason`/`refusal_detail`). `tier` is one of `note`, `event`,
`standing` -- the three families 30-cognition.md §2's write-verb table
lists for Cognition, minus `memo` (memo ships at step 7 and no verb exists
yet, so 3a's own migration table row names only "note, event and Standing
verbs"). `session_id` is nullable: a would-write recorded outside a
session row (a direct wrapper test) still has somewhere to land.

ALL THREE ARE APPEND-ONLY (`nora.infra.sql.history.APPEND_ONLY`): no store
in `nora.infra.sql.cognition_dry_run` ever UPDATEs or DELETEs a row of any
of them, so none gets a `row_history` trigger -- the row IS its own
history, `cognition_wake`'s own precedent (0069/0070), `assess_report`'s
(0077) one table over.

FOREIGN KEYS point inward only (`cognition_session_close.session_id` and
`cognition_would_write.session_id` -> `cognition_session.id`;
`cognition_session.wake_id` -> `cognition_wake.id`) -- nothing outside this
step's three tables references them, so nothing downstream breaks if a
later step drops them at cut-over.

25 characters for the revision id: `alembic_version.version_num` is
varchar(32), and `tests/architecture/test_migration_revision_fits_version_column.py`
is what a longer name would fail.

Revision ID: 0078_cognition_session
Revises: 0077_assess_report
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0078_cognition_session"
down_revision = "0077_assess_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cognition_session",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column(
            "wake_id", sa.BigInteger,
            sa.ForeignKey("cognition_wake.id"), nullable=True,
        ),
        sa.Column("started_at_ms", sa.BigInteger, nullable=False),
        sa.Column("from_stimulus_id", sa.BigInteger, nullable=False),
        sa.Column("through_stimulus_id", sa.BigInteger, nullable=False),
        sa.Column("situation", pg.JSONB, nullable=False),
        sa.Column("agenda", pg.JSONB, nullable=False),
        sa.Column("doctrine_sha256", sa.Text, nullable=True),
        sa.Column(
            "skill_versions", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("config_revision_id", sa.BigInteger, nullable=True),
        sa.Column("model_id", sa.Text, nullable=True),
        sa.Column("model_route", sa.Text, nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint("mode IN ('shadow')", name="ck_cognition_session_mode"),
    )
    op.create_index(
        "idx_cognition_session_unit_id", "cognition_session", ["unit", "id"],
    )

    op.create_table(
        "cognition_session_close",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "session_id", sa.BigInteger,
            sa.ForeignKey("cognition_session.id"), nullable=False,
        ),
        sa.Column("item_index", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("strike", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "reason IN ('written', 'no_change', 'waiting', 'covered', 'struck')",
            name="ck_cognition_session_close_reason",
        ),
    )
    op.create_index(
        "idx_cognition_session_close_session", "cognition_session_close", ["session_id"],
    )

    op.create_table(
        "cognition_would_write",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "session_id", sa.BigInteger,
            sa.ForeignKey("cognition_session.id"), nullable=True,
        ),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("tier", sa.Text, nullable=False),
        sa.Column("verb", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("args", pg.JSONB, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("refusal_reason", sa.Text, nullable=True),
        sa.Column("refusal_detail", sa.Text, nullable=True),
        sa.Column("candidate", pg.JSONB, nullable=True),
        sa.Column("base_revision_id", sa.Text, nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "tier IN ('note', 'event', 'standing')", name="ck_cognition_would_write_tier",
        ),
        sa.CheckConstraint(
            "outcome IN ('would_commit', 'refused')",
            name="ck_cognition_would_write_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'would_commit' AND candidate IS NOT NULL) "
            "OR (outcome = 'refused' AND refusal_reason IS NOT NULL)",
            name="ck_cognition_would_write_outcome_shape",
        ),
    )
    op.create_index(
        "idx_cognition_would_write_session", "cognition_would_write", ["session_id"],
    )
    op.create_index(
        "idx_cognition_would_write_unit_recorded",
        "cognition_would_write", ["unit", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_cognition_would_write_unit_recorded", table_name="cognition_would_write")
    op.drop_index("idx_cognition_would_write_session", table_name="cognition_would_write")
    op.drop_table("cognition_would_write")

    op.drop_index("idx_cognition_session_close_session", table_name="cognition_session_close")
    op.drop_table("cognition_session_close")

    op.drop_index("idx_cognition_session_unit_id", table_name="cognition_session")
    op.drop_table("cognition_session")
