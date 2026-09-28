"""withdrawal -- the citation edge table, `unsupported_at` on the derived
heads that exist, `hidden_at` on messages (design/cognition-loop/10-tiers.md
§2 rule 7, §8, §9; 70-migration.md §2 row 2c, §5, §9; migration step 2c).

WHAT THIS ADDS, all additive:

1. `citation` (new): the edge table 70 §5 lists under step 2 ("the citation
   edge table... filled from existing citations, then written with every
   revision") but that step 2/2b did not build — `0073_lineage` computed the
   `lineage` (disclosure) columns straight from each row's citations without
   persisting the edges themselves, and 2b's event migration built no
   citations at all (events carry no Markdown body yet; that ships with the
   Cognition adaptor's write verbs, step 3). 10 §2 rule 7 is explicit that the
   withdrawal cascade "walks a `citation` edge table; it never parses
   bodies" — so this step builds the table 2c's own gate needs, rather than
   parsing `note_revision`/`standing`/`culture_revision` bodies again at
   cascade time the way `0073`'s one-off backfill did. **Decision, recorded
   in the decision index**: the table is scoped to what a citation can name
   TODAY — a Record message id — because no store yet writes a citation to a
   Rollup or Register key (that is step 3+); a `cited_store` other than
   `'message'` is a later migration's ALTER, not a rename.

   Columns: `citing_store` (`'note'`, `'standing'` or `'culture'` — the three
   stores 10 §7's landing table says exist today with a citing body),
   `citing_key` (the citing row's natural key as text: `note.id`,
   `<platform>:<conversation_id>` for `standing`, `culture.key`),
   `cited_store` (`'message'`, the only kind), `cited_id` (`messages.id` as
   text), `recorded_at`. `UNIQUE (citing_store, citing_key, cited_store,
   cited_id)` makes a repeat citation a no-op (`ON CONFLICT DO NOTHING`) since
   the SAME row re-citing the same message on its next revision is not a new
   edge; the row is append-only otherwise (10 §5's "the `citation` edge table"
   is listed beside the tables that are, and re-citing is the only "revise"
   this edge has — 10 §2 rule 7 marks the CITED row, never the citation).
   Indexed on `(cited_store, cited_id)`, the cascade's own lookup
   ("withdraws one Record item and finds every row citing it").

   BACKFILLED from what already exists, the same extraction `0073_lineage`
   used (`nora.domain.note_body.cited_ids_in`'s regex, written out here per
   `0053_decision_log`'s rule): every `[id, id]` group in the CURRENT
   `note_revision.body` (the one `note.head_revision_id` names) cites its
   message ids; `standing.last_folded_row_id` (the one message id the store
   already tracks per room) is `standing`'s one citation; `culture_revision`
   (the one `culture.head_revision_id` names) cites the newest message on
   record in its room, the same resolution `0073_lineage`'s `_KEY_OF` used to
   find a room's platform, one column further (the row, not just its room).
   A dangling id (a message pruned since, one the regex matched that never
   existed) is recorded anyway: it can never be withdrawn, so it is inert, and
   refusing to write it would need a join this backfill would rather keep
   simple than get subtly wrong under `0073`'s own NOT VALID stance on
   unresolvable citations.

   NOT kept live by a trigger. The three write paths that will keep it
   current going forward — `nora.infra.sql.notes`'s revision write,
   `nora.infra.sql.standing`'s fold write, `nora.infra.sql.culture`'s
   append — insert their own `citation` rows in the SAME transaction as the
   revision they cite from (see each module), following `standing_revision`'s
   own precedent (`0068`'s docstring: "a trigger rather than an append at
   each write site" was chosen for row_history because ~60 scattered upsert
   sites made a miss silent; here there are exactly three sites, each already
   the one place its store computes what it cites, so the trigger's argument
   does not carry).

2. `unsupported_at` (bigint epoch ms, nullable): on `note`, `standing`,
   `culture` — the three HEADS `citation.citing_store` names, the ones a
   prompt block or a read tool actually renders. Set by the withdrawal
   cascade (`nora.app.withdrawal`), never by a model. All three tables are
   already in `nora.infra.sql.history.HISTORY_TABLES`
   (`0068_system_time_history`) with `nora_row_history` attached, so an
   `UPDATE ... SET unsupported_at = ...` is captured as a new version with NO
   migration change here: "the withdrawal cascade marks the rows
   `unsupported`, also a transition" (10 §8) holds for free. Rendering an
   unsupported row is a defect in the READER, guarded by
   `tests/app/test_withdrawal.py`'s negative cases, not by a column
   constraint — there is nothing a CHECK could enforce about what a Python
   caller chooses to SELECT.

3. `hidden_at` (bigint epoch ms, nullable): on `messages`. Set by
   `nora person forget <id>` (soft; `nora person unforget <id>` clears it).
   Same reasoning: `messages` is already in `HISTORY_TABLES`, so hiding is a
   captured transition and a snapshot before the forget restores the row
   (10 §9, "everything stays in history"). `nora.person forget` is scoped to
   what exists today per 70 §9's own list — messages, the Record row a
   platform account writes — and NOT social items, templates, reference
   photos or perceptions, none of which have a table yet (steps 5-6); the
   decision row this migration's DEC amends says so.

WHY NOT THE FULL 70 §9 SURFACE. "Hides the rows authored by or about the
person: their messages and social items, their templates and reference
photos, perceptions naming them, and their own Character and Standing rows"
is 70 §9's target state once every store it names exists. At step 2c only
`messages` (Record) and unit-scoped `standing`/`culture` (never "about a
person": both are a ROOM's or the unit's account, not a person's — Character
by person arrives at step 8) exist, so `forget_person` hides messages and
lets the cascade below mark whatever cites them `unsupported`; there is no
person-scoped Character or Standing row to hide because that store does not
exist yet.

>>> RE-POINTED AT MERGE. <<< `down_revision` is the head of master when this
branch was cut (`0074_event_revision`). `uv run alembic heads` must print
exactly ONE line (AGENTS.md, "Never assume your migration's parent is
unclaimed").

Revision ID: 0075_withdrawal
Revises: 0074_event_revision
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0075_withdrawal"
down_revision = "0074_event_revision"
branch_labels = None
depends_on = None

#: design 42 §4's citation, `nora.domain.note_body._CITATION` written out
#: (`0053_decision_log`'s rule; `0073_lineage` wrote the same constant out).
_CITATION = r"\[\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*\]"

_CITING_STORES = ("note", "standing", "culture")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(
        'LOCK TABLE "note", "standing", "culture", "culture_revision", '
        '"note_revision", "messages" IN SHARE ROW EXCLUSIVE MODE'
    )

    # 1. the citation edge table
    op.create_table(
        "citation",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("citing_store", sa.Text, nullable=False),
        sa.Column("citing_key", sa.Text, nullable=False),
        sa.Column("cited_store", sa.Text, nullable=False, server_default="message"),
        sa.Column("cited_id", sa.Text, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            f"citing_store in ({_in(_CITING_STORES)})",
            name="citation_citing_store_is_known_check",
        ),
        sa.CheckConstraint(
            "cited_store in ('message')", name="citation_cited_store_is_known_check",
        ),
        sa.CheckConstraint(
            "length(btrim(citing_key)) > 0", name="citation_has_a_citing_key_check",
        ),
        sa.CheckConstraint(
            "length(btrim(cited_id)) > 0", name="citation_has_a_cited_id_check",
        ),
    )
    op.create_unique_constraint(
        "citation_edge_unique", "citation",
        ["citing_store", "citing_key", "cited_store", "cited_id"],
    )
    op.create_index(
        "idx_citation_cited", "citation", ["cited_store", "cited_id"],
    )

    # 2. unsupported_at on the three heads
    for table in ("note", "standing", "culture"):
        op.add_column(table, sa.Column("unsupported_at", sa.BigInteger, nullable=True))

    # 3. hidden_at on messages
    op.add_column("messages", sa.Column("hidden_at", sa.BigInteger, nullable=True))

    # 3a. row_history catch-up. `ADD COLUMN` fires no row trigger, and
    # `nora_row_history` (`0068_system_time_history`) suppresses an UPDATE
    # that changes no VALUE — the property that makes an upsert of unchanged
    # data a no-op version, `0068`'s own comment — so a note/standing/culture
    # row whose latest captured version predates this migration would have
    # NO version whose image carries `unsupported_at` until its next real
    # write. `tests/infra/test_row_history.py::
    # test_the_backfill_dates_what_each_row_proves` pins "the head the chain
    # ends on IS the live row" for exactly this reason: a schema-only column
    # is still part of what Nora holds NOW, so the current head must say so.
    # This closes each table's currently-open version and opens an
    # equivalent one whose image reflects the post-`ADD COLUMN` row —
    # manually doing what the trigger would have done had a value actually
    # changed, using the SAME row-key shape `0068`'s trigger builds
    # (`jsonb_build_object(<key column>, to_jsonb(<value>))`).
    for table, key_cols in (
        ("note", ("id",)), ("culture", ("key",)), ("messages", ("id",)),
    ):
        key_json = ", ".join(f"'{c}', to_jsonb(n.{c})" for c in key_cols)
        op.execute(f"""
            WITH new_versions AS (
                SELECT nextval('row_history_id_seq') AS version_id,
                       jsonb_build_object({key_json}) AS row_key,
                       to_jsonb(n) AS image
                FROM "{table}" n
            ),
            closed AS (
                UPDATE row_history rh
                SET superseded_at = clock_timestamp(), superseded_by = nv.version_id
                FROM new_versions nv
                WHERE rh.table_name = '{table}' AND rh.row_key = nv.row_key
                    AND rh.superseded_at IS NULL
                RETURNING rh.superseded_by
            )
            INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
            SELECT nv.version_id, '{table}', nv.row_key, 'update', nv.image, clock_timestamp()
            FROM new_versions nv
            -- The JOIN is load-bearing, not decorative: an UNREFERENCED
            -- data-modifying CTE is never planned at all in Postgres, so
            -- without it `closed` never runs and the INSERT below collides
            -- with the version it should have superseded first.
            JOIN closed c ON c.superseded_by = nv.version_id
        """)  # noqa: S608 — table/key names are module constants, never input
    op.execute("""
        WITH new_versions AS (
            SELECT nextval('row_history_id_seq') AS version_id,
                   jsonb_build_object(
                       'platform', to_jsonb(s.platform),
                       'conversation_id', to_jsonb(s.conversation_id)
                   ) AS row_key,
                   to_jsonb(s) AS image
            FROM "standing" s
        ),
        closed AS (
            UPDATE row_history rh
            SET superseded_at = clock_timestamp(), superseded_by = nv.version_id
            FROM new_versions nv
            WHERE rh.table_name = 'standing' AND rh.row_key = nv.row_key
                AND rh.superseded_at IS NULL
            RETURNING rh.superseded_by
        )
        INSERT INTO row_history (id, table_name, row_key, op, image, recorded_at)
        SELECT nv.version_id, 'standing', nv.row_key, 'update', nv.image, clock_timestamp()
        FROM new_versions nv
        JOIN closed c ON c.superseded_by = nv.version_id
    """)

    # 4. the backfill: note citations, from the head revision's body
    op.execute(f"""
        INSERT INTO citation (citing_store, citing_key, cited_store, cited_id)
        SELECT DISTINCT 'note', n.id::text, 'message', btrim(x)::text
        FROM note n
        JOIN note_revision r ON r.id = n.head_revision_id
        CROSS JOIN LATERAL regexp_matches(r.body, '{_CITATION}', 'g') AS m(g)
        CROSS JOIN LATERAL unnest(string_to_array(m.g[1], ',')) AS x
        WHERE length(btrim(x)) BETWEEN 1 AND 18
        ON CONFLICT DO NOTHING
    """)  # noqa: S608 — a module constant, never input

    # 5. standing's one citation: the row-id watermark
    op.execute("""
        INSERT INTO citation (citing_store, citing_key, cited_store, cited_id)
        SELECT DISTINCT 'standing', s.platform || ':' || s.conversation_id,
               'message', s.last_folded_row_id::text
        FROM standing s
        WHERE s.last_folded_row_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)

    # 6. culture's one citation: the newest message on record in its room
    # (culture_key is a bare conversation id, `0073_lineage`'s own note)
    op.execute("""
        INSERT INTO citation (citing_store, citing_key, cited_store, cited_id)
        SELECT DISTINCT 'culture', c.key, 'message', newest.id::text
        FROM culture c
        CROSS JOIN LATERAL (
            SELECT m.id FROM messages m
            WHERE m.conversation_id = c.key
            ORDER BY m.id DESC LIMIT 1
        ) AS newest
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_column("messages", "hidden_at")
    for table in ("note", "standing", "culture"):
        op.drop_column(table, "unsupported_at")
    op.drop_index("idx_citation_cited", table_name="citation")
    op.drop_constraint("citation_edge_unique", "citation", type_="unique")
    op.drop_table("citation")
