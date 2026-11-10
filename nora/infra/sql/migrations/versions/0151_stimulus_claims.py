"""stimulus_claim + stimulus_pending: consumption as append-only facts.

A stimulus used to be consumed by the watermark's RANGE — `id <= through`
and pending since no later than `through_ms`, except the ids the wake listed
as `unclaimed`. Losslessness then depended on every pending id under a mark
being NAMED somewhere: `view.over` enumerated the backlog the bounded read
hid, and under an advanced watermark that enumeration is exactly the
unbounded read the shape was meant to prevent.

`stimulus_claim` makes the claim itself the fact: `record_wake` writes one
row per `claimed_ids` stimulus — bounded by what the view showed — and the
pending read probes it (`NOT EXISTS` a claim by a wake of the same
`(unit, mode)` that did not fail). `stimulus_pending` materializes the set
that probe cannot visit cheaply: the stimuli NOT permanently consumed —
appended rows minus `closed` claims and retractions, per `(unit, mode)`. The
read walks only the set, so its work is the genuine backlog; consumed
history under a mark is never visited.

The claim backfill replays the OLD rule once: for every `(unit, mode)`
newest valid mark (same `~failed` predicate as `_mark_query`), the stimuli
its range consumed — `id <= through` and pending since at most `at_ms`,
minus its stored `unclaimed` — are claimed under that wake, so nothing the
old representation ate resurfaces as pending. "Pending since" is the EXACT
pre-`0150` bound — a dated row's own `due_ms <= at_ms`, an undated row's
`recorded_at <= at_ms` — not the tick floor (`at_ms // 60000`): a wake at
95 000 ms consumed a row due at 90 000 ms whose tick is 2 > 95 000 // 60 000
= 1. `unclaimed` names are parsed from `detail` in either written form
(object, or a jsonb string holding it).

The pending set is then seeded per `(unit, mode)`: every mode each unit has
waked in, plus `live` for every unit holding stimuli, gets a `stimulus_id =
0` sentinel (the lazily-seeded marker `view` checks) and every live stimulus
not closed-claimed in that mode.
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
            server_default=sa.func.clock_timestamp(), nullable=False,
        ),
    )
    op.create_index("idx_stimulus_claim_stimulus", "stimulus_claim", ["stimulus_id"])
    op.create_index("idx_stimulus_claim_wake", "stimulus_claim", ["wake_id"])
    op.create_table(
        "stimulus_pending",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("stimulus_id", sa.BigInteger, nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(), nullable=False,
        ),
    )
    op.create_index(
        "uq_stimulus_pending", "stimulus_pending",
        ["unit", "mode", "stimulus_id"], unique=True,
    )

    conn = op.get_bind()
    stale = _at(int(datetime.now(UTC).timestamp() * 1000) - _STALE_MS)
    # Each (unit, mode)'s newest VALID mark — `_mark_query`'s `~_failed_wake`
    # spelled out: no outcome that is not `closed`, and not an open tracked
    # wake older than the session timeout (EXPIRED).
    marks = conn.execute(sa.text(
        "SELECT w.id, w.unit, w.through_stimulus_id, w.at_ms, w.detail "
        "FROM cognition_wake w WHERE w.id = ("
        "  SELECT max(w2.id) FROM cognition_wake w2"
        "  WHERE w2.unit = w.unit AND w2.mode = w.mode"
        "    AND NOT EXISTS (SELECT 1 FROM cognition_wake_outcome o"
        "                    WHERE o.wake_id = w2.id AND o.outcome <> 'closed')"
        "    AND NOT (w2.tracked = 1 AND w2.recorded_at < :stale"
        "             AND NOT EXISTS (SELECT 1 FROM cognition_wake_outcome o"
        "                             WHERE o.wake_id = w2.id)))"
    ), {"stale": stale})
    for wake_id, unit, through, at_ms, detail in marks:
        conn.execute(sa.text(
            "INSERT INTO stimulus_claim (unit, stimulus_id, wake_id)"
            " SELECT :unit, s.id, :wake_id FROM stimulus s"
            " WHERE s.unit = :unit AND s.id <= :through"
            "   AND NOT (s.id = ANY(:unclaimed))"
            "   AND ((s.due_ms IS NULL AND s.recorded_at <= :at)"
            "        OR s.due_ms <= :at_ms)"
        ), {
            "unit": unit, "wake_id": wake_id, "through": through,
            "unclaimed": list(_unclaimed(detail)),
            # The OLD rule's exact bounds: a dated row's own `due_ms`, an
            # undated row's `recorded_at` — both against `at_ms` itself.
            "at": _at(at_ms), "at_ms": at_ms,
        })
    # Seed each (unit, mode)'s pending set: the modes the unit waked in plus
    # `live` for any unit holding stimuli; live rows not CONSUMED in that
    # mode — claimed by a `closed` wake, or by an untracked one (already the
    # watermark, never releasable), in either case not failed. The
    # `stimulus_id = 0` sentinel marks the seed.
    conn.execute(sa.text(
        "INSERT INTO stimulus_pending (unit, mode, stimulus_id)"
        " SELECT s.unit, m.mode, s.id FROM stimulus s"
        " JOIN (SELECT DISTINCT unit, mode FROM cognition_wake"
        "       UNION SELECT DISTINCT unit, 'live' FROM stimulus) m"
        "   ON m.unit = s.unit"
        " WHERE NOT EXISTS (SELECT 1 FROM stimulus_retraction r"
        "                   WHERE r.stimulus_id = s.id)"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM stimulus_claim c"
        "     JOIN cognition_wake w ON w.id = c.wake_id"
        "     WHERE c.stimulus_id = s.id AND w.unit = s.unit AND w.mode = m.mode"
        "       AND (w.tracked = 0 OR EXISTS ("
        "         SELECT 1 FROM cognition_wake_outcome o"
        "         WHERE o.wake_id = w.id AND o.outcome = 'closed'))"
        "       AND NOT EXISTS ("
        "         SELECT 1 FROM cognition_wake_outcome o2"
        "         WHERE o2.wake_id = w.id AND o2.outcome <> 'closed'))"
        " ON CONFLICT (unit, mode, stimulus_id) DO NOTHING"
    ))
    conn.execute(sa.text(
        "INSERT INTO stimulus_pending (unit, mode, stimulus_id)"
        " SELECT unit, mode, 0 FROM ("
        "  SELECT DISTINCT unit, mode FROM cognition_wake"
        "  UNION SELECT DISTINCT unit, 'live' FROM stimulus) m"
        " ON CONFLICT (unit, mode, stimulus_id) DO NOTHING"
    ))


def downgrade() -> None:
    op.drop_index("uq_stimulus_pending", table_name="stimulus_pending")
    op.drop_table("stimulus_pending")
    op.drop_index("idx_stimulus_claim_wake", table_name="stimulus_claim")
    op.drop_index("idx_stimulus_claim_stimulus", table_name="stimulus_claim")
    op.drop_table("stimulus_claim")
