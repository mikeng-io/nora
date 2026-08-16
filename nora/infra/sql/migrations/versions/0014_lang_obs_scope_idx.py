"""language_observation(scope_id) index — `rescore`'s own read path.

`rescore` (`nora.infra.sql.language.SqlLanguageStore`) runs `WHERE scope_id =
:s` against `language_observation` on every dream-cycle tick. That table has
no index at all before this migration — only the natural-key
`UniqueConstraint` — and it is append-only, deliberately outside
`store.prune_enabled`'s reach, so it only ever grows. Without this index,
every tick is a sequential scan of every scope's evidence.

A PLAIN `CREATE INDEX`, not `CONCURRENTLY` — unlike `idx_messages_dream_lag`
(`0013_room_language.py`), which was added against an already-live,
already-growing `messages` table and needed `CONCURRENTLY`'s
`autocommit_block()` to avoid stalling ingest, `language_observation` is a
brand-new, empty table in every deployment this migration reaches. There is
nothing for `CONCURRENTLY` to avoid locking.
"""

from __future__ import annotations

from alembic import op

revision = "0014_lang_obs_scope_idx"
down_revision = "0013_room_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_language_observation_scope",
        "language_observation",
        ["scope_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_language_observation_scope", table_name="language_observation")
