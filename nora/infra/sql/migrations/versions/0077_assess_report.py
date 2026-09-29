"""assess_report -- the Assess adaptor's one store (design/cognition-loop/
30-cognition.md §6, 15-map.md §6, 55-self.md §1, 70-migration.md §2 row 3;
migration step 3).

WHAT THIS ADDS, additive only:

`assess_report` (new): one row per Assess run, append-only
(`nora.infra.sql.history.APPEND_ONLY`, so no `row_history` trigger is
attached — the row IS its own history, `event_rsvps`'/`citation`'s own
precedent, `0075_withdrawal`'s docstring). `kind` names what kind of run
wrote it (`'nightly_dq'` today, the one `nora.app.assess_run` produces;
widened additively when a session-scoped or model-read report ships).
`run_at_ms` is the run's own clock, not `recorded_at` (system time): the two
usually agree, but a report replayed or backfilled from a recorded run keeps
its own moment separate from when this row entered the store, `standing`'s
`as_of`-vs-`recorded_at` split precedent. `results` is a JSONB array of
`nora.domain.assess.DqCheckResult` (`check`, `status`, `failing`, `sample`,
`detail`) written out by `nora.infra.sql.assess.SqlAssessReportStore.record`
— never parsed by a model, never read by a turn (15-map.md §6: "Assess …
reports and metrics only"; `tests/architecture/test_assess_reports_are_operator_only.py`
is this branch's own gate, which pins that no `read_only_tools()` entry and no
prefill block names `assess_report` or imports `nora.infra.sql.assess`).

Indexed on `(kind, run_at_ms)`: the operator page's own read ("the last N
nightly runs", "how has `check` trended") and the CLI's own "print the newest
report" both filter by kind and order by time.

Revision ID: 0077_assess_report
Revises: 0076_standing_sections
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0077_assess_report"
down_revision = "0076_standing_sections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assess_report",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("run_at_ms", sa.BigInteger, nullable=False),
        sa.Column(
            "results", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("kind IN ('nightly_dq')", name="ck_assess_report_kind"),
        sa.CheckConstraint(
            "jsonb_typeof(results) = 'array'", name="ck_assess_report_results_shape",
        ),
    )
    op.create_index(
        "idx_assess_report_kind_run", "assess_report", ["kind", "run_at_ms"],
    )


def downgrade() -> None:
    op.drop_index("idx_assess_report_kind_run", table_name="assess_report")
    op.drop_table("assess_report")
