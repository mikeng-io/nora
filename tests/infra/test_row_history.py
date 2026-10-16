"""`row_history` and its trigger: every in-place table is append-only in
system time (migration `0068`).

Against a real Postgres, because the property lives in the database: a
trigger, a partial unique index and a migration's backfill. What each test
pins:

- the migrated database's triggers are EXACTLY `HISTORY_TABLES` (a table
  dropped from the migration's list, or added to the module's without a
  migration, is a history nothing records);
- every table in the schema is classified exactly once;
- one row's chain: insert, update, a no-op upsert, delete, re-insert — each
  a version, each closed by the next, one open at a time;
- `as_of` answers from the chain, and a hidden (deleted) row is not held;
- the capture switch the snapshot seeds under records nothing;
- the backfill: rows that predate `0068` get the versions their own columns
  prove (a note's revisions and retirement, a reminder's pending and fired
  states, a cancelled reminder never outstanding).
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command

from nora.infra.sql import create_sql_engine
from nora.infra.sql.history import (
    APPEND_ONLY,
    CAPTURE_SETTING,
    DEFERRED,
    HISTORY_TABLES,
    NOT_RESTORED,
    as_of,
)
from nora.infra.sql.migrations_runner import alembic_config, run_migrations
from nora.infra.sql.schema import METADATA


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _now(engine: Any) -> datetime:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()


async def _held(engine: Any, table: str, at: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with engine.connect() as conn:
        async for part in as_of(conn, table, at):
            out.extend(part)
    return out


async def _chain(engine: Any, table: str) -> list[Any]:
    async with engine.connect() as conn:
        return list((await conn.execute(sa.text(
            "SELECT id, op, image, recorded_at, superseded_at, superseded_by "
            "FROM row_history WHERE table_name = :t ORDER BY id"
        ), {"t": table})).all())


def test_every_table_is_classified_exactly_once() -> None:
    groups = [set(HISTORY_TABLES), set(APPEND_ONLY), set(DEFERRED), set(NOT_RESTORED)]
    classified = set().union(*groups)
    assert classified == set(METADATA.tables), (
        f"unclassified: {sorted(set(METADATA.tables) - classified)}; "
        f"unknown: {sorted(classified - set(METADATA.tables))}"
    )
    assert sum(len(g) for g in groups) == len(classified), "a table is in two groups"


@pytest.mark.asyncio
async def test_the_migrated_triggers_are_exactly_history_tables(engine: Any) -> None:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT c.relname, t.tgargs FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE t.tgname = 'nora_row_history' AND NOT t.tgisinternal"
        ))).all()
    installed = {
        name: tuple(a.decode() for a in bytes(args).split(b"\x00") if a)
        for name, args in rows
    }
    assert installed == HISTORY_TABLES


@pytest.mark.asyncio
async def test_one_rows_chain_is_every_version_it_held(engine: Any) -> None:
    """Insert, update, a no-op upsert, delete, re-insert — on the real store."""
    upsert = (
        "INSERT INTO tag_debounce_state (platform, conversation_id, last_allowed_at) "
        "VALUES ('signal', 'g1', :v) ON CONFLICT (platform, conversation_id) "
        "DO UPDATE SET last_allowed_at = excluded.last_allowed_at"
    )
    moments: list[datetime] = []
    async with engine.begin() as conn:
        await conn.execute(sa.text(upsert), {"v": 1})
    moments.append(await _now(engine))
    async with engine.begin() as conn:
        await conn.execute(sa.text(upsert), {"v": 2})
    moments.append(await _now(engine))
    async with engine.begin() as conn:
        # writes nothing new: the same value, and `updated_at` is not moved
        await conn.execute(sa.text(
            "UPDATE tag_debounce_state SET last_allowed_at = last_allowed_at"
        ))
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM tag_debounce_state"))
    moments.append(await _now(engine))
    async with engine.begin() as conn:
        await conn.execute(sa.text(upsert), {"v": 3})
    moments.append(await _now(engine))

    chain = await _chain(engine, "tag_debounce_state")
    assert [r.op for r in chain] == ["insert", "update", "delete", "insert"]
    assert [r.image["last_allowed_at"] for r in chain] == [1, 2, 2, 3]
    for version, following in itertools.pairwise(chain):
        assert version.superseded_by == following.id
        assert version.superseded_at == following.recorded_at
    assert chain[-1].superseded_at is None

    held = [[r["last_allowed_at"] for r in await _held(engine, "tag_debounce_state", m)]
            for m in moments]
    assert held == [[1], [2], [], [3]]


@pytest.mark.asyncio
async def test_a_second_open_version_is_refused(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO tag_debounce_state (platform, conversation_id, last_allowed_at) "
            "VALUES ('signal', 'g1', 1)"
        ))
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "
                "VALUES ('tag_debounce_state', "
                "'{\"platform\": \"signal\", \"conversation_id\": \"g1\"}', 'insert', "
                "'{}', clock_timestamp())"
            ))


@pytest.mark.asyncio
async def test_capture_off_records_nothing(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(f"SET LOCAL {CAPTURE_SETTING} = 'off'"))
        await conn.execute(sa.text(
            "INSERT INTO tag_debounce_state (platform, conversation_id, last_allowed_at) "
            "VALUES ('signal', 'g1', 1)"
        ))
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO tag_debounce_state (platform, conversation_id, last_allowed_at) "
            "VALUES ('signal', 'g2', 1)"
        ))
    chain = await _chain(engine, "tag_debounce_state")
    assert [r.image["conversation_id"] for r in chain] == ["g2"]


@pytest.mark.asyncio
async def test_the_backfill_dates_what_each_row_proves(pg_url: str, engine: Any) -> None:
    """Rows written before `0068` get the versions their own columns prove."""
    command.downgrade(alembic_config(pg_url), "0067_summons_message_key")
    day = datetime(2026, 9, 1, tzinfo=UTC)

    def ms(d: datetime) -> int:
        return int(d.timestamp() * 1000)

    note = "11111111-1111-4111-8111-111111111111"
    rev1 = "22222222-2222-4222-8222-222222222221"
    rev2 = "22222222-2222-4222-8222-222222222222"
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO note (id, unit, title, description, uncertainty, "
            "closing_condition, anchor_date, established_in, created_at, "
            "head_revision_id, retired_at, retire_reason) VALUES (:id, 'u', 't', 'd', "
            "'open', 'c', 20260901, 'signal:g1', :c, :h, :r, 'closed')"
        ), {"id": note, "c": ms(day), "h": rev2, "r": ms(day + timedelta(days=3))})
        for rid, body, at, parent in (
            (rev1, "first", day, None), (rev2, "second", day + timedelta(days=1), rev1),
        ):
            await conn.execute(sa.text(
                "INSERT INTO note_revision (id, note_id, body, parent_revision_id, "
                "written_at, established_by) VALUES (:id, :n, :b, :p, :w, 'test')"
            ), {"id": rid, "n": note, "b": body, "p": parent, "w": ms(at)})
        for rid, state, fired in (
            ("a" * 32, "done", day + timedelta(hours=5)),
            ("b" * 32, "cancelled", None),
        ):
            await conn.execute(sa.text(
                "INSERT INTO reminders (id, requester_person_id, platform, "
                "conversation_id, conversation_type, text, created_at, due_at, state, "
                "fired_at, outcome) VALUES (:id, 'p', 'signal', 'g1', 'group', 'x', "
                ":c, :d, :s, :f, :o)"
            ), {"id": rid, "c": day, "d": day + timedelta(hours=5), "s": state,
                "f": fired, "o": "sent" if fired else None})
    run_migrations(pg_url)

    def at(hours: float) -> datetime:
        return day + timedelta(hours=hours)

    notes = {h: await _held(engine, "note", at(h)) for h in (-1, 12, 36, 80)}
    assert notes[-1] == []
    assert [(n["head_revision_id"], n["retired_at"]) for n in notes[12]] == [(rev1, None)]
    assert [(n["head_revision_id"], n["retired_at"]) for n in notes[36]] == [(rev2, None)]
    assert [(n["head_revision_id"], n["retire_reason"]) for n in notes[80]] == [(rev2, "closed")]

    def states(rows: list[dict[str, Any]]) -> dict[str, str]:
        return {r["id"]: r["state"] for r in rows}

    fired, cancelled = (str(uuid.UUID(c * 32)) for c in "ab")
    assert states(await _held(engine, "reminders", at(1))) == {
        fired: "pending", cancelled: "cancelled",
    }
    assert states(await _held(engine, "reminders", at(6))) == {
        fired: "done", cancelled: "cancelled",
    }
    # the head the chain ends on IS the live row, for every backfilled table,
    # up to a key a LATER migration added without a row_history catch-up
    # (0075_withdrawal's `unsupported_at`): such a key is ABSENT from `head`,
    # not `null`, and the read side (`nora.infra.sql.snapshot_at._insert_sql`,
    # `jsonb_populate_recordset`) treats an absent key as the column's own
    # default, NULL here since the columns added since this backfill carry no
    # server default. A key `head` is missing must therefore be NULL in `live`,
    # never some other value.
    async with engine.connect() as conn:
        for table in ("note", "reminders"):
            live_rows = (await conn.execute(sa.text(
                f'SELECT to_jsonb(t) FROM "{table}" t'  # noqa: S608 — a literal above
            ))).scalars().all()
            head_rows = (await conn.execute(sa.text(
                "SELECT image FROM row_history WHERE table_name = :t "
                "AND superseded_at IS NULL AND op <> 'delete'"
            ), {"t": table})).scalars().all()
            live = sorted(live_rows, key=str)
            head = sorted(head_rows, key=str)
            assert len(head) == len(live), table
            for h, live_row in zip(head, live, strict=True):
                missing = set(live_row) - set(h)
                assert all(live_row[k] is None for k in missing), (table, missing)
                filled = {**dict.fromkeys(missing), **h}
                assert filled == live_row, table


@pytest.mark.asyncio
async def test_a_truncate_is_refused_unless_capture_is_off(engine: Any) -> None:
    with pytest.raises(sa.exc.DBAPIError, match="would bypass row_history"):
        async with engine.begin() as conn:
            await conn.execute(sa.text("TRUNCATE tag_debounce_state"))
    async with engine.begin() as conn:
        await conn.execute(sa.text(f"SET LOCAL {CAPTURE_SETTING} = 'off'"))
        await conn.execute(sa.text("TRUNCATE tag_debounce_state"))
