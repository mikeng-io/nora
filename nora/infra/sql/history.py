"""System time: which table keeps its history how, and the as-of-T read.

design/cognition-loop/10-tiers.md §8: every store Nora writes is append-only
in system time, so she can be restored as of any T. Step 1b
(70-migration.md §2) builds that for every table 70 §5 lists as changing in
place today, except the rows it marks for steps 2 and 2b. This module is the
one place that says, for every table in the schema, which of four things it
is — `tests/infra/test_row_history.py` fails when a table is in none of them
or when the migrated database's triggers disagree with `HISTORY_TABLES`:

- **`HISTORY_TABLES`** — changes in place (UPDATE, upsert, DELETE). The live
  table stays exactly as it was, so every reader and writer is unchanged; the
  trigger `nora_row_history()` (migration `0068_system_time_history`) appends
  each version to `row_history` in the same transaction and closes the one it
  replaced. A DELETE appends a `delete` version holding the removed row: it
  is hidden (gone from the head, so from every read) and kept. Chosen over
  rewriting each table as versioned rows with a head view because that means
  changing ~60 write statements and every read's WHERE, and a history that
  misses one write site is silently incomplete; the trigger records every
  path by construction. The heads that stay tables are the compare-and-swap
  cache §8 allows ("a cache derivable from the chain, and no as-of read uses
  it").
- **`APPEND_ONLY`** — never changed after insert, so the row IS its own
  history; each names the column holding its system time (`recorded_at`,
  added by `0068`, or a pre-existing insert-time column).
- **`DEFERRED`** — changes in place but migrates at a later step, named: a
  restore cannot hold them as of T until then, and says so.
- **`NOT_RESTORED`** — not state a restore needs: operator and trace logs no
  turn reads, deprecated tiers (tables kept, code gone), and the history
  and revision tables themselves (the snapshot copies those on their own
  terms).

`as_of` is the read: the version of each row held at T.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.schema import ROW_HISTORY

__all__ = [
    "APPEND_ONLY",
    "CAPTURE_SETTING",
    "DEFERRED",
    "HISTORY_TABLES",
    "NOT_RESTORED",
    "append_only_as_of",
    "as_of",
]

#: The session setting the trigger reads; `off` makes it record nothing. Set
#: ONLY by `nora.infra.sql.snapshot_at.seed`, with `SET LOCAL`, while it copies
#: a source's heads and history into a sandbox — recording that copy as new
#: writes would date every restored row at the moment of the copy.
CAPTURE_SETTING = "nora.row_history"

#: Tables that change in place, and the columns that identify a row (the
#: trigger's arguments). 70 §5's inventory minus its rows marked 2 or 2b,
#: plus `sticker_index` (an in-place cache the inventory missed) and
#: `message_edit` (deleted by cascade when a message is pruned).
HISTORY_TABLES: dict[str, tuple[str, ...]] = {
    "messages": ("id",),
    # Written append-only, but `message_edit.message_id` is ON DELETE CASCADE:
    # a prune deletes a message's edits with no store asking, and only a row
    # trigger (which fires on a cascaded delete) keeps them. Kept over
    # dropping the cascade, which would make every prune of an edited message
    # fail on the foreign key instead.
    "message_edit": ("id",),
    "note_candidate": ("id",),
    "note": ("id",),
    "culture": ("key",),
    "standing": ("platform", "conversation_id"),
    "reminders": ("id",),
    "compute_jobs": ("id",),
    "instagram_summons": ("id",),
    "version_announcements": ("version", "platform", "conversation_id"),
    "participation_window_state": ("platform", "conversation_id"),
    "dream_schedule_state": ("adaptor_name", "scope_id"),
    "consolidation_watermark": ("consolidator_name", "scope_id"),
    "analytics_watermark": ("deriver_name", "subject_id"),
    "tag_debounce_state": ("platform", "conversation_id"),
    "media_objects": ("sha256",),
    "video_readings": (
        "sha256", "provider", "model", "chunk_seconds", "chunk_max", "reading_digest",
    ),
    "presence_fixes": ("id",),
    "music_queue": ("id",),
    "music_session": ("guild_id",),
    "geocode_cache": ("lat_rounded", "lng_rounded"),
    "sticker_index": ("platform", "fetch_token"),
    # Step 1 (0069): written append-only, but ON DELETE CASCADE from
    # `messages`, `message_edit`'s case exactly.
    "message_reaction": ("id",),
    "message_delete": ("id",),
    # Step 2 (0071): the Rollup as versioned builds. A build's rows are
    # written by its fold and changed in place by later runs of the same
    # build (a relink, a place's label, an occupancy bin, a unit rollup
    # recomputed and diffed); a superseded build is never touched again. The
    # trigger keeps each version, so the Rollup as of T is the build
    # `rollup_build` says was current at T, with its rows as they were at T.
    "presence_places": ("circle_id", "place_id"),
    "presence_stays": ("id",),
    "presence_trips": ("id",),
    "presence_derived_places": ("id",),
    "presence_occupancy": ("id",),
    "presence_place_rhythm": ("subject_id", "place_id"),
    "presence_corridors": ("id",),
    "rollup_build": ("id",),
    "rollup_fold_state": ("build_id", "subject_id"),
    "rollup_colocation": ("id",),
    "rollup_outing": ("id",),
    "rollup_daybook": ("id",),
    "rollup_tie": ("id",),
    "rollup_response": ("id",),
    # Step 2b (0074_event_revision): the head is still the compare-and-swap
    # cache 10-tiers.md §8 allows over `event_revision` (APPEND_ONLY, below).
    "events": ("id",),
}

#: Append-only tables a restore copies, and each one's system-time column.
#: `tool_call_log.created_at` is `now()` at insert and was always system
#: time; the others gained `recorded_at` in `0068`.
APPEND_ONLY: dict[str, str] = {
    "note_revision": "recorded_at",
    "standing_revision": "recorded_at",
    "culture_revision": "recorded_at",
    "decision_log": "recorded_at",
    "note_merge_decision": "recorded_at",
    "tool_call_log": "created_at",
    # design/sandbox-runtime §12 (0080_computer_events): the computer audit,
    # two rows (started, finished) per computer tool call, never changed.
    "computer_events": "created_at",
    "revision_blob": "recorded_at",
    "doctrine_revision": "recorded_at",
    "config_revision": "recorded_at",
    "deploy_revision": "recorded_at",
    # Step 1 (0069, renamed by 0070/DEC-234): the cognition loop's stimuli,
    # its wake log (a unit's watermark is its newest row, never a row
    # rewritten) and source health.
    "stimulus": "recorded_at",
    "cognition_wake": "recorded_at",
    "source_health": "recorded_at",
    # Step 2b (0074_event_revision): the chain `events` is a
    # compare-and-swap cache over, and RSVPs, append-only since this step
    # ("RSVP transition rows", 70 §5) — a repeat RSVP is an INSERT, never an
    # upsert; the current response is the newest row per (event_id, person_id).
    "event_revision": "recorded_at",
    "event_rsvps": "recorded_at",
    # Step 2c (0075_withdrawal): the citation edge table. Written once per
    # edge (`ON CONFLICT DO NOTHING`, `nora.app.withdrawal` §2c's migration
    # docstring) and never updated or deleted — an edge does not change, the
    # ROW IT CITES is marked `unsupported` instead — so it is its own
    # history, same as `event_rsvps`.
    "citation": "recorded_at",
    # Step 3 (0077_assess_report): the Assess adaptor's one store. One row
    # per run, never rewritten — the next run is the next row
    # (`nora.domain.assess.AssessReport`).
    "assess_report": "recorded_at",
    # Step 3a (0078_cognition_session): the Cognition adaptor's session and
    # its dry-run write verbs. All three written once, never revised — a
    # session is a replay input, a close and a would-write are each their
    # own row.
    "cognition_session": "recorded_at",
    "cognition_session_close": "recorded_at",
    "cognition_would_write": "recorded_at",
    # Step 3a-ii (70-migration.md §2 row 3a-ii): the pace verbs
    # (`wake_at`/`wake_move`/`wake_cancel`) and the mirrored, held `remind`
    # family. Each transition is its own row, never revised.
    "cognition_self_wake": "recorded_at",
    "cognition_remind_mirror": "recorded_at",
    # Step 3a-ii, the wake lifecycle: how a tracked wake ended (only `closed`
    # advances the watermark), what it returned to the next agenda, and the
    # `self` stimuli taken back.
    "cognition_wake_outcome": "recorded_at",
    "cognition_carry": "recorded_at",
    "stimulus_retraction": "recorded_at",
}

#: Changes in place today and migrates at a later step (70 §5). Not
#: restorable as of T before then; `nora snapshot` leaves them empty and
#: names them. Empty since step 2b (`0074_event_revision`) migrated the last
#: entries `events`/`event_rsvps` marked "2b"; kept as a named group rather
#: than removed, because a later step of this migration may add to it again.
DEFERRED: dict[str, str] = {}

#: Not copied into a restore, each with why.
NOT_RESTORED: dict[str, str] = {
    "row_history": "the chain itself; the snapshot copies it up to T on its own terms",
    "tag_trace": "a per-turn trace record; no turn reads it",
    "turn_verdict": "the verdict's table, its code deleted 2026-09-24",
    "reasoning_strip_log": "an operator log; no turn reads it",
    "artifacts": "generated-file metadata served by URL; not something she reasons over",
    "language_observation": "deprecated tier (2026-09-07); table kept, code gone",
    "room_lexeme": "deprecated tier (2026-09-07); table kept, code gone",
    "exchange_observation": "deprecated tier (2026-09-07); table kept, code gone",
    "room_exchange": "deprecated tier (2026-09-07); table kept, code gone",
    "room_portrait": "deprecated tier (2026-09-07); table kept, code gone",
    "fact_entity": "deprecated tier (2026-09-07); table kept, code gone",
    "fact_candidate": "deprecated tier (2026-09-07); table kept, code gone",
    "fact": "deprecated tier (2026-09-07); table kept, code gone",
    "fact_premise": "deprecated tier (2026-09-07); table kept, code gone",
    "fact_asserter": "deprecated tier (2026-09-07); table kept, code gone",
    "embedding": "deprecated tier (2026-09-07); table kept, code gone",
    "rendered_claim": "deprecated tier (2026-09-07); table kept, code gone",
    "note_entity": "deprecated chain (2026-09-08); nothing writes it",
    "presence_corridor_speed": (
        "retired writer (design/27 §4.4 M2): nothing writes or reads it; table kept"
    ),
}


def _history_as_of(table: str, at: datetime) -> sa.Select[Any]:
    """The versions of `table` open at `at`, oldest first."""
    return (
        sa.select(ROW_HISTORY.c.image)
        .where(
            ROW_HISTORY.c.table_name == table,
            ROW_HISTORY.c.recorded_at <= at,
            sa.or_(
                ROW_HISTORY.c.superseded_at.is_(None),
                ROW_HISTORY.c.superseded_at > at,
            ),
            # A `delete` version is the row being hidden: open at T means
            # hidden at T, so it is not in the head.
            ROW_HISTORY.c.op != "delete",
        )
        .order_by(ROW_HISTORY.c.recorded_at.asc(), ROW_HISTORY.c.id.asc())
    )


async def as_of(
    conn: AsyncConnection, table: str, at: datetime, *, batch: int = 1000,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Every row `table` held at `at`, as the dicts `to_jsonb` rendered, in
    batches. Streamed: `messages` and `presence_fixes` are the deployment's
    two largest tables and a restore holds all of either.

    Inclusive at `at`: a version recorded exactly then was held then.
    """
    if table not in HISTORY_TABLES:
        raise ValueError(f"{table!r} keeps no row_history; see nora.infra.sql.history")
    result = await conn.stream(_history_as_of(table, at))
    async for part in result.partitions(batch):
        yield [row.image for row in part]


async def append_only_as_of(
    conn: AsyncConnection, table: str, at: datetime, *, batch: int = 1000,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Every row of an append-only `table` recorded at or before `at`."""
    column = APPEND_ONLY.get(table)
    if column is None:
        raise ValueError(f"{table!r} is not append-only; see nora.infra.sql.history")
    stmt = sa.text(
        f'SELECT to_jsonb(t) AS image FROM "{table}" t '  # noqa: S608 — table and column names are module constants, never input
        f'WHERE t."{column}" <= :at ORDER BY t."{column}"'
    ).columns(image=pg.JSONB).bindparams(at=at)
    result = await conn.stream(stmt)
    async for part in result.partitions(batch):
        yield [row.image for row in part]
