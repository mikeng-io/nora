"""Step 1b's first gate (design/cognition-loop/70-migration.md §2): write,
revise and retire across every store migrated here, snapshot at two moments,
and get EXACTLY what was held at each.

WHAT IS HELD is read from the live tables at each moment (`to_jsonb` of
every row) — the thing the restore claims to reproduce, observed directly,
never through `row_history`. The restore is then built after the source has
moved on, into a sandbox database of its own (`sandbox_as_of`, the harness's
per-case database), and read back the same way. Equality is of whole row
sets, append-only tables included, so a row too many, a row too few or one
column carrying a later value all fail.

Per in-place table, six rows keyed 0..5 walk every transition:

- 0 written before t1 and never touched (the parent append-only rows cite);
- 1 written before t1, revised between t1 and t2, retired after t2;
- 2 written before t1, retired between t1 and t2;
- 3 written, revised and retired all before t1;
- 4 written between t1 and t2, revised after t2;
- 5 written after t2.

Append-only tables get one row in each of the three stretches.

And one case no store asks for: an edit of message 2, which is pruned
between t1 and t2. `message_edit.message_id` is ON DELETE CASCADE, so the
prune deletes the edit with no statement naming it; the edit must be held at
t1, gone at t2, and kept in history as hidden.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.infra.sql.history import APPEND_ONLY, HISTORY_TABLES
from nora.infra.sql.snapshot_at import copied_tables
from nora.subject import sandbox_as_of

T0 = datetime(2026, 9, 1, tzinfo=UTC)
MS0 = int(T0.timestamp() * 1000)

#: The edit of message 2 that the prune cascades away.
PRUNED_EDIT = 3100


def _uuid(table: str, i: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{i}"))


def _hex(table: str, i: int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{table}:{i}").hex


#: A valid row per in-place table, for key `i`.
ROWS: dict[str, Callable[[int], dict[str, Any]]] = {
    "messages": lambda i: {
        "id": 1000 + i, "platform": "signal", "conversation_id": "g1",
        "conversation_type": "group", "sender_id": f"s{i}", "sender_name": f"n{i}",
        "ts": MS0 + i, "body": f"message {i}", "is_nora": 0,
    },
    "message_edit": lambda i: {
        "id": 3000 + i, "message_id": 1000, "ts": MS0 + i, "body": f"edit {i}",
    },
    "note_candidate": lambda i: {
        "id": _uuid("nc", i), "unit": "u", "kind": "miss", "probe": f"p{i}",
        "first_seen_at": MS0, "last_seen_at": MS0, "occurrence_count": 1,
    },
    "note": lambda i: {
        "id": _uuid("note", i), "unit": "u", "title": f"note {i}", "description": "d",
        "uncertainty": "open", "closing_condition": "c", "anchor_date": 20260901,
        "established_in": "signal:g1", "created_at": MS0 + i,
        "head_revision_id": _uuid("head", i),
    },
    "culture": lambda i: {
        "key": f"room{i}", "head_revision_id": _uuid("cr", 100 + i), "updated_at": MS0,
    },
    "standing": lambda i: {
        "platform": "signal", "conversation_id": f"g{i}", "body": f"position {i}",
        "updated_at": MS0,
    },
    "reminders": lambda i: {
        "id": _hex("rem", i), "requester_person_id": "p", "platform": "signal",
        "conversation_id": "g1", "conversation_type": "group", "text": f"r{i}",
        "created_at": T0, "due_at": T0 + timedelta(hours=1), "state": "pending",
    },
    "compute_jobs": lambda i: {
        "id": _uuid("job", i), "backend_job_id": f"b{i}", "task": "t", "platform": "signal",
        "conversation_id": "g1", "conversation_type": "group", "requester_person_id": "p",
        "created_at": T0, "state": "pending",
    },
    "instagram_summons": lambda i: {
        "id": _uuid("ig", i), "message_id": f"m{i}", "media_id": "md", "shortcode": "sc",
        "author_igid": "a", "state": "claimed", "mode": "dry_run",
    },
    "version_announcements": lambda i: {
        "version": f"V{i}", "platform": "signal", "conversation_id": "g1",
        "announced_at": T0, "state": "announcing",
    },
    "participation_window_state": lambda i: {
        "platform": "signal", "conversation_id": f"g{i}", "last_window_end_at": MS0,
    },
    "dream_schedule_state": lambda i: {
        "adaptor_name": "a", "scope_id": f"s{i}", "last_fired_at": MS0,
    },
    "consolidation_watermark": lambda i: {
        "consolidator_name": "c", "scope_id": f"s{i}", "last_message_id": i,
    },
    "analytics_watermark": lambda i: {
        "deriver_name": "d", "subject_id": f"s{i}", "last_fix_id": i,
    },
    "tag_debounce_state": lambda i: {
        "platform": "signal", "conversation_id": f"g{i}", "last_allowed_at": MS0,
    },
    "media_objects": lambda i: {
        "sha256": f"{i:064x}", "source": "inbound", "description": f"shows {i}",
        "created_at_ms": MS0,
    },
    "video_readings": lambda i: {
        "sha256": f"{i:064x}", "provider": "p", "model": "m", "chunk_seconds": 10,
        "chunk_max": 3, "reading_digest": "d", "reading": f"reading {i}",
    },
    "presence_fixes": lambda i: {
        "id": 2000 + i, "circle_id": "c", "person_id": "p", "ts": MS0 + i, "lat": 22.25,
        "lng": 114.125, "wire_state": "ok", "recorded_at": MS0 + i,
    },
    "music_queue": lambda i: {
        "id": _hex("mq", i), "guild_id": "g", "position": i, "source": "youtube",
        "url": f"u{i}", "title": f"t{i}", "added_by_person_id": "p", "added_at": T0,
    },
    "music_session": lambda i: {
        "guild_id": f"g{i}", "voice_channel_id": "v", "notify_channel_id": "n",
        "paused": False, "volume": 1.0, "updated_at": T0,
    },
    "geocode_cache": lambda i: {
        "lat_rounded": 22.0 + i / 8, "lng_rounded": 114.0, "address": f"street {i}",
    },
    "sticker_index": lambda i: {
        "platform": "signal", "fetch_token": f"t{i}", "sha256": f"{i:064x}",
    },
}

#: The column each revision moves, and to what.
REVISE: dict[str, tuple[str, Callable[[int], Any]]] = {
    "messages": ("delivery_status", lambda i: f"delivered-{i}"),
    "message_edit": ("body", lambda i: f"re-edited {i}"),
    "note_candidate": ("occurrence_count", lambda i: 10 + i),
    "note": ("strikes", lambda i: 1 + i),
    "culture": ("updated_at", lambda i: MS0 + 1000 + i),
    "standing": ("body", lambda i: f"revised position {i}"),
    "reminders": ("state", lambda i: "cancelled"),
    "compute_jobs": ("state", lambda i: "firing"),
    "instagram_summons": ("state", lambda i: "published"),
    "version_announcements": ("state", lambda i: "done"),
    "participation_window_state": ("last_window_end_at", lambda i: MS0 + 5 + i),
    "dream_schedule_state": ("last_fired_at", lambda i: MS0 + 5 + i),
    "consolidation_watermark": ("last_message_id", lambda i: 100 + i),
    "analytics_watermark": ("last_fix_id", lambda i: 100 + i),
    "tag_debounce_state": ("last_allowed_at", lambda i: MS0 + 5 + i),
    "media_objects": ("description", lambda i: f"now shows {i}"),
    "video_readings": ("reading", lambda i: f"re-read {i}"),
    "presence_fixes": ("address", lambda i: f"near {i}"),
    "music_queue": ("position", lambda i: 50 + i),
    "music_session": ("paused", lambda i: True),
    "geocode_cache": ("address", lambda i: f"renamed {i}"),
    "sticker_index": ("hits", lambda i: 7 + i),
}

#: One append-only row, number `n`, citing the stable key-0 parents.
APPEND: dict[str, Callable[[int], dict[str, Any]]] = {
    "note_revision": lambda n: {
        "id": _uuid("nr", n), "note_id": _uuid("note", 0), "body": f"body {n}",
        "written_at": MS0 + n, "established_by": "test",
    },
    "standing_revision": lambda n: {
        "platform": "signal", "conversation_id": "g0", "body": f"pos {n}",
        "fold_count": n, "updated_at": MS0 + n,
    },
    "culture_revision": lambda n: {
        "id": _uuid("cr", n), "culture_key": "room0", "body": f"account {n}",
        "written_at": MS0 + n, "established_by": "test",
    },
    "decision_log": lambda n: {
        "id": f"d{n}", "writer": "tag", "platform": "signal", "conversation_id": "g1",
        "verdict": "replied", "ts": MS0 + n,
    },
    "note_merge_decision": lambda n: {
        "id": _uuid("nm", n), "unit": "u", "mode": "shadow", "outcome": "would_merge",
        "survivor_id": _uuid("note", 0), "duplicate_id": _uuid("note", 0),
        "survivor_title": "a", "duplicate_title": "b", "established_by": "t", "ts": MS0 + n,
    },
    "tool_call_log": lambda n: {
        "id": _uuid("tc", n), "conversation_id": "g1", "conversation_type": "group",
        "tag_id": f"t{n}", "tool_name": "now", "tool_input": {}, "tool_result": {"n": n},
    },
    "revision_blob": lambda n: {"sha256": f"{n:064x}", "content": f"text {n}"},
    "doctrine_revision": lambda n: {
        "corpus": "doctrine", "content_sha256": f"{n:064x}", "files": {}, "loaded_at": T0,
    },
    "config_revision": lambda n: {
        "file": "policy.toml", "path": "/p", "content_sha256": f"{n:064x}", "loaded_at": T0,
    },
    "deploy_revision": lambda n: {"started_at": T0, "manifest_status": "absent"},
}


def test_the_fixture_covers_every_restorable_table() -> None:
    assert set(ROWS) == set(REVISE) == set(HISTORY_TABLES)
    assert set(APPEND) == set(APPEND_ONLY)


def _table(name: str) -> sa.Table:
    from nora.infra.sql.schema import METADATA
    return METADATA.tables[name]


def _key(table: str, row: dict[str, Any]) -> list[Any]:
    t = _table(table)
    return [t.c[c] == row[c] for c in HISTORY_TABLES[table]]


async def _write(conn: Any, table: str, i: int) -> None:
    await conn.execute(sa.insert(_table(table)).values(ROWS[table](i)))


async def _revise(conn: Any, table: str, i: int) -> None:
    column, value = REVISE[table]
    await conn.execute(
        sa.update(_table(table)).where(*_key(table, ROWS[table](i))).values({column: value(i)})
    )


async def _retire(conn: Any, table: str, i: int) -> None:
    await conn.execute(sa.delete(_table(table)).where(*_key(table, ROWS[table](i))))


async def _append(conn: Any, table: str, n: int) -> None:
    await conn.execute(sa.insert(_table(table)).values(APPEND[table](n)))


async def _held(engine: Any) -> dict[str, list[str]]:
    """Every restorable table's rows, as canonical JSON, sorted."""
    out: dict[str, list[str]] = {}
    async with engine.connect() as conn:
        for table in copied_tables():
            images = (await conn.execute(
                sa.text(f'SELECT to_jsonb(t)::text FROM "{table}" t')  # noqa: S608 — module constant
            )).scalars().all()
            out[table] = sorted(json.dumps(json.loads(i), sort_keys=True) for i in images)
    return out


