"""System time: which table keeps its history how, and the as-of-T read.

Every store Nora writes is append-only in system time, so she can be restored
as of any T. This module is the one place that says, for every table in the
schema, which of four things it
is — `tests/infra/test_row_history.py` fails when a table is in none of them
or when the migrated database's triggers disagree with `HISTORY_TABLES`:

- **`HISTORY_TABLES`** — changes in place (UPDATE, upsert, DELETE). The live
  table stays exactly as it was, so every reader and writer is unchanged; the
  trigger `nora_row_history()` (migration `0068_system_time_history`) appends
  each version to `row_history` in the same transaction and closes the one it
  replaced. A DELETE appends a `delete` version holding the removed row: it
  is hidden (gone from the head, so from every read) and kept. A trigger
  rather than versioned rows with a head view: the latter means changing
  every write statement and every read's WHERE, and a history that misses one
  write site is silently incomplete; the trigger records every path by
  construction. The heads that stay tables are a compare-and-swap cache
  derivable from the chain, and no as-of read uses it.
- **`APPEND_ONLY`** — never changed after insert, so the row IS its own
  history; each names the column holding its system time (`recorded_at`,
  added by `0068`, or a pre-existing insert-time column).
- **`DEFERRED`** — changes in place but is not yet migrated, named: a
  restore cannot hold them as of T until then, and says so.
- **`NOT_RESTORED`** — not state a restore needs: operator and trace logs no
  turn reads, deprecated tiers (tables kept, code gone), and the history
  and revision tables themselves (the snapshot copies those on their own
  terms), and the memory index, which is rebuilt from the restored stores.
  A table nothing restores serves no as-of read, so it may change in place
  without a history.

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
#: trigger's arguments). Includes `sticker_index` (an in-place cache) and
#: `message_edit` (deleted by cascade when a message is pruned).
HISTORY_TABLES: dict[str, tuple[str, ...]] = {
    "message_reading": ("id",),
    "message_reading_queue": ("id",),
    "message_reading_claim": ("id",),
    "message_reading_attempt": ("id",),
    "messages": ("id",),
    # Written append-only, but `message_edit.message_id` is ON DELETE CASCADE:
    # a prune deletes a message's edits with no store asking, and only a row
    # trigger (which fires on a cascaded delete) keeps them. Dropping the
    # cascade would make every prune of an edited message fail on the foreign
    # key instead.
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
    # Written append-only, but ON DELETE CASCADE from `messages`:
    # `message_edit`'s case exactly (0069).
    "message_reaction": ("id",),
    "message_delete": ("id",),
    # 0071: the Rollup as versioned builds. A build's rows are
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
    # 0074_event_revision: the head is the compare-and-swap cache over
    # `event_revision` (APPEND_ONLY, below).
    "events": ("id",),
    # 0082_computer_sessions: a session is
    # continued and expired in place; its versions are what it was at T.
    "computer_sessions": ("id",),
    # 0117_persona: an account's head, its `revision` bumped by every write.
    "persona": ("key",),
}

#: Append-only tables a restore copies, and each one's system-time column.
#: `tool_call_log.created_at` is `now()` at insert and is system time; the
#: others gained `recorded_at` in `0068`.
APPEND_ONLY: dict[str, str] = {
    "note_revision": "recorded_at",
    # ADR 0020 (0084_documents): a document's identity and one snapshot per revision,
    # neither ever updated; the current document is the highest revision.
    "documents": "recorded_at",
    "document_revision": "recorded_at",
    # 0087_document_state: a document's lifecycle, one row per change; the current
    # state is the latest row, so archive, delete and restore never update anything.
    "document_state": "recorded_at",
    # 0097_otp: a one-time code and the attempts against it; spending, failing and
    # superseding a code are all rows, so nothing is updated.
    "otp_challenge": "recorded_at",
    "otp_attempt": "recorded_at",
    "standing_revision": "recorded_at",
    "culture_revision": "recorded_at",
    # 0100_lessons: a lesson is an identity, and every change to it a revision.
    "lesson": "recorded_at",
    "lesson_revision": "recorded_at",
    # 0117_persona: a Persona line; a change is a new row that supersedes it.
    "persona_line": "recorded_at",
    # 0121_persona_dispute: a dispute of a line; what it DID to the line is a
    # new persona_line row, so a dispute row itself never changes.
    "persona_dispute": "recorded_at",
    # 0101_self_tension: a refused lesson and the law it pulled against.
    "self_tension": "recorded_at",
    # 0103_memos: a memo is an identity, and every change to it (a revision, a
    # withdrawal) a revision.
    "memo": "recorded_at",
    "memo_revision": "recorded_at",
    # 0107_social_stream: an item a member's network showed, every probe and read
    # of it, and a frame gone or hidden; a change to an item is a state row.
    "social_item": "recorded_at",
    "social_poll": "recorded_at",
    "social_item_state": "recorded_at",
    # 0108_social_perception: each attempt to perceive an item's media.
    "social_perception": "recorded_at",
    # 0119_social_item_count: a post's or reel's counts, a row where they moved.
    "social_item_count": "recorded_at",
    # 0109_blob_tombstone: a story frame's bytes deleted at their bound.
    "blob_tombstone": "recorded_at",
    "decision_log": "recorded_at",
    "note_merge_decision": "recorded_at",
    "tool_call_log": "created_at",
    # 0081_computer_events: the computer audit,
    # two rows (started, finished) per computer tool call, never changed.
    "computer_events": "created_at",
    # 0083_computer_exports: every file that left
    # the computer, one row per export, never changed; a revision is a row.
    "computer_exports": "created_at",
    "revision_blob": "recorded_at",
    "doctrine_revision": "recorded_at",
    "config_revision": "recorded_at",
    "deploy_revision": "recorded_at",
    # 0069 (DEC-234): the cognition loop's stimuli,
    # its wake log (a unit's watermark is its newest row, never a row
    # rewritten) and source health.
    "stimulus": "recorded_at",
    "cognition_wake": "recorded_at",
    "source_health": "recorded_at",
    # 0074_event_revision: the chain `events` is a compare-and-swap cache
    # over, and RSVPs, append-only — a repeat RSVP is an INSERT, never an
    # upsert; the current response is the newest row per (event_id, person_id).
    "event_revision": "recorded_at",
    "event_rsvps": "recorded_at",
    # 0077_assess_report: the Assess adaptor's one store. One row
    # per run, never rewritten — the next run is the next row
    # (`nora.domain.assess.AssessReport`).
    "assess_report": "recorded_at",
    # The Cognition session and its closes (0078): written once, never revised —
    # a session is a replay input, a close is its own row.
    "cognition_session": "recorded_at",
    "cognition_session_close": "recorded_at",
    # The pace verbs (`wake_at`/`wake_move`/`wake_cancel`): each transition is
    # its own row, never revised.
    "cognition_self_wake": "recorded_at",
    # The wake lifecycle: how a tracked wake ended (only `closed`
    # advances the watermark), what it returned to the next agenda, and the
    # `self` stimuli taken back.
    "cognition_wake_outcome": "recorded_at",
    "cognition_carry": "recorded_at",
    "stimulus_retraction": "recorded_at",
}

#: Changes in place and not yet migrated. Not restorable as of T; `nora
#: snapshot` leaves them empty and names them. Currently empty; kept as a
#: named group so a table can be deferred again.
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
    "rendered_claim": "deprecated tier (2026-09-07); table kept, code gone",
    "note_entity": "deprecated chain (2026-09-08); nothing writes it",
    "presence_corridor_speed": (
        "retired writer (design/27 §4.4 M2): nothing writes or reads it; table kept"
    ),
    # 0105_memory_index: a projection of the stores above, so a restore leaves
    # it empty and `memory_index_state` reads `never` until a rebuild.
    "memory_node": "derived; nora memory rebuild",
    "memory_edge": "derived; nora memory rebuild",
    "memory_projection_queue": "derived; nora memory rebuild",
    "memory_index_state": "derived; nora memory rebuild",
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
