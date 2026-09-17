"""reasoning_strip_log -- the transport dropping a turn's reasoning becomes a
row instead of a WARNING inside a third-party logger.

**THE DEFECT.** OmniRoute's Chat Completions transport cannot carry
`reasoningContent` in a multi-turn position. When an agent loop goes multi-turn
-- which on this path means it reached for a tool -- Strands'
`OpenAIModel._format_regular_messages` DROPS every `reasoningContent` block out
of the request, logs one WARNING, and continues. Read back off production on
2026-09-17: it fired twice in roughly two hours of uptime, both immediately
after a tool returned, and it did NOT raise. The loop ran on to the next
completion and the turn reached TTS. She spoke, with her reasoning discarded,
and nothing anywhere counted it (#325; measured as a SAFETY BREACH on the
relevance gate by a different caller of the same transport, #186 / ADR 0011).

**WHY POSTGRES AND NOT A COUNTER.** This is the exact shape of event a
Prometheus counter cannot hold. `nora` restarts two or three times a day and
prometheus-client creates label values lazily, so a low-frequency series reads
EMPTY forever and an absent series means "nothing since the last restart",
never "never happened". Two occurrences in two hours, on a path that after the
2026-09-17 config change produces roughly one run per adaptor per DAY, is
precisely that. A counter here would report zero across a deploy and be
believed. The counter still exists (`nora_reasoning_strip_total`, with its
denominator in the same series so an absence cannot pass for a zero), and it is
for the live window only; the answer that outlives a deploy is here. ADR 0013.

**THE ROW IS AN OPPORTUNITY, NOT AN EVENT.** `continuation_calls` is the
denominator -- model calls made with a prior assistant turn already in the
message list, the only calls the transport can strip. `strip_calls` is how many
of them it stripped. A raw count of strips answers nothing: 2 of 2 tool-using
turns and 2 of 200 are different worlds, and the production reading that opened
#325 could not tell which it was. Rows are written ONLY for invocations with at
least one continuation call, which is what keeps this a census of the calls at
risk rather than a log of every model call in the system.

**WHAT MAKES THE NEXT CHANGE LEGIBLE.** `model` and `reasoning_effort` are read
off the model provider at the moment of the call, never from config -- the row
records what was SENT. Between 08:52Z and 09:42Z on 2026-09-17 the OmniRoute
combo behind the alias changed upstream (`deepseek-v4-flash` ->
`deepseek-v4.1-flash`), and in the same session the operator moved
`reasoning_effort` `low -> none` on the tag path and `none -> low` on every
Dreamer adaptor. Whether the combo change CAUSED the drop is untested: the
before-window contained zero comparable events, because the only pre-switch
tool calls were Dreamer-side at `none` and could never have warned. Two columns
beside the ratio turn the next such question into a GROUP BY.

`reasoning_effort` is why zero is not the same as healthy. At `none` a path
emits no reasoning at all and CANNOT be stripped, so `strip_calls = 0` there
means impossible rather than fine -- and the tag path, which produced both
production hits, is at `none` as of 2026-09-17 while the Dreamer, which just
acquired nine read verbs, is at `low`. Without this column those two readings
are the same number.

**`tag_id` IS NULLABLE and the NULL is the answer**, not a gap: a Dreamer run
has no tag. Absence is representable per row, as `participation_window_state.
last_send_at` is one table over, rather than defaulted to something that reads
as a real value.

**NO BACKFILL, AND NONE IS POSSIBLE.** The event was never recorded anywhere
but a WARNING line inside `strands.models.openai`, and the container's own log
window opens at the last recreate.

**NO SWEEP.** Rows land only on multi-turn invocations. Pruning would be a
retention judgement rather than arithmetic -- unlike `tag_debounce_state`,
whose rows provably cannot change an answer once their interval has elapsed --
and the question this table exists to answer spans more than one deploy, which
is the whole reason it is not a counter.

Revision ID: 0057_reasoning_strip_log
Revises: 0056_speech_trigger_state
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0057_reasoning_strip_log"
down_revision = "0056_speech_trigger_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reasoning_strip_log",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        # "tag" / "participation" / "dreamer:<adaptor>". Text rather than an
        # enum: half the vocabulary is the adaptor registry, and an enum would
        # need a migration every time an adaptor lands and be wrong in between.
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("reasoning_effort", sa.Text, nullable=False),
        # NULL means «this path has no tag» -- a Dreamer run. See the docstring.
        sa.Column("tag_id", sa.Text),
        sa.Column("model_calls", sa.Integer, nullable=False),
        sa.Column("continuation_calls", sa.Integer, nullable=False),
        sa.Column("strip_calls", sa.Integer, nullable=False),
        sa.Column("stripped_blocks", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        # Refused at the database rather than trusted from the writer. The
        # interesting clause is `strip_calls <= continuation_calls`: a row that
        # violated it would mean the reasoning came from the PREFILL rather
        # than from the loop, which is a shape no correct reading of this table
        # can explain and must not be stored quietly.
        sa.CheckConstraint(
            "continuation_calls >= 1 "
            "AND continuation_calls <= model_calls "
            "AND strip_calls <= continuation_calls "
            "AND stripped_blocks >= 0 "
            "AND ((strip_calls = 0) = (stripped_blocks = 0))",
            name="ck_reasoning_strip_log_counts",
        ),
    )
    # The read is «the rate on one path over one window», so the index leads
    # with `path` and not with the timestamp.
    op.create_index(
        "idx_reasoning_strip_log_path_ts",
        "reasoning_strip_log",
        ["path", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_reasoning_strip_log_path_ts", table_name="reasoning_strip_log")
    op.drop_table("reasoning_strip_log")
