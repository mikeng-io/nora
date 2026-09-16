"""note_candidate_title_key -- the create-candidate staging key narrows to
include the normalised title (issue #235).

`note_candidate_create_anchor_key` was `(workspace, anchor_date,
anchor_entity_ids)` and the entity list was meant to keep two arrangements on
one day apart. Measured 2026-09-16 on the live deployment: it does not. In
workspace `ridgeline` a candidate named two people, collided with an already
settled same-day row that named the same two, and was dropped before the gate
ever saw it -- 'Voice audio controller architecture' destroyed by 'Nora voice
wake TTS fix'. In a fixed-membership room every arrangement names the same
cast, so the entity list is a constant and the key degenerates to `(workspace,
anchor_date)`. Over the retained window the settled-row branch read
displaced:reobserved = 1:1, so the benign case the wide key exists to serve was
not more common than the loss it caused.

THIS REVERSES A RECORDED REVIEW FINDING, DELIBERATELY. M10 ("a title is not an
identity") is upheld for the NOTE -- `note`'s uniqueness is still
`(workspace, lower(title)) where retired_at is null`, because a note must stay
findable by one name across revisions. It is reversed for the STAGING row,
which is not an identity but a one-shot dedupe slot for one sighting, and whose
title is the only field that separates two arrangements written about on one
day. The cost is a re-described occasion staging twice -- a visible duplicate
(#236) -- in place of a silent destruction.

`lower(btrim(title))` rather than raw `title`, so the key normalises the way
`note`'s own uniqueness does. It is NOT identical to `note_handle`'s
`casefold()` (Postgres `lower` does not fold `ß`->`ss`); a divergence, stated
rather than assumed away, and irrelevant for this corpus.

DOWNGRADE IS LOSSY AND CAN FAIL ON LIVE DATA. Rows that are distinct under the
narrow key can collide under the wide one, so `downgrade()` raises a unique
violation unless the duplicates are resolved first. The same shape
`0049_note_candidate_create` created, restored exactly.

The upgrade is safe on current production data -- all 10 `kind='create'` rows
were distinct under the wide key on 2026-09-16, and distinctness under the wide
key implies it under the narrow one. Verify on any target before applying:

    select workspace, anchor_date, anchor_entity_ids, lower(btrim(title)),
           count(*)
    from note_candidate where kind='create'
    group by 1,2,3,4 having count(*) > 1;   -- must return zero rows

The expression column is hand-written for `idx_messages_conv_ts`'s reason:
autogenerate cannot see index expressions or partial predicates, and
`tests/infra/test_schema_parity.py` -- not `alembic check` -- is what holds this
against `nora/infra/sql/schema.py`.

The revision id is kept SHORT on purpose: `alembic_version.version_num` is
`varchar(32)`, and a longer id fails at the stamp with
`StringDataRightTruncation` -- after the DDL has already run.

Revision ID: 0054_note_candidate_title_key
Revises: 0053_decision_log
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054_note_candidate_title_key"
down_revision = "0053_decision_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "note_candidate_create_anchor_key", table_name="note_candidate",
    )
    op.create_index(
        "note_candidate_create_anchor_key", "note_candidate",
        [
            "workspace", "anchor_date", "anchor_entity_ids",
            sa.text("lower(btrim(title))"),
        ],
        unique=True, postgresql_where=sa.text("kind = 'create'"),
    )


def downgrade() -> None:
    # Lossy -- see the module docstring. Two rows kept apart by their titles
    # collide here, and Postgres refuses rather than choosing one.
    op.drop_index(
        "note_candidate_create_anchor_key", table_name="note_candidate",
    )
    op.create_index(
        "note_candidate_create_anchor_key", "note_candidate",
        ["workspace", "anchor_date", "anchor_entity_ids"],
        unique=True, postgresql_where=sa.text("kind = 'create'"),
    )
