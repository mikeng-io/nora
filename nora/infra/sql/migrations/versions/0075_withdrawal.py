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
    # No table lock: cheap `ADD COLUMN`/`CREATE TABLE` DDL only takes its own
    # brief ACCESS EXCLUSIVE lock, and the citation backfill below is a plain
    # read + `INSERT ... ON CONFLICT DO NOTHING` that tolerates a concurrent
    # writer — a message inserted mid-migration just gets no citation edge
    # backfilled for it, no different from the dangling-id case the backfill
    # already accepts as inert. A `SHARE ROW EXCLUSIVE` lock on `messages`
    # here would block live ingestion for the length of the migration; that
    # was needed only by the manual `row_history` catch-up this migration
    # used to run (removed — see below), not by anything left in it now.

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

    # No row_history catch-up. `ADD COLUMN` fires no row trigger, so a
    # note/standing/culture/messages row whose latest captured version
    # predates this migration has no version whose image carries
    # `unsupported_at`/`hidden_at` until its next real write — that version
    # is missing the key, not zero. The read side (`nora.app.snapshot_at`,
    # `nora.infra.sql.snapshot`) treats a column ABSENT from an older
    # version's JSON image as that column's default (NULL for both), which
    # is exactly what the column was for every row at the instant this
    # migration ran; the live row itself (read straight from the table, not
    # a history image) always carries the true current value because
    # `ADD COLUMN ... nullable=True` backfills NULL in place. A manual
    # rewrite of every existing row under `SHARE ROW EXCLUSIVE` — this
    # migration's own previous shape — bought nothing this doesn't already
    # give for free, at the cost of blocking live ingestion for as long as
    # the three tables took to rewrite; see `tests/infra/test_snapshot_at.py`
    # for the guard that an old version missing the key restores as NULL.

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
