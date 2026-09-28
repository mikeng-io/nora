"""lineage -- the columns every derived row carries, their backfill, and the
Record's trigram index (design/cognition-loop/10-tiers.md §3, 15-map.md §4,
70-migration.md §2 row 2 and §5; migration step 2, the lineage half).

WHAT THIS ADDS, all additive:

1. On the derived stores that exist today (`note`, `note_revision`,
   `standing`, `standing_revision`, `culture_revision`), the columns 10 §3
   lists:

   - `reasoning`: text, NULL or one of 10 §4's six modes;
   - `confidence`: text, NULL or low/medium/high;
   - `lineage`: jsonb, section -> list of `{kind, subject, origin}`;
   - `exclude_persons`: text[];
   - `as_of`: bigint epoch ms, when its newest evidence was observed.

   `culture` (the head) gains none: it holds only a pointer to its revision,
   and every reader joins through it. `reasoning` and `confidence` stay NULL
   on every existing row and on every row today's writers produce: no write
   verb states a mode or a confidence yet (step 3's verbs do), and NULL is
   "not stated", never a guess. `valid_from`/`valid_to` are not here: no
   writer of these stores knows them; events (2b) are where they start.

2. `idx_messages_body_trgm`, a GIN trigram index over `messages.body`, for
   `record_query`'s lexical search (15 §4). `pg_trgm` is already installed
   (`0026_note_tier`); it is the operator design/dreamer-agent-loop/
   24-embedding-seam.md §2 measured on Cantonese (a term inside a sentence
   found; a CJK run is one `simple` tsvector token, so full-text search would
   not find it). The index serves `ilike` substring search and pg_trgm's
   `<%` word-similarity operator alike.

THE BACKFILL (70 §5: "computed from each existing row's citations; a row whose
citations cannot be resolved gets origin-room-only lineage"). The rule is
written out here rather than imported (a migration is a frozen historical
statement, `0053_decision_log`); `nora.infra.sql.notes`, `.standing` and
`.culture` apply the same rule to every new row.

- `note_revision.lineage = {"row": [...]}`: `message:<room>` for every room a
  cited message (`[<messages.id>, ...]` in the body, design 42 §4) was said
  in; `message:<established_in>` on a note's FIRST revision, which was
  written in the room the note was raised in; and `unresolved` at the
  note's origin room when a cited id is no longer in `messages` (pruned), or
  when a later revision cites nothing (a legacy body: the room it was written
  from was never recorded). `as_of` is the newest cited message's `ts`,
  else the revision's `written_at`.
- `note.lineage` is the union of its revisions', `as_of` the newest of theirs.
- `standing`, `standing_revision`: `body` and `working_set` each get
  `message:<conversation_id>` and `unresolved` at it. The fold reads its own
  room, and also the prompt it was shown, whose unit-wide `<notes>` it may
  restate and does not cite, so the rest of what it rests on is not
  resolvable. `as_of` is the `ts` of the message `last_folded_row_id` names
  (the newest message the fold read), else `updated_at`.
- `culture_revision`: `body` gets `message:<culture_key>` and `unresolved`
  at it, for the same reason (the culture fold is shown the unit's note
  index). `as_of` is `written_at`.

So every existing standing and culture row renders only in its own room,
which is the only room either is read in today, and every note renders where
the rooms it cites allow, which is unchanged for the room it was raised in.

HISTORY (step 1b, DEC-232). The backfill UPDATEs of `note` and `standing`
fire `nora_row_history()` like any other write, so each head's history holds
a version dated at this migration with its lineage: a restore before it reads
the old image, whose missing columns take their defaults (`'{}'`, every
section unknown, so origin-room-only). The revision tables are append-only
and this backfill is the one UPDATE they take, of columns that did not exist;
their lineage is derived from their own bodies, so it holds for them at any
T. Every table written here is LOCKed first, for 0068's reason: the presence
collector and the analytics process keep writing while `nora` restarts.

>>> RE-POINTED AT MERGE. <<< Cut on `0069_cognition_signal` as `0070_lineage`;
`0070_stimulus_rename` and then step 2a's `0071_rollup_builds` merged first,
so this is re-parented onto the latter and renumbered 0072.
`uv run alembic heads` must print exactly ONE line (AGENTS.md, "Never assume
your migration's parent is unclaimed").

Revision ID: 0072_lineage
Revises: 0071_rollup_builds
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0072_lineage"
down_revision = "0071_rollup_builds"
branch_labels = None
depends_on = None

#: The stores that gain the columns. Frozen here.
_TABLES: tuple[str, ...] = (
    "note", "note_revision", "standing", "standing_revision", "culture_revision",
)

#: 10 §4 and 10 §3, frozen (`nora.domain.lineage` is the live copy).
_MODES = ("computed", "told", "deduced", "induced", "abduced", "said")
_CONFIDENCES = ("low", "medium", "high")

#: design 42 §4's citation, `nora.domain.note_body._CITATION` written out.
_CITATION = r"\[\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*\]"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _message(room: str) -> str:
    return f"jsonb_build_object('kind', 'message', 'subject', {room}, 'origin', {room})"


def _unresolved(room: str) -> str:
    return f"jsonb_build_object('kind', 'unresolved', 'subject', NULL, 'origin', {room})"


def upgrade() -> None:
    tables = ", ".join(f'"{t}"' for t in (*_TABLES, "culture", "messages"))
    op.execute(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE")

    # 1. the columns
    for table in _TABLES:
        op.add_column(table, sa.Column("reasoning", sa.Text, nullable=True))
        op.add_column(table, sa.Column("confidence", sa.Text, nullable=True))
        op.add_column(table, sa.Column(
            "lineage", pg.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ))
        op.add_column(table, sa.Column(
            "exclude_persons", pg.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'"),
        ))
        op.add_column(table, sa.Column("as_of", sa.BigInteger, nullable=True))
        op.create_check_constraint(
            f"{table}_reasoning_is_a_mode_check", table,
            f"reasoning is null or reasoning in ({_in(_MODES)})",
        )
        op.create_check_constraint(
            f"{table}_confidence_is_a_level_check", table,
            f"confidence is null or confidence in ({_in(_CONFIDENCES)})",
        )
        op.create_check_constraint(
            f"{table}_lineage_is_an_object_check", table,
            "jsonb_typeof(lineage) = 'object'",
        )

    # 2. the backfill: note revisions from their citations
    op.execute(f"""
        WITH cited AS (
            SELECT r.id AS rid, btrim(x)::bigint AS mid
            FROM note_revision r
            CROSS JOIN LATERAL regexp_matches(r.body, '{_CITATION}', 'g') AS m(g)
            CROSS JOIN LATERAL unnest(string_to_array(m.g[1], ',')) AS x
            WHERE length(btrim(x)) BETWEEN 1 AND 18
        ),
        resolved AS (
            SELECT c.rid, msg.conversation_id, msg.ts
            FROM cited c LEFT JOIN messages msg ON msg.id = c.mid
        ),
        per_revision AS (
            SELECT
                r.id,
                n.established_in AS origin,
                r.parent_revision_id IS NULL AS first,
                r.written_at,
                coalesce((
                    SELECT jsonb_agg(DISTINCT {_message('x.conversation_id')})
                    FROM resolved x
                    WHERE x.rid = r.id AND x.conversation_id IS NOT NULL
                ), '[]'::jsonb) AS rooms,
                EXISTS (
                    SELECT 1 FROM resolved x
                    WHERE x.rid = r.id AND x.conversation_id IS NULL
                ) AS missing,
                EXISTS (SELECT 1 FROM resolved x WHERE x.rid = r.id) AS cites,
                (SELECT max(x.ts) FROM resolved x WHERE x.rid = r.id) AS newest
            FROM note_revision r
            JOIN note n ON n.id = r.note_id
        )
        UPDATE note_revision r
        SET lineage = jsonb_build_object('row',
                p.rooms
                || CASE WHEN p.first
                        THEN jsonb_build_array({_message('p.origin')})
                        ELSE '[]'::jsonb END
                || CASE WHEN p.missing OR (NOT p.cites AND NOT p.first)
                        THEN jsonb_build_array({_unresolved('p.origin')})
                        ELSE '[]'::jsonb END),
            as_of = coalesce(p.newest, p.written_at)
        FROM per_revision p
        WHERE p.id = r.id
    """)  # noqa: S608 — module constants, never input
    # 3. a note is the union of its revisions
    op.execute("""
        UPDATE note n
        SET lineage = jsonb_build_object('row', u.entries),
            as_of = u.newest
        FROM (
            SELECT r.note_id,
                   coalesce(jsonb_agg(DISTINCT e), '[]'::jsonb) AS entries,
                   max(r.as_of) AS newest
            FROM note_revision r
            CROSS JOIN LATERAL jsonb_array_elements(r.lineage -> 'row') AS e
            GROUP BY r.note_id
        ) u
        WHERE u.note_id = n.id
    """)
    # 4. standing and its chain: own room, plus what it was shown and did not cite
    for table in ("standing", "standing_revision"):
        entries = (
            f"jsonb_build_array({_message('s.conversation_id')}, "
            f"{_unresolved('s.conversation_id')})"
        )
        op.execute(f"""
            UPDATE {table} s
            SET lineage = jsonb_build_object('body', {entries}, 'working_set', {entries}),
                as_of = coalesce(
                    (SELECT m.ts FROM messages m WHERE m.id = s.last_folded_row_id),
                    s.updated_at
                )
        """)  # noqa: S608 — table names are module constants, never input
    # 5. culture's chain
    entries = (
        f"jsonb_build_array({_message('c.culture_key')}, {_unresolved('c.culture_key')})"
    )
    op.execute(f"""
        UPDATE culture_revision c
        SET lineage = jsonb_build_object('body', {entries}),
            as_of = c.written_at
    """)  # noqa: S608 — a module constant, never input

    # 6. the Record's lexical index
    op.create_index(
        "idx_messages_body_trgm", "messages", ["body"],
        postgresql_using="gin", postgresql_ops={"body": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("idx_messages_body_trgm", table_name="messages")
    for table in _TABLES:
        for check in ("reasoning_is_a_mode", "confidence_is_a_level", "lineage_is_an_object"):
            op.drop_constraint(f"{table}_{check}_check", table, type_="check")
        for column in ("as_of", "exclude_persons", "lineage", "confidence", "reasoning"):
            op.drop_column(table, column)
