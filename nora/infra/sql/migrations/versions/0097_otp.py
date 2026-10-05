"""otp_challenge, otp_attempt — one-time codes sent in a direct message (`nora.app.otp`).

Two APPEND-ONLY tables. A challenge row holds who the code is for, why (`sign_in` or
`adopt`), which platform carried it, an HMAC of the code (never the code), and when
it expires. Every check of a code is one `otp_attempt` row; a challenge is spent by
its one `ok` attempt (`uq_otp_attempt_ok` lets Postgres decide a race between two),
dead after the configured number of failed ones, and superseded by a later
challenge for the same person and purpose. Nothing is updated, so no
`nora_row_history` trigger: `nora.infra.sql.history.APPEND_ONLY` names
`recorded_at` as each table's system time. No foreign key between them, for the
reason `0084_documents` gives.

Follows `0096_compute_jobs_checkpoint`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0097_otp
Revises: 0096_compute_jobs_checkpoint
Create Date: 2026-10-05
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0097_otp"
down_revision = "0096_compute_jobs_checkpoint"
branch_labels = None
depends_on = None


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "otp_challenge",
        sa.Column("challenge_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("purpose", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _recorded_at(),
        sa.CheckConstraint(
            "purpose IN ('sign_in', 'adopt')", name="ck_otp_challenge_purpose",
        ),
        sa.CheckConstraint(
            "platform IN ('signal', 'whatsapp')", name="ck_otp_challenge_platform",
        ),
        sa.CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'", name="ck_otp_challenge_code_hash",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_otp_challenge_expiry"),
    )
    op.create_index(
        "idx_otp_challenge_person", "otp_challenge", ["person_id", "purpose", "issued_at"],
    )
    op.create_table(
        "otp_attempt",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("challenge_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        _recorded_at(),
    )
    op.create_index(
        "uq_otp_attempt_ok", "otp_attempt", ["challenge_id"],
        unique=True, postgresql_where=sa.text("ok"),
    )


def downgrade() -> None:
    # Going back down forgets every code issued; a code in flight stops working,
    # which is what a code that cannot be checked should do.
    op.drop_index("uq_otp_attempt_ok", table_name="otp_attempt")
    op.drop_table("otp_attempt")
    op.drop_index("idx_otp_challenge_person", table_name="otp_challenge")
    op.drop_table("otp_challenge")
