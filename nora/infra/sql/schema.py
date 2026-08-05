"""The single source of truth for Nora's one table.

Every declaration below that looks redundant is load-bearing — each was
verified against the live database on 2026-08-05, and removing any one of them
makes a fresh database silently diverge from stamped production:

- `nullable=True` on `id`: SQLite's `INTEGER PRIMARY KEY AUTOINCREMENT` has
  `notnull=0`, but SQLAlchemy's `primary_key=True` implies `nullable=False`.
  Without this override, `alembic check` emits a `modify_nullable` diff against
  the production database while passing against a fresh one — and the natural
  "fix", `--autogenerate`, would emit a `batch_alter_column` table rebuild on
  production.
- `sqlite_autoincrement=True`: without it SQLAlchemy emits no `AUTOINCREMENT`,
  so rowids get reused. `append` prunes on a schedule and `recent` tie-breaks
  on `id`, so reuse would make the suite stop testing production's ordering.
- `sa.text("ts DESC")`: the index is descending on `ts` in production.
  `alembic check` cannot see this — declaring it as a plain column produced an
  empty diff — which is why `tests/infra/test_schema_parity.py` asserts it via
  `PRAGMA index_xinfo` instead.
"""

from __future__ import annotations

import sqlalchemy as sa

METADATA = sa.MetaData()

MESSAGES = sa.Table(
    "messages",
    METADATA,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=True),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("conversation_type", sa.Text, nullable=False),
    sa.Column("sender_id", sa.Text, nullable=False),
    sa.Column("sender_name", sa.Text),
    sa.Column("ts", sa.Integer, nullable=False),
    sa.Column("body", sa.Text),
    sa.Column("is_nora", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "delivery_status", sa.Text, nullable=False, server_default=sa.text("'pending'")
    ),
    sa.Column("raw_json", sa.Text),
    sa.Column(
        "created_at",
        sa.Text,
        nullable=False,
        server_default=sa.text("(datetime('now'))"),
    ),
    # The resolved person_id, or NULL when the sender mapped to nobody at the
    # moment of ingest. Written in the same INSERT (identity resolves before
    # the audit write), never by a follow-up UPDATE — WAL allows one writer and
    # this path drops the message on contention. Unindexed on purpose:
    # cardinality is a handful of people.
    #
    # LAST on purpose, and this position is load-bearing. `ALTER TABLE ADD
    # COLUMN` appends to the end, so production has it last; declaring it
    # mid-table made a fresh database order its columns differently, which
    # shifted `ts`'s column index and `test_indexes_match_production_exactly`
    # caught it via `PRAGMA index_xinfo`. Any future column goes here too.
    sa.Column("person_id", sa.Text),
    sa.CheckConstraint("conversation_type IN ('group','dm')"),
    sqlite_autoincrement=True,
)

# Declared after the table so the expression index can reference its columns.
# `idx_messages_conv_ts` is excluded from Alembic autogenerate by
# `migrations/env.py::include_object` — see that hook's comment.
IDX_CONV_TS = sa.Index(
    "idx_messages_conv_ts", MESSAGES.c.conversation_id, sa.text("ts DESC")
)
IDX_IS_NORA = sa.Index("idx_messages_is_nora", MESSAGES.c.is_nora)
