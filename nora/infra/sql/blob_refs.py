"""Which content addresses a row still names: what a blob deletion asks first.

A blob is keyed by the hash of its bytes, so the same bytes are one blob
however many rows name them: a story frame a member also sent to a room, an
image the computer audit recorded, a post that repeats a frame. Bytes may go
only when nothing names them but the rows the caller is letting go.

`BLOB_KEY_COLUMNS` is every column that names a key, and `held_elsewhere`
reads each of them. `tests/infra/test_blob_refs.py` fails on a schema column
that looks like a key, or is JSON (which can hold a key under any name), and
is in neither it nor `NOT_BLOB_KEYS`, so a new store that names blobs is read
here, or says why not.

A `media_objects` row is the bytes' own index entry, not a reference, unless
another source wrote it first: one row per address, first source wins, so a
row a room, a tool or a generation wrote says that path holds the bytes too.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.media import SOCIAL_MEDIA_SOURCE
from nora.infra.sql.schema import (
    BLOB_TOMBSTONE,
    COMPUTE_JOBS,
    COMPUTER_EVENTS,
    COMPUTER_EXPORTS,
    DOCUMENT_REVISION,
    MEDIA_OBJECTS,
    MESSAGES,
    REMINDERS,
    SOCIAL_ITEM,
    STICKER_INDEX,
)

__all__ = ["BLOB_KEY_COLUMNS", "NOT_BLOB_KEYS", "held_elsewhere"]

#: Every `(table, column)` that names a blob key, as `held_elsewhere` reads it.
#: A JSON column is matched as text, so a key under any field name counts.
BLOB_KEY_COLUMNS: frozenset[tuple[str, str]] = frozenset({
    ("messages", "media_json"),
    ("sticker_index", "sha256"),
    ("computer_events", "request"),
    ("computer_events", "result"),
    ("computer_events", "output_ref"),
    ("computer_exports", "sha256"),
    ("computer_exports", "blob_key"),
    ("document_revision", "media"),
    ("media_objects", "sha256"),
    ("social_item", "media_ref"),
    # A job or a reminder still to run may name the media it acts on.
    ("compute_jobs", "payload"),
    ("reminders", "payload"),
})

_LINEAGE = "a presence lineage: which presence rows a derivation rests on"
_TRACE = "a log of what a turn or session was shown; nothing reads bytes through it"

#: Columns that look like a key, or are JSON that could hold one under
#: another name, and name no blob that must stay, and why.
NOT_BLOB_KEYS: Mapping[tuple[str, str], str] = {
    ("message_reading", "snapshot"): "frozen source text; message rows own blob custody",
    ("message_reading_claim", "content"): "statement slots and source ids, never blob custody",
    ("persona_line", "content"): "prose Character slots, never blob custody",
    ("persona_line", "strength"): "code-computed occasion roots and timestamps, never blob custody",
    ("persona_line", "fact_value"): (
        "typed identity labels, dates, lists and person references; never blob custody"
    ),
    ("revision_blob", "sha256"): "the hash of a text body stored in the row itself",
    ("document_revision", "sha256"): "the hash of the revision's own text",
    ("document_revision", "spec"): "a document's layout; its pictures are `media`",
    ("video_readings", "sha256"): "a reading cached by the bytes it read; it needs no bytes",
    ("memory_node", "sha256"): "the index, derived from the rows above and rebuilt from them",
    ("memory_node", "lineage"): _LINEAGE,
    ("blob_tombstone", "sha256"): "bytes already deleted",
    ("media_objects", "provenance"): (
        "a row's birth record; reference images a generation named are not "
        "needed to show what it made"
    ),
    ("row_history", "image"): (
        "past versions of rows: a restore to before a deletion is incomplete "
        "for those bytes by design, and the tombstone says so"
    ),
    ("row_history", "row_key"): "a history row's key, never a blob",
    ("stimulus", "ref"): "a stimulus's source row (`social_read:<read id>`), never a blob",
    ("tool_call_log", "tool_input"): _TRACE,
    ("tool_call_log", "tool_result"): _TRACE,
    ("tag_trace", "messages"): _TRACE,
    ("tag_trace", "transcript"): _TRACE,
    ("cognition_session", "situation"): _TRACE,
    ("cognition_session", "agenda"): _TRACE,
    ("cognition_session", "skill_versions"): "the versions of the skills a session loaded",
    ("cognition_self_wake", "subject_rows"): _TRACE,
    ("cognition_wake", "detail"): "why a wake fired or was braked",
    ("compute_jobs", "result_document_ids"): "documents a job wrote, by id",
    ("compute_jobs", "checkpoint"): "the compute backend's own resume state",
    ("computer_sessions", "stopped_at_detail"): "why a computer session stopped",
    ("doctrine_revision", "files"): "doctrine file paths to their `revision_blob` hashes",
    ("assess_report", "results"): "an assessment's scores",
    ("event_revision", "field_meta"): "who set each typed field of an event",
    ("events", "field_meta"): "who set each typed field of an event",
    ("presence_corridors", "sequence"): "places in order",
    ("presence_trips", "sequence"): "places in order",
    ("rollup_build", "detail"): "a build's counts",
    ("rollup_daybook", "stays"): "a day's stays",
    ("rollup_daybook", "rooms"): "a day's rooms",
    ("rollup_fold_state", "open_stay"): "a stay still open",
    ("room_exchange", "examples"): "quoted exchanges, text only",
    ("room_lexeme", "examples"): "quoted usages, text only",
    ("standing", "working_set"): "a position's working notes, text only",
    ("standing_revision", "working_set"): "a position's working notes, text only",
    **{
        (table, "lineage"): _LINEAGE
        for table in (
            "culture_revision", "event_revision", "events", "note", "note_revision",
            "presence_corridors", "presence_derived_places", "presence_occupancy",
            "presence_place_rhythm", "presence_stays", "presence_trips",
            "rollup_colocation", "rollup_daybook", "rollup_outing", "rollup_response",
            "rollup_tie", "standing", "standing_revision",
        )
    },
}


async def held_elsewhere(
    conn: AsyncConnection, shas: Iterable[str], *,
    letting_go: Iterable[tuple[str, str]] = (),
) -> set[str]:
    """The keys among `shas` that some row still names, other than the social
    items `letting_go` (`(network, item_id)` pairs) whose bytes the caller is
    deleting. A key absent from the answer is named by nothing else."""
    wanted = sorted(set(shas))
    if not wanted:
        return set()
    keys = (
        sa.func.unnest(sa.literal(wanted, type_=pg.ARRAY(sa.Text)))
        .table_valued("k").render_derived()
    )
    k = keys.c.k
    ev, ex, i = COMPUTER_EVENTS, COMPUTER_EXPORTS, SOCIAL_ITEM

    def text(column: sa.ColumnElement[object]) -> sa.ColumnElement[str]:
        return sa.cast(column, sa.Text)

    # ponytail: `media_json`, `media_ref` and the audit's JSON are matched as
    # text, a scan per key; the deletion pass asks for a few keys a day. An
    # index of keys if that grows.
    # A story frame whose bytes were deleted after it was recorded no longer
    # holds them: bytes stored again since belong to the rows written since.
    deleted_since = sa.exists().where(
        BLOB_TOMBSTONE.c.sha256 == k, BLOB_TOMBSTONE.c.deleted_at > i.c.recorded_at,
    )
    others = sa.exists().where(
        i.c.media_ref.contains(k), ~sa.and_(i.c.kind == "story_frame", deleted_since),
    )
    gone = list(letting_go)
    if gone:
        others = others.where(sa.tuple_(i.c.network, i.c.item_id).not_in(gone))
    held = sa.or_(
        sa.exists().where(MESSAGES.c.media_json.contains(k)),
        sa.exists().where(STICKER_INDEX.c.sha256 == k),
        sa.exists().where(sa.or_(
            text(ev.c.request).contains(k), text(ev.c.result).contains(k),
            text(ev.c.output_ref).contains(k),
        )),
        sa.exists().where(text(COMPUTE_JOBS.c.payload).contains(k)),
        sa.exists().where(text(REMINDERS.c.payload).contains(k)),
        sa.exists().where(sa.or_(ex.c.sha256 == k, ex.c.blob_key == k)),
        sa.exists().where(k == sa.any_(DOCUMENT_REVISION.c.media)),
        sa.exists().where(
            MEDIA_OBJECTS.c.sha256 == k, MEDIA_OBJECTS.c.source != SOCIAL_MEDIA_SOURCE,
        ),
        others,
    )
    rows = await conn.execute(sa.select(k).select_from(keys).where(held))
    return set(rows.scalars())
