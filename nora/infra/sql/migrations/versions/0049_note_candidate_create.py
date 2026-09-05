"""note_candidate gains a CREATE kind — the note tier's Dreamer-side create
path (design/dreamer-agent-loop/33-note-dreamer-schedule.md §4, issue #98).

**The table was a miss cache and stays one for `kind='miss'` rows.**
`note_candidate` today holds `(workspace, probe)` -> "we looked and found
nothing", written by `record_miss`. This adds a SECOND row shape sharing the
same table and id space: `kind='create'` is a full note proposal the
`note-candidates` adaptor read off a room's transcript, staged here for a
deterministic gate to decide create/update/refuse over — the model proposes,
code disposes, same split the fact tier's Phase A/B already enforces.

**`kind` defaults `'miss'`, so every existing row is one with no backfill
required** — there is nothing to migrate, only a new value to allow.

**`probe`/`first_seen_at`/`last_seen_at` become NULLABLE.** They were
`NOT NULL` with no default because every row used to be a miss, and a miss
always has a probe and a sighting window. A `kind='create'` row has no probe
in the miss-cache sense — it is not a question that came back empty, it is a
note proposed outright — so it cannot supply one. `first_seen_at`/
`last_seen_at` are reused for the create-candidate's OWN sighting window (when
this occasion was first/last proposed), which is why they stay on the shared
columns rather than growing a second pair.

**Two separate uniqueness domains, not one** (M10, the Kimi review finding 33
§4 records). `note_candidate_probe_key` — `(workspace, probe)` — is UNCHANGED
and keeps working exactly as before: Postgres treats every `NULL` probe as
distinct, so any number of `kind='create'` rows (all `probe IS NULL`) coexist
under it without a partial-index rewrite. The NEW domain is
`note_candidate_create_anchor_key`, a PARTIAL unique index scoped to
`kind='create'` over `(workspace, anchor_date, anchor_entity_ids)` — this
tier's own anchor key (`nora.domain.notes.anchor_key`), never `title`: two
spellings of one occasion must collide, and two notes sharing a title with
different anchors must not.

**Why a plain partial unique index works here, unlike the curator's own
merge key.** `nora.domain.notes.group_on_anchor_key` compares
`(anchor_date, RESOLVED entity ids)` — the entity half comes from
`note_entity`, a join, which cannot be a column in a unique index. This
column is different: it holds `anchor_entity_ids` EXACTLY AS THE MODEL WROTE
IT (unresolved, entity-resolution is issue #100's, not this adaptor's — see
`nora/language/note_candidates.py`), normalised (SORTED, DE-DUPLICATED) by
the writer before the row is inserted. A normalised array is an ordinary
value two rows can be equal or unequal on, so a plain multi-column partial
unique index expresses the key directly — no computed column, no expression
index, no dedupe pushed into application code. The normalisation is what
makes this correct: an UNSORTED array would let `{ada,ben}` and `{ben,ada}`
describe one occasion under two different keys, which is exactly the defect
`anchor_key` exists to close one layer up.

**`message_ids`** mirrors `exchange_observation.message_ids`
(`pg.ARRAY(sa.Integer)` — `messages.id` is `sa.Integer`, not a uuid or a
conversation id), not `observed_in` (`pg.ARRAY(sa.Text)`, which holds
CONVERSATION ids and stays as it is for the miss cache).

**`status`/`attempts` are NULLABLE/zero-defaulted respectively**, `fact_
candidate`'s own shape: `status` is meaningless for a `kind='miss'` row (the
CHECK below enforces that a `create` row always has one and a `miss` row
never does) and `attempts` counts materialization attempts a create-candidate
survives, defaulting to 0 for every row including existing miss rows (a
default that changes nothing for them).

**Every CHECK below is required BY THIS FILE, not by `schema.py` alone** —
`alembic check`/`compare_metadata` is blind to CHECK constraints
(CLAUDE.md; `tests/infra/test_schema_parity.py`'s own docstring). A
constraint declared only on the `sa.Table` is documentation; this migration
is what puts it in the database.

Read from the live database before writing this, as `0038`/`0048` both did:

    docker exec nora-db-1 psql -U nora -d nora -tAc \\
        "select count(*) from note_candidate"
    -> 0

Empty, so no existing row can violate `note_candidate_create_fields_check`
or the new NOT NULL relaxations, and there is nothing to backfill.

Revision ID: 0049_note_candidate_create
Revises: 0048_fact_attribution
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0049_note_candidate_create"
down_revision = "0048_fact_attribution"
branch_labels = None
depends_on = None

#: `nora.domain.notes.NOTE_BODY_CEILING_CHARS`, deliberately NOT imported —
#: `0038`'s own precedent: a migration is a historical record of what was
#: applied, and a constant that moves later must not silently rewrite what
#: this revision claims to have done.
BODY_CEILING_CHARS = 32000


def upgrade() -> None:
    op.add_column(
        "note_candidate",
        sa.Column("kind", sa.Text, nullable=False, server_default=sa.text("'miss'")),
    )
    op.alter_column("note_candidate", "probe", nullable=True)
    op.alter_column("note_candidate", "first_seen_at", nullable=True)
    op.alter_column("note_candidate", "last_seen_at", nullable=True)

    op.add_column("note_candidate", sa.Column("title", sa.Text))
    op.add_column("note_candidate", sa.Column("description", sa.Text))
    op.add_column("note_candidate", sa.Column("body", sa.Text))
    op.add_column("note_candidate", sa.Column("closing_condition", sa.Text))
    op.add_column("note_candidate", sa.Column("anchor_date", sa.Integer))
    op.add_column(
        "note_candidate",
        sa.Column("anchor_entity_ids", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
    )
    op.add_column(
        "note_candidate",
        sa.Column("message_ids", pg.ARRAY(sa.Integer), nullable=False,
                  server_default=sa.text("'{}'")),
    )
    op.add_column("note_candidate", sa.Column("status", sa.Text))
    op.add_column(
        "note_candidate",
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    )

    op.create_check_constraint(
        "note_candidate_kind_check", "note_candidate",
        "kind in ('miss', 'create')",
    )
    op.create_check_constraint(
        "note_candidate_miss_fields_check", "note_candidate",
        "kind <> 'miss' or (probe is not null and first_seen_at is not null "
        "and last_seen_at is not null)",
    )
    op.create_check_constraint(
        "note_candidate_create_fields_check", "note_candidate",
        "kind <> 'create' or (title is not null and description is not null "
        "and body is not null and closing_condition is not null and "
        "anchor_date is not null and status is not null)",
    )
    op.create_check_constraint(
        "note_candidate_status_check", "note_candidate",
        "status is null or status in ('pending', 'created', 'refused')",
    )
    op.create_check_constraint(
        "note_candidate_title_fits_the_index_check", "note_candidate",
        "title is null or (length(btrim(title)) > 0 and length(title) <= 60)",
    )
    op.create_check_constraint(
        "note_candidate_description_fits_the_index_check", "note_candidate",
        "description is null or (length(btrim(description)) > 0 and "
        "length(description) <= 200)",
    )
    op.create_check_constraint(
        "note_candidate_closing_condition_check", "note_candidate",
        "closing_condition is null or length(btrim(closing_condition)) > 0",
    )
    op.create_check_constraint(
        "note_candidate_body_has_content_check", "note_candidate",
        "body is null or length(btrim(body)) > 0",
    )
    op.create_check_constraint(
        "note_candidate_body_fits_the_ceiling_check", "note_candidate",
        f"body is null or length(body) <= {BODY_CEILING_CHARS}",
    )

    # The new uniqueness domain — see the module docstring for why a plain
    # partial unique index is correct once the writer normalises the array.
    op.create_index(
        "note_candidate_create_anchor_key", "note_candidate",
        ["workspace", "anchor_date", "anchor_entity_ids"],
        unique=True, postgresql_where=sa.text("kind = 'create'"),
    )


def downgrade() -> None:
    op.drop_index("note_candidate_create_anchor_key", table_name="note_candidate")
    op.drop_constraint(
        "note_candidate_body_fits_the_ceiling_check", "note_candidate", type_="check",
    )
    op.drop_constraint(
        "note_candidate_body_has_content_check", "note_candidate", type_="check",
    )
    op.drop_constraint(
        "note_candidate_closing_condition_check", "note_candidate", type_="check",
    )
    op.drop_constraint(
        "note_candidate_description_fits_the_index_check", "note_candidate",
        type_="check",
    )
    op.drop_constraint(
        "note_candidate_title_fits_the_index_check", "note_candidate", type_="check",
    )
    op.drop_constraint(
        "note_candidate_status_check", "note_candidate", type_="check",
    )
    op.drop_constraint(
        "note_candidate_create_fields_check", "note_candidate", type_="check",
    )
    op.drop_constraint(
        "note_candidate_miss_fields_check", "note_candidate", type_="check",
    )
    op.drop_constraint(
        "note_candidate_kind_check", "note_candidate", type_="check",
    )

    op.drop_column("note_candidate", "attempts")
    op.drop_column("note_candidate", "status")
    op.drop_column("note_candidate", "message_ids")
    op.drop_column("note_candidate", "anchor_entity_ids")
    op.drop_column("note_candidate", "anchor_date")
    op.drop_column("note_candidate", "closing_condition")
    op.drop_column("note_candidate", "body")
    op.drop_column("note_candidate", "description")
    op.drop_column("note_candidate", "title")

    op.alter_column("note_candidate", "last_seen_at", nullable=False)
    op.alter_column("note_candidate", "first_seen_at", nullable=False)
    op.alter_column("note_candidate", "probe", nullable=False)
    op.drop_column("note_candidate", "kind")
