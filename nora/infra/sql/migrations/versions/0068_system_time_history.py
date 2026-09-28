"""system_time_history -- every store Nora writes is append-only in system
time, so she can be restored as of any T (design/cognition-loop/10-tiers.md
§8; 70-migration.md §2 row 1b and §5).

WHAT THIS ADDS, all additive (no table dropped, no reader changed):

1. `row_history` and the trigger function `nora_row_history()`, attached
   AFTER INSERT OR UPDATE OR DELETE to every table 70 §5 lists as changing in
   place, except the rows it marks for steps 2 and 2b (events, RSVPs and the
   presence Rollup tables), plus `sticker_index`, an in-place cache the
   inventory missed. Each write appends the new version and closes the one it
   replaced (`superseded_at`, `superseded_by`) in the SAME transaction; a
   DELETE appends a `delete` version carrying the removed row, so a prune or
   a retention sweep hides a row from the head and keeps it in history.
   The live tables are unchanged, so every reader and writer is unchanged.

   A trigger rather than an append at each write site, because the write
   sites are ~60 statements across 17 modules (upserts, compare-and-swaps,
   batch updates, deletes), and a history that misses one site is silently
   incomplete: a trigger records every path by construction, including one
   added next month. The list of tables is written out below rather than
   imported from `nora.infra.sql.history`, following `0053_decision_log`: a
   migration is a frozen historical statement.
   `tests/infra/test_row_history.py` checks the two agree.

2. `recorded_at` on the append-only tables a restore reads (the note,
   standing and culture revision chains, `message_edit`, `decision_log`,
   `note_merge_decision`): the database's clock at insert, by server
   default.

3. `revision_blob`, `doctrine_revision`, `config_revision`,
   `deploy_revision`: what instructions, config and code were in force when
   (written by `nora.app.revisions` at load).

THE BACKFILL, and what it cannot know. History starts here; for each row
that already exists the migration writes the versions its own columns still
prove, at the best time they record, and the current row last:

- `note`: one version per `note_revision` (head at that revision, at its
  `written_at`, retirement blanked when it came later), then the row as it
  is now at `retired_at` (or the last revision). `strikes`, `revisit_at` and
  `last_wake_revision_id` read as TODAY's in every backfilled version: their
  past values were overwritten and are not recoverable.
- `culture`: one version per `culture_revision` (head at it, at its
  `written_at`).
- `standing`: one version per `standing_revision` (at its `updated_at`); a
  head with no revision (folded only before `0059`) gets the head alone.
- `reminders`, `compute_jobs`: a `pending` version at `created_at`, then the
  row as it is now at its terminal stamp (`fired_at`/`completed_at`). A
  CANCELLED reminder records no time, so its cancellation is dated at
  `created_at` — it is never outstanding in any restore, the direction
  `nora.infra.sql.snapshot` already chose (a pending row is what fires). A
  row in flight with no stamp is dated at this migration.
- every other table: the row as it is now, at its own birth or last-touched
  time — `messages.ts` (delivery status and adoption before today are
  collapsed into it), `note_candidate.first_seen_at`,
  `media_objects.created_at_ms` (an earlier description, if one was
  replaced, is gone), `presence_fixes.recorded_at`, each state table's
  `updated_at`, `version_announcements.announced_at`,
  `instagram_summons.updated_at`, `video_readings.created_at`,
  `music_queue.added_at`, `geocode_cache.resolved_at`,
  `sticker_index.first_seen`.
- a row DELETED before today (a pruned message, a fix past retention) is
  gone and cannot be restored.
- the append-only tables' `recorded_at` is backfilled from their own write
  time (`written_at`, `updated_at`, `ts`, epoch ms).

Cost: one history row per existing row of the covered tables, written once
at upgrade; afterwards one more row per write. 70 §6 already prices that
growth ("nothing derived is overwritten").

>>> RE-POINT AT MERGE TIME. <<< `down_revision` is the head of master when
this branch was cut (`0067_summons_message_key`). Step 1 (signals) adds a
migration on another branch; whichever merges second re-points onto the
other and renames nothing else. `uv run alembic heads` must then print
exactly ONE line (AGENTS.md, "Never assume your migration's parent is
unclaimed").

Revision ID: 0068_system_time_history
Revises: 0067_summons_message_key
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0068_system_time_history"
down_revision = "0067_summons_message_key"
branch_labels = None
depends_on = None

#: The in-place tables and their key columns (the trigger's arguments).
#: Frozen here; `nora.infra.sql.history.HISTORY_TABLES` is the live copy.
_HISTORY_TABLES: dict[str, tuple[str, ...]] = {
    "messages": ("id",),
    "note_candidate": ("id",),
    "note": ("id",),
    "culture": ("key",),
    "standing": ("platform", "conversation_id"),
    "reminders": ("id",),
    "compute_jobs": ("id",),
    "instagram_summons": ("id",),
    "version_announcements": ("version", "platform", "conversation_id"),
    "participation_window_state": ("platform", "conversation_id"),
    "dream_schedule_state": ("adaptor_name", "scope_id"),
    "consolidation_watermark": ("consolidator_name", "scope_id"),
    "analytics_watermark": ("deriver_name", "subject_id"),
    "tag_debounce_state": ("platform", "conversation_id"),
    "media_objects": ("sha256",),
    "video_readings": (
        "sha256", "provider", "model", "chunk_seconds", "chunk_max", "reading_digest",
    ),
    "presence_fixes": ("id",),
    "music_queue": ("id",),
    "music_session": ("guild_id",),
    "geocode_cache": ("lat_rounded", "lng_rounded"),
    "sticker_index": ("platform", "fetch_token"),
}

#: Append-only tables gaining `recorded_at`, each with the SQL that dates a
#: row that already exists.
_APPEND_ONLY_BACKFILL: dict[str, str] = {
    "note_revision": "to_timestamp(written_at / 1000.0)",
    "standing_revision": "to_timestamp(updated_at / 1000.0)",
    "culture_revision": "to_timestamp(written_at / 1000.0)",
    "message_edit": "to_timestamp(ts / 1000.0)",
    "decision_log": "to_timestamp(ts / 1000.0)",
    "note_merge_decision": "to_timestamp(ts / 1000.0)",
}

#: The time the CURRENT row of each table is dated at, over alias `x`.
#: `now()` (this migration) is the last resort for a row with no time at all.
_CURRENT_AT: dict[str, str] = {
    "messages": "to_timestamp(x.ts / 1000.0)",
    "note_candidate": "to_timestamp(x.first_seen_at / 1000.0)",
    "note": "to_timestamp(greatest(x.created_at, coalesce(x.retired_at, 0)) / 1000.0)",
    "culture": "to_timestamp(x.updated_at / 1000.0)",
    "standing": "to_timestamp(x.updated_at / 1000.0)",
    "reminders": (
        "CASE WHEN x.fired_at IS NOT NULL THEN x.fired_at "
        "WHEN x.state IN ('pending', 'cancelled') THEN x.created_at ELSE now() END"
    ),
    "compute_jobs": (
        "CASE WHEN x.completed_at IS NOT NULL THEN x.completed_at "
        "WHEN x.state = 'pending' THEN x.created_at ELSE now() END"
    ),
    "instagram_summons": "coalesce(x.updated_at, x.seen_at)",
    "version_announcements": "x.announced_at",
    "participation_window_state": "x.updated_at",
    "dream_schedule_state": "x.updated_at",
    "consolidation_watermark": "x.updated_at",
    "analytics_watermark": "x.updated_at",
    "tag_debounce_state": "x.updated_at",
    "media_objects": "to_timestamp(x.created_at_ms / 1000.0)",
    "video_readings": "x.created_at",
    "presence_fixes": "to_timestamp(coalesce(x.recorded_at, x.ts) / 1000.0)",
    "music_queue": "x.added_at",
    "music_session": "x.updated_at",
    "geocode_cache": "x.resolved_at",
    "sticker_index": "x.first_seen",
}

#: Versions a table's own columns prove BEFORE its current row, each a
#: SELECT of (row_key, image, recorded_at) over alias `x`. `{key}` is the
#: table's key expression.
_EARLIER_VERSIONS: dict[str, tuple[str, ...]] = {
    "note": (
        # the head at each revision, with a later retirement blanked
        "SELECT {key}, CASE WHEN x.retired_at IS NOT NULL AND x.retired_at > r.written_at "
        "THEN to_jsonb(x) || jsonb_build_object('head_revision_id', r.id, "
        "'retired_at', NULL, 'replaced_by', NULL, 'retire_reason', NULL) "
        "ELSE to_jsonb(x) || jsonb_build_object('head_revision_id', r.id) END, "
        "to_timestamp(r.written_at / 1000.0) "
        "FROM note x JOIN note_revision r ON r.note_id = x.id",
    ),
    "culture": (
        "SELECT {key}, to_jsonb(x) || jsonb_build_object('head_revision_id', r.id, "
        "'updated_at', r.written_at), to_timestamp(r.written_at / 1000.0) "
        "FROM culture x JOIN culture_revision r ON r.culture_key = x.key",
    ),
    "standing": (
        "SELECT {key}, to_jsonb(x) || jsonb_build_object('body', r.body, "
        "'fold_count', r.fold_count, 'updated_at', r.updated_at, "
        "'last_folded_row_id', r.last_folded_row_id, 'working_set', r.working_set), "
        "to_timestamp(r.updated_at / 1000.0) "
        "FROM standing x JOIN standing_revision r "
        "ON r.platform = x.platform AND r.conversation_id = x.conversation_id",
    ),
    "reminders": (
        "SELECT {key}, to_jsonb(x) || jsonb_build_object('state', 'pending', "
        "'fired_at', NULL, 'outcome', NULL), x.created_at FROM reminders x "
        "WHERE x.state <> 'pending' OR x.fired_at IS NOT NULL OR x.outcome IS NOT NULL",
    ),
    "compute_jobs": (
        "SELECT {key}, to_jsonb(x) || jsonb_build_object('state', 'pending', "
        "'completed_at', NULL, 'result_url', NULL, 'result_summary', NULL, "
        "'error', NULL), x.created_at FROM compute_jobs x WHERE x.state <> 'pending'",
    ),
}

_TRIGGER_FUNCTION = """
CREATE FUNCTION nora_row_history() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    at timestamptz := clock_timestamp();
    new_image jsonb;
    old_image jsonb;
    new_key jsonb := '{}'::jsonb;
    old_key jsonb := '{}'::jsonb;
    col text;
    version_id bigint;
