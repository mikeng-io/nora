"""ADR 0029: rename the typed shadow tier to profile.

Claims, clocks, modes, scope and immutable instruction blobs remain intact.
Structural restore keys move with their stores; live prose is untouched.
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "0130_profile_names"
down_revision = "0129_message_readings"
branch_labels = None
depends_on = None

TABLES = {"persona": "profile", "persona_line": "profile_line",
          "persona_dispute": "profile_dispute"}
COLUMNS = {"persona_key": "profile_key"}
PARTS = {"identity": "facts", "character": "traits", "hypothesis": "guesses"}
ADAPTORS = {
    "persona-unit:persona": "profile-unit:profile",
    **{f"persona-{kind}": f"profile-{kind}"
       for kind in ("person", "room", "unit", "nora", "verdict")},
}
KINDS = {"persona_line": "profile_line"}


def _names(value: str, mapping: dict[str, str]) -> str:
    pattern = "|".join(re.escape(x) for x in sorted(mapping, key=len, reverse=True))
    return re.sub(pattern, lambda m: mapping[m.group()], value)


def _case(column: str, mapping: dict[str, str], *, prefix: bool = False) -> str:
    # All identifiers and values are the frozen constants above, never input.
    cases = []
    for old, new in mapping.items():
        if prefix:
            cases.append(f"WHEN {column} = '{old}' OR {column} LIKE '{old}:%' "
                         f"THEN '{new}' || substring({column} FROM {len(old) + 1})")
        else:
            cases.append(f"WHEN {column} = '{old}' THEN '{new}'")
    return "CASE " + " ".join(cases) + f" ELSE {column} END"


def _rewrite(table: str, column: str, mapping: dict[str, str], *, prefix: bool = False) -> None:
    expression = _case(f'"{column}"', mapping, prefix=prefix)
    op.execute(f'UPDATE "{table}" SET "{column}" = {expression} '  # noqa: S608 - frozen migration/test identifiers
               f'WHERE "{column}" IS DISTINCT FROM ({expression})')


def _migrate(*, reverse: bool) -> None:
    tables = {v: k for k, v in TABLES.items()} if reverse else TABLES
    columns = {v: k for k, v in COLUMNS.items()} if reverse else COLUMNS
    parts = {v: k for k, v in PARTS.items()} if reverse else PARTS
    adaptors = {v: k for k, v in ADAPTORS.items()} if reverse else ADAPTORS
    kinds = {v: k for k, v in KINDS.items()} if reverse else KINDS
    names = {**tables, **columns}
    conn = op.get_bind()
    # A rename must not manufacture new versions or change system time.
    op.execute("SET LOCAL nora.row_history = 'off'")
    # Capture the actual constraints before the rename, preserving every rule.
    constraints = conn.execute(sa.text(
        "SELECT c.conname, c.contype, t.relname, pg_get_constraintdef(c.oid) AS definition "
        "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = 'public' AND t.relname = ANY(:tables)"
    ), {"tables": list(tables)}).all()
    for name, kind, table, _definition in constraints:
        if kind == "c":
            op.drop_constraint(name, table, type_="check")
    indexes = conn.execute(sa.text(
        "SELECT i.relname, t.relname FROM pg_index x "
        "JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid "
        "WHERE t.relname = ANY(:tables) "
        "AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.oid)"
    ), {"tables": list(tables)}).all()
    node_check = conn.execute(sa.text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'memory_node'::regclass AND conname = 'ck_memory_node_kind'"
    )).scalar_one()
    node_index = conn.execute(sa.text(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
        "AND indexname = 'idx_memory_node_unembedded'"
    )).scalar_one()
    op.drop_constraint("ck_memory_node_kind", "memory_node", type_="check")
    op.drop_index("idx_memory_node_unembedded", table_name="memory_node")
    for old, new in tables.items():
        op.rename_table(old, new)
    for table in tables.values():
        present = {x["name"] for x in sa.inspect(conn).get_columns(table)}
        for old, new in columns.items():
            if old in present:
                op.alter_column(table, old, new_column_name=new)
    entry = "persona_line" if reverse else "profile_line"
    _rewrite(entry, "part", parts)
    header = "persona" if reverse else "profile"
    keys = {"nora": "self"} if reverse else {"self": "nora"}
    # Current per-unit keys already end in #nora; translate the retired bare key.
    _rewrite(header, "key", keys)
    for table in (entry, "persona_dispute" if reverse else "profile_dispute"):
        _rewrite(table, "persona_key" if reverse else "profile_key", keys)
    for table, column in (("consolidation_watermark", "consolidator_name"),
                          ("dream_schedule_state", "adaptor_name")):
        # The source and destination vocabularies overlap. A two-phase rename
        # prevents unique-key collisions between typed and prose sessions.
        staged = {a: "__adr0029__" + b for a, b in adaptors.items()}
        _rewrite(table, column, staged, prefix=True)
        _rewrite(table, column, {v: v.removeprefix("__adr0029__")
                                 for v in staged.values()}, prefix=True)
    sessions = {**adaptors, **{"dream:" + a: "dream:" + b for a, b in adaptors.items()}}
    _rewrite(entry, "written_by_session", sessions, prefix=True)
    _rewrite("reasoning_strip_log", "path", {"dreamer:" + a: "dreamer:" + b
                                           for a, b in adaptors.items()}, prefix=True)
    for table, cols in (("memory_node", ("kind",)),
                        ("memory_projection_queue", ("kind",)),
                        ("memory_edge", ("src_kind", "dst_kind"))):
        for column in cols:
            _rewrite(table, column, kinds)
    # Edges carry source-column provenance, not authored text.
    provenance = {f"persona_line.{column}": f"profile_line.{COLUMNS.get(column, column)}"
                  for column in ("citations", "persona_key", "fact_value",
                                 "draws_on", "supersedes")}
    _rewrite("memory_edge", "provenance", {v: k for k, v in provenance.items()}
             if reverse else provenance)
    quoted_kinds = {f"'{a}'": f"'{b}'" for a, b in kinds.items()}
    op.execute('ALTER TABLE memory_node ADD CONSTRAINT ck_memory_node_kind '
               + _names(node_check, quoted_kinds))
    op.execute(_names(node_index, quoted_kinds))
    # Instruction-version lookup keys move; their digests continue to identify
    # the exact original immutable revision_blob bytes.
    file_names = {f"objectives/{a}.md": f"objectives/{b}.md" for a, b in adaptors.items()}
    file_case = _case("key", file_names)
    op.execute("UPDATE doctrine_revision SET files = (SELECT jsonb_object_agg("  # noqa: S608 - frozen migration/test identifiers
               + file_case + ", value) FROM jsonb_each(files)) WHERE files <> '{}'::jsonb")
    # Restore readers use these structural keys. Do not rewrite arbitrary
    # message text, historical tools, snapshots of prompts or instruction blobs.
    for column in ("table_name",):
        _rewrite("row_history", column, tables)
    for table in tables.values():
        for old, new in columns.items():
            for field in ("image", "row_key"):
                op.execute(f"UPDATE row_history SET {field} = ({field} - '{old}') "  # noqa: S608 - frozen migration/test identifiers
                           f"|| jsonb_build_object('{new}', {field}->'{old}') "
                           f"WHERE table_name = '{table}' AND {field} ? '{old}'")
    # Restore header keys and parts in historical typed row images as well.
    for table, column in ((header, "key"),
                          (entry, "persona_key" if reverse else "profile_key"),
                          ("persona_dispute" if reverse else "profile_dispute",
                           "persona_key" if reverse else "profile_key")):
        for field in ("row_key", "image"):
            expression = _case(f"{field}->>'{column}'", keys)
            op.execute(f"UPDATE row_history SET {field} = jsonb_set({field}, "  # noqa: S608
                       f"'{{{column}}}', to_jsonb({expression})) "
                       f"WHERE table_name = '{table}' AND {field} ? '{column}'")
    # Restore readers resolve schedule and watermark history just like live cursors.
    for table, column in (("consolidation_watermark", "consolidator_name"),
                          ("dream_schedule_state", "adaptor_name")):
        for field in ("row_key", "image"):
            staged = {a: "__adr0029__" + b for a, b in adaptors.items()}
            for mapping in (staged, {v: v.removeprefix("__adr0029__")
                                    for v in staged.values()}):
                expression = _case(f"{field}->>'{column}'", mapping, prefix=True)
                op.execute(f"UPDATE row_history SET {field} = jsonb_set({field}, "  # noqa: S608 - frozen migration/test identifiers
                           f"'{{{column}}}', to_jsonb({expression})) "
                           f"WHERE table_name = '{table}' AND {field} ? '{column}'")
    for old, kind, table, definition in constraints:
        target, new = tables[table], _names(old, names)
        if kind == "c":
            definition = _names(definition, columns)
            # Only quoted part values, not the Character tier's table name.
            if target == entry:
                definition = _names(definition, {f"'{a}'": f"'{b}'" for a, b in parts.items()})
            op.execute(f'ALTER TABLE "{target}" ADD CONSTRAINT "{new}" {definition}')
        elif old != new:
            op.execute(f'ALTER TABLE "{target}" RENAME CONSTRAINT "{old}" TO "{new}"')
    for old, _table in indexes:
        new = _names(old, names)
        if old != new:
            op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')
    op.execute("SET LOCAL nora.row_history = 'on'")


def upgrade() -> None:
    _migrate(reverse=False)


def downgrade() -> None:
    _migrate(reverse=True)
