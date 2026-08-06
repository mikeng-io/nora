"""media_json — the custody index, so a placeholder stops pointing at somebody
else's filesystem

Hand-written, like 0001-0003, and for the reasons 0003 spells out in full:

- **No `op.batch_alter_table`.** Batch mode is one edit (`recreate="always"`,
  or any operation SQLite cannot do in place) away from rebuilding the table
  from REFLECTION — and reflection is exactly what cannot see
  `idx_messages_conv_ts`'s `ts DESC` ordering, so the rebuild silently
  downgrades it to ASC and `alembic check` reports nothing. A plain
  `ALTER TABLE ... ADD COLUMN` is an in-place header edit that cannot become
  one. `tests/infra/test_alembic.py` pins the index shape either side of this
  migration rather than trusting the form of the call.
- **No index on the new column.** Nothing queries by hash; the column is read
  back with the row it belongs to. The one index that matters must not be
  disturbed.

Nullable with no server default. NULL means "Nora holds no bytes for this row",
which covers both the ordinary case (no attachments — every row in this
database today) and the honest failure case (an attachment arrived and could
not be copied). Those are told apart by the BODY — `[image]` with no handle
versus `[image a3f2c1]` — and by `nora_media_custody_total`, never by inventing
a distinction in this column.

**No backfill, and this one is not the usual "only 20 rows" argument.** The
bytes for a historical attachment are in signal-cli's store or nowhere, and
there are no historical attachments: the live database has no row whose body
contains a placeholder, and signal-cli's data directory contains no
`attachments/` directory at all. A backfill would have nothing to find. What
DOES work retroactively is the placeholder taxonomy, because `raw_json` holds
the complete envelope for every row — a future re-render can turn a stored
`[attachment: x.aac]` into `[voice note]` without this column's help.

NOTE for whoever runs the gates after pulling this — same as 0003's:
`tests/infra/test_schema_parity.py` compares a fresh database against the live
`data/messages.db`, so run the migration (or start Nora once) BEFORE
`uv run pytest -q`, or the schema gate fails with a column-set mismatch that
reads like a schema bug rather than an un-upgraded database.

Revision ID: 0004_media_json
Revises: 0003_quoted_ts
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_media_json"
down_revision = "0003_quoted_ts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("media_json", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "media_json")