BEGIN
    -- The one bypass: `nora snapshot` seeds a sandbox's heads from a source's
    -- history and copies that history across itself; recording the seeding
    -- as new writes would date every restored row at the moment of the copy.
    IF current_setting('nora.row_history', true) = 'off' THEN
        RETURN NULL;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_image := to_jsonb(NEW);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        old_image := to_jsonb(OLD);
    END IF;
    -- An UPDATE that changed nothing (an upsert writing the values already
    -- there) is not a new version.
    IF TG_OP = 'UPDATE' AND new_image = old_image THEN
        RETURN NULL;
    END IF;
    FOREACH col IN ARRAY TG_ARGV LOOP
        IF new_image IS NOT NULL THEN
            new_key := new_key || jsonb_build_object(col, new_image -> col);
        END IF;
        IF old_image IS NOT NULL THEN
            old_key := old_key || jsonb_build_object(col, old_image -> col);
        END IF;
    END LOOP;
    -- A delete, or an update that moved the key: the old key's row is gone.
    IF TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND new_key <> old_key) THEN
        version_id := nextval('row_history_id_seq');
        UPDATE row_history SET superseded_at = at, superseded_by = version_id
         WHERE table_name = TG_TABLE_NAME AND row_key = old_key
           AND superseded_at IS NULL;
        INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
        VALUES (version_id, TG_TABLE_NAME, old_key, 'delete', old_image, at);
        IF TG_OP = 'DELETE' THEN
            RETURN NULL;
        END IF;
    END IF;
    version_id := nextval('row_history_id_seq');
    UPDATE row_history SET superseded_at = at, superseded_by = version_id
     WHERE table_name = TG_TABLE_NAME AND row_key = new_key
       AND superseded_at IS NULL;
    INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
    VALUES (version_id, TG_TABLE_NAME, new_key, lower(TG_OP), new_image, at);
    RETURN NULL;
