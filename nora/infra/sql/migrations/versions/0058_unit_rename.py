"""workspace -> unit -- the memory tier's social-unit key stops sharing a word
with Honcho's tenant.

**THE COLLISION.** Two unrelated things were both called `workspace`:

- Nora's own, DEC-105's: WHICH SOCIAL UNIT a room belongs to. Rooms sharing it
  are the same group of people reachable in more than one place, which is what
  makes `send_to` between them something other than a leak. It is the key on
  `note`, `note_candidate` and `decision_log`, and it comes from policy.toml.
- Honcho's: the first path segment of every call the memory client makes
  (`/v3/workspaces/{id}/...`). It is a TENANT -- one per Nora -- and it holds
  every unit at once. Its value is `memory.workspace_id`, shipped as `"nora"`.

They are at different levels and they were never the same set, but they were
spelled the same, and the resolvers for them were both called `workspace_for`.
That is how `NoteCurator._discharge` came to pass `note.workspace` as
`conclude(workspace_id=...)`: a person-claim discharged from a note in the
`friends` unit was addressed to a Honcho workspace called `friends`, which does
not exist, while `representation`/`chat` read the default and never looked
there. On this deployment the same line addressed `ridgeline` -- a workspace that
DOES exist and belongs to something else. Fixed in the same change; this
migration is the half of it that the database holds.

design/24 §S.1 already records the first time this happened, with the word
"group", and names the cost: "a session's worth of confusion". `unit` is that
section's own word for the thing ("a group of PEOPLE -- a social unit").

**SCOPE.** A pure rename: same column, same type, same nullability, same
defaults, same rows. Postgres carries index and constraint REFERENCES across
`RENAME COLUMN` on its own, so only the two identifiers that spell the word out
are renamed here. Nothing is read or rewritten, so this is O(catalog) on any
table size and needs no backfill.

**THE OPERATOR STEP THIS MIGRATION CANNOT DO.** policy.toml's key changes with
it (`workspace = "friends"` -> `unit = "friends"`). `_ConversationEntry` is
`extra="forbid"`, so an un-edited policy.toml is a LOUD startup failure, not a
room that silently rejoins the default unit -- which is the failure mode worth
having, but it does mean the file and the image must move together.
"""

from alembic import op

revision = "0058_unit_rename"
down_revision = "0057_reasoning_strip_log"
branch_labels = None
depends_on = None

#: Every table DEC-105's key lands on. The fact tier's three are here because
#: `nora.infra.sql.schema` still declares them and the schema-parity test reads
#: that declaration -- leaving them behind would drift the baseline, not save
#: work.
TABLES = (
    "note",
    "note_candidate",
    "decision_log",
    "fact",
    "fact_entity",
    "fact_candidate",
    "rendered_claim",
)

#: The only two index/constraint names that SPELL the column. Every other one
#: over it (`idx_note_standing`, `idx_note_anchor`, `idx_fact_standing`,
#: `idx_fact_disputed`, `note_candidate_probe_key`, `fact_candidate_triple_key`,
#: `note_candidate_create_anchor_key`) is named for its table and purpose, so
#: the rename leaves them correct and Postgres repoints them itself.
RENAMES = (
    ("index", None, "idx_decision_log_workspace_ts", "idx_decision_log_unit_ts"),
    ("constraint", "fact_entity",
     "fact_entity_workspace_name_key", "fact_entity_unit_name_key"),
)

#: Postgres 17 names NOT NULL constraints in its own catalog and `pg_dump`
#: emits them, so they reach the schema-parity baseline -- and a `RENAME
#: COLUMN` does not touch them. One per table, `<table>_workspace_not_null`,
#: on every table above except `rendered_claim`, whose column is nullable.
NOT_NULL = tuple(t for t in TABLES if t != "rendered_claim")


def upgrade() -> None:
    for table in TABLES:
        op.alter_column(table, "workspace", new_column_name="unit")
    _rename_identifiers(forward=True)


def downgrade() -> None:
    _rename_identifiers(forward=False)
    for table in TABLES:
        op.alter_column(table, "unit", new_column_name="workspace")


def _rename_identifiers(*, forward: bool) -> None:
    for kind, table, old, new in RENAMES:
        a, b = (old, new) if forward else (new, old)
        if kind == "index":
            op.execute(f'alter index "{a}" rename to "{b}"')
        else:
            op.execute(f'alter table {table} rename constraint "{a}" to "{b}"')
    for table in NOT_NULL:
        a, b = ("workspace", "unit") if forward else ("unit", "workspace")
        op.execute(
            f'alter table {table} rename constraint '
            f'"{table}_{a}_not_null" to "{table}_{b}_not_null"'
        )