async def _clock(engine: Any) -> datetime:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()


@pytest.mark.asyncio
async def test_a_snapshot_at_each_moment_holds_exactly_what_was_held(
    pg_url: str, pg_template: str,
) -> None:
    engine = create_sql_engine(pg_url)
    tables = [t for t in copied_tables() if t in HISTORY_TABLES]
    try:
        async with engine.begin() as conn:  # before t1
            # `culture.head_revision_id` is a foreign key: its revisions first.
            for i in range(6):
                await _append(conn, "culture_revision", 100 + i)
            for table in tables:
                for i in (0, 1, 2, 3):
                    await _write(conn, table, i)
            await conn.execute(sa.insert(_table("message_edit")).values(
                id=PRUNED_EDIT, message_id=ROWS["messages"](2)["id"], ts=MS0, body="edited",
            ))
        async with engine.begin() as conn:
            for table in tables:
                await _revise(conn, table, 3)
                await _retire(conn, table, 3)
            for table in APPEND:
                await _append(conn, table, 1)
        t1 = await _clock(engine)
        held_t1 = await _held(engine)

        async with engine.begin() as conn:  # between t1 and t2
            for table in tables:
                await _revise(conn, table, 1)
                await _retire(conn, table, 2)
                await _write(conn, table, 4)
            for table in APPEND:
                await _append(conn, table, 2)
        t2 = await _clock(engine)
        held_t2 = await _held(engine)

        async with engine.begin() as conn:  # after t2
            for table in tables:
                await _revise(conn, table, 4)
                await _retire(conn, table, 1)
                await _write(conn, table, 5)
            for table in APPEND:
                await _append(conn, table, 3)
        now = await _held(engine)
    finally:
        await engine.dispose()

    # Positive control: every table moved at each step, so a restore that
    # returned today's rows, or t1's at t2, cannot pass.
    for table in (*tables, *APPEND):
        assert held_t1[table] != held_t2[table] != now[table], table

    def edits(held: dict[str, list[str]]) -> set[int]:
        return {json.loads(r)["id"] for r in held["message_edit"]}

    assert PRUNED_EDIT in edits(held_t1) and PRUNED_EDIT not in edits(held_t2)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.connect() as conn:
            hidden = (await conn.execute(sa.text(
                "SELECT op FROM row_history WHERE table_name = 'message_edit' "
                "AND row_key = jsonb_build_object('id', CAST(:i AS integer)) "
                "AND superseded_at IS NULL"
            ), {"i": PRUNED_EDIT})).scalar_one()
    finally:
        await engine.dispose()
    assert hidden == "delete"  # the cascade was recorded: hidden, and kept

    for at, held in ((t1, held_t1), (t2, held_t2)):
        async with sandbox_as_of(pg_url, at, template=pg_template) as (url, report):
            restored_engine = create_sql_engine(url)
            try:
                restored = await _held(restored_engine)
            finally:
                await restored_engine.dispose()
        assert report.orphaned == {}
        for table in copied_tables():
            assert restored[table] == held[table], f"{table} at {at.isoformat()}"
