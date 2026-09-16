"""culture / culture_revision -- an account of how a room works and why, kept
over time (design/culture-tier, DEC-182..185).

WHY A STORE EXISTS AT ALL. A model asked to describe a room's culture reasons
from scratch and has no memory of its last answer, so any single account is a
snapshot. The chain of accounts is the evolution a single call cannot express,
and keeping snapshots IN ORDER is this tier's one genuine contribution. Without
the chain there is nothing here a prompt could not re-derive per turn.

NOTHING RETIRES (DEC-183). No `retired_at`, no `closing_condition`, no TTL, no
staleness column of any kind -- and their absence is a decision, not an
omission. A quiet room's culture is not stale, it is ACCURATE, so there is
nothing for a clock to invalidate. `parent_revision_id` reads "the same thing,
earlier" and never "superseded": the old account does not become false, it
becomes history, and the history is part of what the new one means. The
operator's test is that 「漢人」 did not retire when the Han dynasty did. The
first draft of this tier imported `standing`'s retirement lifecycle wholesale
and was wrong in every particular.

`body` IS PROSE, WITH NO SECTION SCHEMA (DEC-184). A three-section JSONB body
under a CHECK was drafted and rejected: it is the same enumeration error as
`participation.md`'s five `go` shapes one layer down, and it could not have
held 「秘書」 -- a role a room invented that no column could have anticipated.
Requiring a "what shifted" section also makes the FIRST revision unwritable, or
invites a `reasoning_effort = "none"` model to confabulate a diff to satisfy a
constraint. `note_revision` is the precedent and the reason it works is that
provenance sits AROUND the prose rather than decomposing it.

`culture.key` IS A `primary_id`, NOT `(platform, conversation_id)`. The rule is
`GLOSSARY.md`'s (PR #230) and it follows from which scheduler hands the key:
the ingest and tag paths get the message that arrived, so `messages`,
`standing` and `decision_log` key the thread; a Dreamer adaptor is handed
`scopes()`, so it keys the conversation. This is worth a paragraph because
getting it wrong FAILS SILENTLY -- a store keyed differently from its loop
matches zero rows on the watermark query, so the adaptor is scheduled, raises
nothing, logs nothing and never runs. All six live conversations hold exactly
one id today, so the two keyings are equal by accident of data; issue #229
carries what a second id should mean.

THE NUMBERS ARE WRITTEN OUT rather than read from `nora.infra.sql.schema`,
following `0053_decision_log` and `0035_turn_verdict`: a migration is a frozen
historical statement, and one reading a live constant silently changes what it
applied when the constant moves. `tests/infra/test_schema_parity.py` is what
catches a change to one that did not reach the other.

THE FOREIGN KEY RUNS `culture.head_revision_id -> culture_revision.id`, and the
first draft of this migration had it reversed. `culture_revision.culture_key ->
culture.key` fails on the FIRST FOLD OF EVERY ROOM: the revision must exist
before a head can point at it, and that constraint demanded the opposite. It was
caught by the store suite, not by review. The direction chosen also names the
integrity that matters -- a head pointing at a revision that is not there is
corruption every reader hits, while an unreferenced revision is history nothing
links to yet. `culture_revision` is therefore created FIRST.

`written_at DESC` is authored by hand for `idx_messages_conv_ts`'s reason:
autogenerate cannot see per-column index ordering and reports permanent phantom
drift on it.

Revision ID: 0055_culture
Revises: 0054_note_candidate_title_key
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0055_culture"
down_revision = "0054_note_candidate_title_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `culture_revision` FIRST: the head points at a revision, so that table
    # must exist before the constraint can name it.
    op.create_table(
        "culture_revision",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("culture_key", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("parent_revision_id", pg.UUID(as_uuid=False), nullable=True),
        sa.Column("written_at", sa.BigInteger, nullable=False),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.Column("instruction_sha256", sa.Text, nullable=True),
        sa.Column("model", sa.Text, nullable=True),
        sa.CheckConstraint(
            "instruction_sha256 is null or instruction_sha256 ~ '^[0-9a-f]{64}$'",
            name="culture_revision_instruction_sha256_check",
        ),
        sa.CheckConstraint(
            "model is null or length(btrim(model)) > 0",
            name="culture_revision_model_is_named_check",
        ),
        # 32000 characters, four times any operating cap, for
        # `0038_note_body_ceiling`'s reason: this bound says "this row is
        # corrupt", not "no writer should produce that". `length()` and never
        # `octet_length()` -- an account is routinely Cantonese, where the two
        # differ threefold.
        sa.CheckConstraint(
            "length(body) <= 32000",
            name="culture_revision_body_fits_the_ceiling_check",
        ),
        sa.CheckConstraint(
            "length(btrim(body)) > 0", name="culture_revision_has_a_body_check",
        ),
        sa.CheckConstraint(
            "length(btrim(established_by)) > 0",
            name="culture_revision_names_its_writer_check",
        ),
        sa.CheckConstraint(
            "parent_revision_id is null or parent_revision_id <> id",
            name="culture_revision_parent_is_not_self_check",
        ),
    )
    op.create_table(
        "culture",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("head_revision_id", pg.UUID(as_uuid=False), nullable=False),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.ForeignKeyConstraint(
            ["head_revision_id"], ["culture_revision.id"],
            name="culture_head_revision_id_fkey",
        ),
        sa.CheckConstraint("length(btrim(key)) > 0", name="culture_has_a_key_check"),
    )
    op.create_index(
        "idx_culture_revision_key_written", "culture_revision",
        ["culture_key", sa.text("written_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_culture_revision_key_written", table_name="culture_revision")
    op.drop_table("culture")
    op.drop_table("culture_revision")
