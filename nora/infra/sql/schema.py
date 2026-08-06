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
    # LAST-BUT-ONE now; the append rule below is what decides the order.
    # `ALTER TABLE ADD COLUMN` appends to the end, so production has added
    # columns in the order their migrations ran; declaring one mid-table made a
    # fresh database order its columns differently, which shifted `ts`'s column
    # index and `test_indexes_match_production_exactly` caught it via
    # `PRAGMA index_xinfo`.
    sa.Column("person_id", sa.Text),
    # The `ts` of the row this one replies to (`dataMessage.quote.id` for
    # Signal), or NULL when the message is not a reply. Written in the same
    # INSERT the parser's value arrives on, never by a follow-up UPDATE — same
    # reason as `person_id` above. Unindexed on purpose: `idx_messages_conv_ts`
    # is the one index that matters and it must not be disturbed.
    #
    sa.Column("quoted_ts", sa.Integer),
    # What Nora HOLDS for this row's attachments: a JSON array of
    # `{sha256, media_type, size, filename}`, or NULL when she holds nothing —
    # which is both "there were no attachments" and "there were, and the bytes
    # could not be copied". Those two are distinguished by the body (`[image]`
    # with no handle) and by `nora_media_custody_total`, not by this column;
    # a column that claimed custody it did not have would be the A9 violation
    # this whole change exists to end.
    #
    # ONE JSON column, not four scalars, because a Signal message carries a
    # LIST of attachments — four columns would silently describe the first one
    # and lose the rest. And not a second table, because the retention story is
    # the whole reason media is being recorded carefully: `prune_all` already
    # deletes messages by conversation, a child table would need a cascade and
    # a second sweep to stay consistent with it, and the blob sweep reads the
    # surviving rows either way. The cost is that this column is not queryable
    # by hash without a scan; nothing queries it by hash, and an index here
    # would disturb the one index that matters.
    #
    # ANY FUTURE COLUMN GOES AFTER THIS ONE, for the append reason above.
    # `tests/infra/test_schema_parity.py::
    # test_the_column_order_of_a_fresh_database_matches_a_migrated_one` is what
    # fails if it does not.
    sa.Column("media_json", sa.Text),
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
