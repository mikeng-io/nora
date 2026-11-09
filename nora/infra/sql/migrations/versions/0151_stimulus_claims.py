"""stimulus_claim: consumption as an append-only fact, not a range rule.

A stimulus used to be consumed by the watermark's RANGE — `id <= through`
and pending since no later than `through_ms`, except the ids the wake listed
as `unclaimed`. Losslessness then depended on every pending id under a mark
being NAMED somewhere: `view.over` enumerated the backlog the bounded read
hid, and under an advanced watermark that enumeration is exactly the
unbounded read the shape was meant to prevent.

This table makes the claim itself the fact: `record_wake` writes one row per
`claimed_ids` stimulus — bounded by what the view showed — and the pending
read probes it (`NOT EXISTS` a claim by a wake that did not fail). Rows a
wake's range passed over stay pending without ever being enumerated; each
wake also records `detail.pending_floor`, the lowest id under its `through`
that can still be pending, so the below-mark residual is a bounded cursor
range rather than history.

The backfill replays the OLD rule once: for every `(unit, mode)` newest
valid mark (same `~failed` predicate as `_mark_query`), the stimuli its
range consumed — `id <= through` and pending since at most `through_ms`,
minus its stored `unclaimed` — are claimed under that wake, so nothing the
old representation ate resurfaces as pending. `unclaimed` names are parsed
from `detail` in either written form (object, or a jsonb string holding it).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0151_stimulus_claims"
down_revision = "0150_due_ticks"
branch_labels = None
depends_on = None

#: `wake_state`'s expiry horizon (`WakePolicy.session_timeout_seconds`,
#: hardcoded — migrations carry their own constants, never imports that can
#: drift): a TRACKED wake older than this with no outcome row is EXPIRED.
_STALE_MS = 900_000


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _unclaimed(detail: object) -> tuple[int, ...]:
    while isinstance(detail, str):
        detail = json.loads(detail)
    ids = (detail or {}).get("unclaimed") if isinstance(detail, dict) else None
    while isinstance(ids, str):
        ids = json.loads(ids)
    return tuple(int(i) for i in ids or ())


def upgrade() -> None:
    op.create_table(
        "stimulus_claim",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("stimulus_id", sa.BigInteger, nullable=False),
        sa.Column("wake_id", sa.BigInteger, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("idx_stimulus_claim_stimulus", "stimulus_claim", ["stimulus_id"])
    op.create_index("idx_stimulus_claim_wake", "stimulus_claim", ["wake_id"])

    conn = op.get_bind()
    stale = _at(int(datetime.now(UTC).timestamp() * 1000) - _STALE_MS)
    # Each (unit, mode)'s newest VALID mark — `_mark_query`'s `~_failed_wake`
    # spelled out: no outcome that is not `closed`, and not an open tracked
    # wake older than the session timeout (EXPIRED).
    marks = conn.execute(sa.text(
        "SELECT w.id, w.unit, w.mode, w.through_stimulus_id, w.at_ms, w.detail "
        "FROM cognition_wake w WHERE w.id = ("
        "  SELECT max(w2.id) FROM cognition_wake w2"
        "  WHERE w2.unit = w.unit AND w2.mode = w.mode"
        "    AND NOT EXISTS (SELECT 1 FROM cognition_wake_outcome o"
        "                    WHERE o.wake_id = w2.id AND o.outcome <> 'closed')"
        "    AND NOT (w2.tracked = 1 AND w2.recorded_at < :stale"
        "             AND NOT EXISTS (SELECT 1 FROM cognition_wake_outcome o"
        "                             WHERE o.wake_id = w2.id)))"
    ), {"stale": stale})
    for wake_id, unit, mode, through, at_ms, detail in marks:
        conn.execute(sa.text(
            "INSERT INTO stimulus_claim (unit, stimulus_id, wake_id)"
            " SELECT :unit, s.id, :wake_id FROM stimulus s"
            " WHERE s.unit = :unit AND s.id <= :through"
            "   AND NOT (s.id = ANY(:unclaimed))"
            "   AND ((s.due_ms IS NULL AND s.recorded_at <= :at)"
            "        OR s.due_tick <= :tick)"
        ), {
            "unit": unit, "wake_id": wake_id, "through": through,
            "unclaimed": list(_unclaimed(detail)),
            # The old rule consumed what was eligible BY `at_ms`: a dated row
            # is pending from `due_tick * 60000`, so the bound is the FLOOR
            # tick — `due_tick <= at_ms // 60000`, not the ceiling.
            "at": _at(at_ms), "tick": at_ms // 60_000,
        })


def downgrade() -> None:
    op.drop_index("idx_stimulus_claim_wake", table_name="stimulus_claim")
    op.drop_index("idx_stimulus_claim_stimulus", table_name="stimulus_claim")
    op.drop_table("stimulus_claim")
