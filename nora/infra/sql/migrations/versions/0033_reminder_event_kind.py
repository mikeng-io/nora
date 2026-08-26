"""reminders — event-model abstraction columns (DEC-139).

Two columns on the existing `reminders` table. `kind` names WHAT a row is —
the default `'reminder'` is the whole of the pre-DEC-139 table, so every
existing row satisfies the NOT NULL the moment the column lands; `payload`
is the kind's denormalized shape, JSONB so a structured map needs no
further migration.

HAND-WRITTEN for 0004/0005's reason: the parity gate
(`tests/infra/test_schema_parity.py`) catches drift between this file and
`nora.infra.sql.schema.REMINDERS`. Autogenerate would omit the server
default and every live row would read back `None` for a NOT NULL column
until the next write.

EXISTING ROWS ARE NOT TOUCHED. `kind` defaults to `'reminder'` at the
server, so a row written before this revision reads back byte-identical
after it; `payload` is NULL and the fire path treats `None` as "no payload
to read". The one-shot/recurrence constraints are untouched — a kind/payload
pair is not consulted by the scheduling machinery (see the domain docstring).

The column list and its ORDER match `nora.infra.sql.schema.REMINDERS`
exactly; the parity gate fails on any drift.

Revision ID: 0033_reminder_event_kind
Revises: 0032_media_objects
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0033_reminder_event_kind"
down_revision = "0032_media_objects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column(
            "kind", sa.Text, nullable=False, server_default=sa.text("'reminder'"),
        ),
    )
    op.add_column("reminders", sa.Column("payload", pg.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("reminders", "payload")
    op.drop_column("reminders", "kind")