END
$fn$
"""


def _key_sql(columns: tuple[str, ...], alias: str = "x") -> str:
    pairs = ", ".join(f"'{c}', to_jsonb({alias}.\"{c}\")" for c in columns)
    return f"jsonb_build_object({pairs})"


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=True,
    )


def _backfill(table: str, columns: tuple[str, ...]) -> None:
    key = _key_sql(columns)
    earlier = _EARLIER_VERSIONS.get(table, ())
    for select in earlier:
        op.execute(
            "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "  # noqa: S608 — table and column names are module constants, never input
            f"SELECT '{table}', v.k, 'backfill', v.i, v.t "
            f"FROM ({select.format(key=key)}) AS v(k, i, t)"
        )
    current_at = f"coalesce({_CURRENT_AT[table]}, now())"
    if not earlier:
        op.execute(
            "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "  # noqa: S608 — table and column names are module constants, never input
            f"SELECT '{table}', {key}, 'backfill', to_jsonb(x), {current_at} "
            f"FROM \"{table}\" x"
        )
        return
    # The current row, unless the newest earlier version already IS it; never
    # dated before that version, so the chain ends on what the head holds.
    op.execute(
        "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "  # noqa: S608 — table and column names are module constants, never input
        f"SELECT '{table}', {key}, 'backfill', to_jsonb(x), "
        f"greatest({current_at}, last.recorded_at) "
        f"FROM \"{table}\" x LEFT JOIN LATERAL ("
        "  SELECT h.image, h.recorded_at FROM row_history h "
        f"  WHERE h.table_name = '{table}' AND h.row_key = {key} "
        "  ORDER BY h.recorded_at DESC, h.id DESC LIMIT 1"
        ") last ON true "
        "WHERE last.image IS NULL OR last.image <> to_jsonb(x)"
    )


def _link(table: str) -> None:
    op.execute(
        "WITH chain AS ("  # noqa: S608 — table and column names are module constants, never input
        "  SELECT id, lead(id) OVER w AS next_id, lead(recorded_at) OVER w AS next_at "
        f"  FROM row_history WHERE table_name = '{table}' "
        "  WINDOW w AS (PARTITION BY row_key ORDER BY recorded_at, id)"
        ") "
        "UPDATE row_history h SET superseded_by = c.next_id, superseded_at = c.next_at "
        "FROM chain c WHERE h.id = c.id AND c.next_id IS NOT NULL"
    )


def upgrade() -> None:
    # 1. the chain table
    op.create_table(
        "row_history",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("row_key", pg.JSONB, nullable=False),
        sa.Column("op", sa.Text, nullable=False),
        sa.Column("image", pg.JSONB, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.BigInteger, nullable=True),
        sa.CheckConstraint(
            "op IN ('backfill', 'insert', 'update', 'delete')",
            name="row_history_op_is_known_check",
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL) = (superseded_by IS NULL)",
            name="row_history_supersession_is_whole_check",
        ),
    )
    op.create_index(
        "idx_row_history_key", "row_history", ["table_name", "row_key", "recorded_at"],
    )
    op.create_index(
        "idx_row_history_table_recorded", "row_history", ["table_name", "recorded_at"],
    )

    # 2. recorded_at on the append-only tables. Added nullable, backfilled,
    # then tightened: a volatile default on ADD COLUMN would rewrite each
    # table with this migration's clock on every existing row.
    for table, dated in _APPEND_ONLY_BACKFILL.items():
        op.add_column(table, _recorded_at())
        op.execute(f"UPDATE {table} SET recorded_at = {dated}")  # noqa: S608 — table and column names are module constants, never input
        op.alter_column(
            table, "recorded_at", nullable=False,
            server_default=sa.text("clock_timestamp()"),
        )

    # 3. the backfill, then the chain links, then the one-open-version rule,
    # then the triggers (the backfill must not fire them).
    for table, columns in _HISTORY_TABLES.items():
        _backfill(table, columns)
        _link(table)
    op.create_index(
        "uq_row_history_open", "row_history", ["table_name", "row_key"],
        unique=True, postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.execute(_TRIGGER_FUNCTION)
    for table, columns in _HISTORY_TABLES.items():
        args = ", ".join(f"'{c}'" for c in columns)
        op.execute(
            f"CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON \"{table}\" FOR EACH ROW EXECUTE FUNCTION nora_row_history({args})"
        )

    # 4. what was in force: content, instruction loads, config, code.
    op.create_table(
        "revision_blob",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="revision_blob_sha256_check"),
    )
    op.create_table(
        "doctrine_revision",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("corpus", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.Text, nullable=False),
        sa.Column("assembled_sha256", sa.Text, nullable=True),
        sa.Column("files", pg.JSONB, nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hostname", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "corpus IN ('doctrine', 'dreamer')", name="doctrine_revision_corpus_check",
        ),
    )
    op.create_index(
        "idx_doctrine_revision_corpus_loaded", "doctrine_revision",
        ["corpus", "loaded_at"],
    )
    op.create_table(
        "config_revision",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("file", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.Text, nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hostname", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "file IN ('config.toml', 'policy.toml', 'identity.toml')",
            name="config_revision_file_check",
        ),
    )
    op.create_index(
        "idx_config_revision_file_loaded", "config_revision", ["file", "loaded_at"],
    )
    op.create_table(
        "deploy_revision",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_status", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=True),
        sa.Column("git_sha", sa.Text, nullable=True),
        sa.Column("built_at", sa.Text, nullable=True),
        sa.Column("image_id", sa.Text, nullable=True),
        sa.Column("hostname", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "manifest_status IN ('ok', 'absent', 'unreadable')",
            name="deploy_revision_manifest_status_check",
        ),
    )


def downgrade() -> None:
    # LOSSY, and said so: the history of every write since this migration is
    # dropped with `row_history`, and every recorded load with the revision
    # tables. The live tables are untouched.
    op.drop_table("deploy_revision")
    op.drop_index("idx_config_revision_file_loaded", table_name="config_revision")
    op.drop_table("config_revision")
    op.drop_index("idx_doctrine_revision_corpus_loaded", table_name="doctrine_revision")
    op.drop_table("doctrine_revision")
    op.drop_table("revision_blob")
    for table in _HISTORY_TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS nora_row_history ON "{table}"')
    op.execute("DROP FUNCTION IF EXISTS nora_row_history()")
    for table in _APPEND_ONLY_BACKFILL:
        op.drop_column(table, "recorded_at")
    op.drop_index("uq_row_history_open", table_name="row_history")
    op.drop_index("idx_row_history_table_recorded", table_name="row_history")
    op.drop_index("idx_row_history_key", table_name="row_history")
    op.drop_table("row_history")
