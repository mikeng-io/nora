"""The lifecycle audit's SQL runs against the real `computer_events` table
(DEC-262).

`deploy/computer/lifecycle_event.py` writes the rows `nora-computer` records
(create, recreate, computerd start, token rotation, policy-log read, destroy,
egress, backup, restore), and a stub `psql` proves only that SQL was piped. These
run it on Postgres at the migration head, with the schema's own CHECK
constraints and the append-only triggers in force: the actor `operator` and the
`direct` path must already be allowed (no migration is needed or added), a
replay of the whole spool must be a no-op and not an error, and the triggers
must still refuse every UPDATE and DELETE of the rows it wrote.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from tests.unit.nora_computer_harness import load_lifecycle_event

lifecycle = load_lifecycle_event()


def _dsn(pg_url: str) -> str:
    return pg_url.replace("postgresql+asyncpg://", "postgresql://")


def _run(dsn: str, statements: list[str]) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


def _rows(dsn: str) -> list[dict]:
    with psycopg.connect(dsn) as conn:
        cur = conn.execute(
            "SELECT actor, tool, path, phase, status, platform, conversation_id, scope, "
            "request, result, created_at, started_at, finished_at, session_id "
            "FROM computer_events ORDER BY created_at, id"
        )
        names = [d.name for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


@pytest.mark.parametrize("event", sorted(lifecycle.EVENTS))
def test_every_event_kind_satisfies_the_tables_own_constraints(pg_url, event) -> None:
    """The five constraints (actor, path, phase, phase times, uniqueness) accept
    what the script writes for each kind, with no migration."""
    dsn = _dsn(pg_url)
    made = lifecycle.make_event(
        event, "ok", name="nora-computer", detail={"k": "v"}, actor_host="norty",
    )
    _run(dsn, [lifecycle.insert_sql(made)])
    (row,) = _rows(dsn)
    assert (row["actor"], row["tool"], row["path"], row["phase"]) == (
        "operator", "computer_vm", "direct", "finished",
    )
    assert row["request"] == {"event": event, "name": "nora-computer", "host": "norty"}
    assert row["result"] == {"status": "ok", "k": "v"}
    assert row["started_at"] == row["finished_at"] == row["created_at"]
    assert row["session_id"] is None


def test_replaying_the_whole_spool_is_a_no_op(pg_url, tmp_path) -> None:
    dsn = _dsn(pg_url)
    spool = tmp_path / "spool.jsonl"
    for kind in ("create", "computerd_start", "token_rotation"):
        lifecycle.main(["record", "--spool", str(spool), "--event", kind])
    statements = _sql_of(spool)
    assert len(statements) == 3
    _run(dsn, statements)
    _run(dsn, statements)           # a second sync of the same spool
    _run(dsn, statements[:1])       # and of a prefix
    assert len(_rows(dsn)) == 3


def _sql_of(spool) -> list[str]:
    return [lifecycle.insert_sql(e) for e in lifecycle.read_spool(spool)[0]]


def test_a_row_carries_when_it_happened_not_when_it_was_synced(pg_url) -> None:
    dsn = _dsn(pg_url)
    then = datetime(2026, 10, 1, 3, 5, 4, tzinfo=UTC)
    made = lifecycle.make_event("computerd_start", now=then)
    _run(dsn, [lifecycle.insert_sql(made)])
    (row,) = _rows(dsn)
    assert row["created_at"] == then


def test_the_append_only_triggers_still_refuse_every_change(pg_url) -> None:
    """The writer must not weaken the table. UPDATE, DELETE and TRUNCATE of a
    lifecycle row are refused by the same triggers as of a tool's row."""
    dsn = _dsn(pg_url)
    _run(dsn, [lifecycle.insert_sql(lifecycle.make_event("destroy"))])
    for statement in (
        "UPDATE computer_events SET status = 'ok'",
        "DELETE FROM computer_events",
        "TRUNCATE computer_events",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            _run(dsn, [statement])
    assert len(_rows(dsn)) == 1


def test_a_conflicting_replay_does_not_try_to_update(pg_url) -> None:
    """ON CONFLICT DO NOTHING, not DO UPDATE: a replay that updated would trip
    the trigger. Proved by the replay succeeding with the trigger in force."""
    dsn = _dsn(pg_url)
    made = lifecycle.make_event("create")
    _run(dsn, [lifecycle.insert_sql(made)])
    changed = {**made, "status": "failed"}      # same operation_id, a different body
    _run(dsn, [lifecycle.insert_sql(changed)])
    (row,) = _rows(dsn)
    assert row["status"] == "ok"                # the first record stands


def test_hostile_text_in_a_detail_is_data_not_sql(pg_url) -> None:
    dsn = _dsn(pg_url)
    nasty = "x'); DROP TABLE computer_events; --"
    made = lifecycle.make_event("policy_log_read", detail={"args": nasty})
    _run(dsn, [lifecycle.insert_sql(made)])
    (row,) = _rows(dsn)
    assert row["result"]["args"] == nasty
    assert json.dumps(row["result"])            # and the table is still there


def test_the_event_and_operation_ids_are_uuids_the_table_accepts() -> None:
    made = lifecycle.make_event("backup")
    uuid.UUID(made["id"])
    uuid.UUID(made["operation_id"])
    assert made["id"] != made["operation_id"]


def test_two_syncs_racing_to_insert_the_same_event_do_not_fail(pg_url) -> None:
    """The same event arrives twice as a concurrent sync would send it: the second
    insert collides on the primary key AND the operation key. With a conflict
    TARGET naming only (operation_id, phase), a collision that surfaced on the
    primary key first would raise; bare DO NOTHING takes whichever unique key it is."""
    dsn = _dsn(pg_url)
    made = lifecycle.make_event("computerd_start")
    _run(dsn, [lifecycle.insert_sql(made)])
    _run(dsn, [lifecycle.insert_sql(made)])
    assert len(_rows(dsn)) == 1


def test_a_collision_on_the_primary_key_alone_is_skipped_too(pg_url) -> None:
    dsn = _dsn(pg_url)
    first = lifecycle.make_event("create")
    other = {**lifecycle.make_event("destroy"), "id": first["id"]}      # same id, new operation
    _run(dsn, [lifecycle.insert_sql(first)])
    _run(dsn, [lifecycle.insert_sql(other)])                            # must not raise
    (row,) = _rows(dsn)
    assert row["request"]["event"] == "create"
