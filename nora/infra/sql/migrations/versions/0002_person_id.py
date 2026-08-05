"""person_id — the resolved identity, persisted at ingest

Hand-written, like 0001. Two things this deliberately does NOT do:

- **No `op.batch_alter_table`.** On SQLite, batch mode rebuilds the table and
  recreates its indexes from *reflection* — and `schema.py` documents that
  reflection is exactly what cannot see `idx_messages_conv_ts`'s `ts DESC`
  ordering. A batch-mode add_column would therefore silently downgrade that
  index to ASC. Plain `ALTER TABLE ... ADD COLUMN` is an in-place header edit
  that touches no index at all.
- **No index on the new column.** Cardinality is a handful of people, and the
  one index that matters here must not be disturbed.

Nullable with no server default: NULL is meaningful and is the whole point of
the column — it records that the process, at ingest time, could not map this
sender to anyone. That is a different question from "is this sender mapped by
the file on disk right now", which `identity.toml` answers and which diverges
from reality whenever the operator edits it without restarting (mapping is
startup-only, Q4).

NOTE for whoever runs the gates after pulling this: `tests/infra/
test_schema_parity.py` compares against the live `data/messages.db`, so run
the migration (or start Nora once) BEFORE `uv run pytest -q`, or the schema
gate fails with a column-set mismatch that reads like a schema bug rather than
an un-upgraded database.

Revision ID: 0002_person_id
Revises: 0001_baseline
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_person_id"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("person_id", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "person_id")
