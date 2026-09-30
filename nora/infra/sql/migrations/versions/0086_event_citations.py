"""event citations -- the `citation` edge table admits events and Rollup keys,
gains a `part`, and `events` gains `unsupported_at` and `field_meta`
(design/cognition-loop/40-register.md §3-§4, 10-tiers.md §2 rule 7 and §3;
the review of 2026-09-30 M2/M4/m6 and of #662 M8/m4/m6; migration step E1a).

WHAT THIS ADDS, all additive:

1. `citation.citing_store` admits `'event'` (the key is `events.id` as text),
   and `citation.cited_store` admits `'rollup'` (the key is the text a note and
   an event cite a Rollup row by, `rollup(kind|subject|from/to)` without the
   wrapper: `nora.domain.note_body.rollup_key`). 0075 scoped the vocabulary to
   what a citation could name then and said a later migration ALTERs it; this
   is that migration.

2. `citation.part` (nullable): the field or section an edge supports, so a
   per-field lineage can be read off the edges. Every row written before this
   migration keeps `NULL` (a note, Standing or culture citation supports the
   row as a whole). The edge's uniqueness widens to include `part`
   (`NULLS NOT DISTINCT`, Postgres 15+: two whole-row edges to one message
   still collapse to one, and one message cited for two fields is two edges).

3. `events.unsupported_at` (epoch ms, nullable): the withdrawal cascade marks
   an event whose citations reach a withdrawn message, the way it marks a note,
   Standing or culture head (0075). `events` is a HISTORY table, so a mark is
   captured as an ordinary version; no row_history catch-up (0075's argument:
   an ADD COLUMN fires no row trigger and a reader defaults a missing key).

4. `events.field_meta` and `event_revision.field_meta` (jsonb object, default
   `{}`): each field's own `reasoning`, `confidence` and `as_of`, keyed by
   field name (`what`, `start`, `end`, `place`, `people`, `status`), because
   10 §3 gives an event those per FIELD and 0074 could only carry them
   row-level until a writer stated one. The row-level `reasoning` and
   `confidence` columns stay.

5. `citation` moves from append-only to a HISTORY table (the trigger
   `nora_row_history`, `0068`'s function). An event's edges for a field are
   REPLACED when a revision restates the field, so the cascade follows what the
   event now rests on (10 §2 rule 7: the row's CURRENT citation chain), and
   that is a DELETE: the trigger keeps each removed edge as a `delete` version,
   so what an earlier revision rested on is still re-derivable. The table's own
   history starts here, at what its columns prove now (`0074`'s pattern: one
   `backfill` version per row, dated at `recorded_at`).

Revision ID: 0086_event_citations
Revises: 0085_cognition_cause_none
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0086_event_citations"
down_revision = "0085_cognition_cause_none"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("citation_citing_store_is_known_check", "citation", type_="check")
    op.create_check_constraint(
        "citation_citing_store_is_known_check", "citation",
        "citing_store in ('note', 'standing', 'culture', 'event')",
    )
    op.drop_constraint("citation_cited_store_is_known_check", "citation", type_="check")
    op.create_check_constraint(
        "citation_cited_store_is_known_check", "citation",
        "cited_store in ('message', 'rollup')",
    )
    op.add_column("citation", sa.Column("part", sa.Text, nullable=True))
    op.create_check_constraint(
        "citation_part_is_not_blank_check", "citation",
        "part is null or length(btrim(part)) > 0",
    )
    op.drop_constraint("citation_edge_unique", "citation", type_="unique")
    op.create_unique_constraint(
        "citation_edge_unique", "citation",
        ["citing_store", "citing_key", "cited_store", "cited_id", "part"],
        postgresql_nulls_not_distinct=True,
    )

    op.add_column("events", sa.Column("unsupported_at", sa.BigInteger, nullable=True))
    for table in ("events", "event_revision"):
        op.add_column(table, sa.Column(
            "field_meta", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ))
        op.create_check_constraint(
            f"ck_{table}_field_meta_shape", table, "jsonb_typeof(field_meta) = 'object'",
        )

    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON \"citation\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"citation\" "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )
    op.execute("""
        INSERT INTO row_history (table_name, row_key, op, image, recorded_at)
        SELECT 'citation', jsonb_build_object('id', to_jsonb(x.id)),
               'backfill', to_jsonb(x), x.recorded_at
        FROM "citation" x
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS nora_row_history ON \"citation\"")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history_truncate ON \"citation\"")
    op.execute("DELETE FROM row_history WHERE table_name = 'citation'")

    for table in ("event_revision", "events"):
        op.drop_constraint(f"ck_{table}_field_meta_shape", table, type_="check")
        op.drop_column(table, "field_meta")
    op.drop_column("events", "unsupported_at")

    # An event edge and a Rollup edge have no place in the old vocabulary, and a
    # per-part edge would collide once `part` is gone: they go, the whole-row
    # edges (every one written before this migration) stay.
    op.execute(
        "DELETE FROM citation WHERE citing_store = 'event' OR cited_store = 'rollup' "
        "OR part IS NOT NULL"
    )
    op.drop_constraint("citation_edge_unique", "citation", type_="unique")
    op.drop_constraint("citation_part_is_not_blank_check", "citation", type_="check")
    op.drop_column("citation", "part")
    op.create_unique_constraint(
        "citation_edge_unique", "citation",
        ["citing_store", "citing_key", "cited_store", "cited_id"],
    )
    op.drop_constraint("citation_cited_store_is_known_check", "citation", type_="check")
    op.create_check_constraint(
        "citation_cited_store_is_known_check", "citation", "cited_store in ('message')",
    )
    op.drop_constraint("citation_citing_store_is_known_check", "citation", type_="check")
    op.create_check_constraint(
        "citation_citing_store_is_known_check", "citation",
        "citing_store in ('note', 'standing', 'culture')",
    )
