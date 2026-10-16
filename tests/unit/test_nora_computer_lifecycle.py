"""The lifecycle audit of `deploy/computer/nora-computer` (DEC-262): a row for each VM
create, recreate, computerd restart, token rotation and policy-log read, and
for destroy and the egress change beside them.

The script runs on the host as the operator, outside the nora container, so the
first record is a host-side spool and Postgres' `computer_events` is replayed
from it. Driven here through the stub host: what the script wrote to the spool,
what it piped to `psql`, and that no token reached either. The SQL itself is run
against a real Postgres in `tests/infra/test_lifecycle_event_sql.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.nora_computer_harness import Host, load_lifecycle_event

fingerprint = load_lifecycle_event().fingerprint

TOKEN_A = "tok-aaaaaaaaaaaaaaaaaaaaaaaa"  # noqa: S105 - a test value
TOKEN_B = "tok-bbbbbbbbbbbbbbbbbbbbbbbb"  # noqa: S105 - a test value


@pytest.fixture
def fresh(tmp_path: Path) -> Host:
    """No VM yet: the first `serve` creates it."""
    host = Host(tmp_path, sandbox=False)
    host.write_token(TOKEN_A)
    return host


def _details(host: Host, kind: str) -> list[dict]:
    return [e["detail"] for e in host.events() if e["event"] == kind]


def test_the_first_serve_records_a_create_then_a_computerd_start(fresh: Host) -> None:
    result = fresh.run("serve")
    assert result.returncode == 0, result.stdout + result.stderr
    assert fresh.event_kinds() == ["create", "computerd_start"]
    # The same two rows reached Postgres, each an idempotent insert. Every sync
    # replays the whole spool (that is what makes it safe to run any time), so
    # the statements are counted distinct, by event id.
    sql = fresh.psql_sql()
    ids = [e["id"] for e in fresh.events()]
    assert len(ids) == 2 and all(i in sql for i in ids)
    # Each event is sent ONCE: a sync reads from the offset the last one reached,
    # not the whole history.
    assert sql.count("INSERT INTO computer_events") == 2
    assert "ON CONFLICT DO NOTHING" in sql


def test_a_restart_of_the_unit_is_a_computerd_start_and_not_a_create(fresh: Host) -> None:
    fresh.run("serve")
    again = fresh.run("serve")
    assert again.returncode == 0, again.stdout + again.stderr
    assert fresh.event_kinds() == ["create", "computerd_start", "computerd_start"]


def test_a_create_after_a_destroy_is_a_recreate_that_names_the_earlier_create(fresh: Host) -> None:
    fresh.run("serve")
    first = fresh.state_dir / "created-at"
    assert first.exists()
    previous = first.read_text().strip()
    assert fresh.run("destroy").returncode == 0
    fresh.run("serve")
    assert fresh.event_kinds() == [
        "create", "computerd_start", "destroy", "recreate", "computerd_start",
    ]
    assert _details(fresh, "recreate") == [
        {"image": "nora-computer:latest", "previous_create": previous},
    ]


def test_a_rotated_token_is_recorded_once_by_fingerprint_and_never_by_value(fresh: Host) -> None:
    fresh.run("serve")
    assert _details(fresh, "token_rotation") == []          # the first start is no rotation
    fresh.run("serve")
    assert _details(fresh, "token_rotation") == []          # same token: no rotation
    fresh.write_token(TOKEN_B)
    fresh.run("serve")
    assert _details(fresh, "token_rotation") == [
        {"previous_fingerprint": fingerprint(TOKEN_A), "fingerprint": fingerprint(TOKEN_B)},
    ]
    # The rotation is recorded before the start it belongs to.
    assert fresh.event_kinds()[-2:] == ["token_rotation", "computerd_start"]
    # Neither token, in any form, is in the spool or in what went to Postgres.
    everything = fresh.spool_path.read_text() + fresh.psql_sql()
    assert TOKEN_A not in everything and TOKEN_B not in everything


def test_reading_the_policy_log_is_an_audited_read(fresh: Host) -> None:
    fresh.run("serve")
    result = fresh.run("policy-log", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "policy-log-output" in result.stdout + result.stderr
    assert fresh.event_kinds()[-1] == "policy_log_read"
    assert _details(fresh, "policy_log_read") == [{"args": "--json"}]
    assert fresh.sbx_calls("policy log nora-computer --json")


def test_the_policy_log_is_not_read_when_the_read_could_not_be_recorded(fresh: Host) -> None:
    """An audited read that cannot be audited does not happen."""
    fresh.run("serve")
    before = len(fresh.sbx_calls("policy log"))
    result = fresh.run("policy-log", NORA_COMPUTER_STATE_DIR="/dev/null/nope")
    assert result.returncode == 5, result.stdout + result.stderr
    assert len(fresh.sbx_calls("policy log")) == before


def test_an_egress_change_is_recorded(fresh: Host) -> None:
    fresh.run("serve")
    before = fresh.event_kinds().count("egress_apply")
    assert fresh.run("egress", "apply").returncode == 0
    assert fresh.event_kinds().count("egress_apply") == before + 1


def test_a_failed_create_is_recorded_as_failed_and_the_unit_still_exits_non_zero(
    fresh: Host,
) -> None:
    result = fresh.run("serve", STUB_RUN_FAILS="1")
    assert result.returncode == 6, result.stdout + result.stderr
    assert fresh.event_kinds() == ["create"]
    assert fresh.events()[0]["status"] == "failed"
    assert not (fresh.state_dir / "created-at").exists()


def test_a_database_that_is_down_costs_nothing_but_a_delay(fresh: Host) -> None:
    """The unit starts before the db is healthy more often than not. The spool
    holds the events, serve carries on, and `sync-events` replays them."""
    result = fresh.run("serve", STUB_DB_FAIL="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not yet in Postgres" in result.stdout + result.stderr
    assert fresh.event_kinds() == ["create", "computerd_start"]
    (fresh.dir / "psql.sql").unlink()
    synced = fresh.run("sync-events")
    assert synced.returncode == 0, synced.stdout + synced.stderr
    ids = [e["id"] for e in fresh.events()]
    sql = fresh.psql_sql()
    assert all(i in sql for i in ids)
    assert sql.count("INSERT INTO computer_events") == len(ids)   # the file was cleared first


def test_destroy_is_recorded_and_so_is_a_destroy_that_failed(fresh: Host) -> None:
    fresh.run("serve")
    assert fresh.run("destroy").returncode == 0
    assert fresh.event_kinds()[-1] == "destroy" and fresh.events()[-1]["status"] == "ok"
    fresh.run("serve")
    failed = fresh.run("destroy", STUB_RM_FAILS="1")
    assert failed.returncode == 1
    assert fresh.events()[-1]["event"] == "destroy" and fresh.events()[-1]["status"] == "failed"


def test_a_destroy_that_could_not_be_recorded_does_not_look_audited(fresh: Host) -> None:
    fresh.run("serve")
    result = fresh.run("destroy", NORA_COMPUTER_STATE_DIR="/dev/null/nope")
    assert result.returncode == 5, result.stdout + result.stderr
    assert "could NOT be recorded" in result.stdout + result.stderr


def test_a_later_sync_sends_only_the_events_since_the_last_good_one(fresh: Host) -> None:
    fresh.run("serve")
    sent = fresh.psql_sql().count("INSERT INTO computer_events")
    fresh.run("policy-log")
    assert fresh.psql_sql().count("INSERT INTO computer_events") == sent + 1
    assert int((fresh.state_dir / "lifecycle-events.synced").read_text()) == \
        fresh.spool_path.stat().st_size


def test_a_sync_that_failed_does_not_advance_the_offset(fresh: Host) -> None:
    fresh.run("serve", STUB_DB_FAIL="1")
    assert not (fresh.state_dir / "lifecycle-events.synced").exists()
    (fresh.dir / "psql.sql").unlink()
    assert fresh.run("sync-events").returncode == 0
    assert fresh.psql_sql().count("INSERT INTO computer_events") == 2
    assert int((fresh.state_dir / "lifecycle-events.synced").read_text()) == \
        fresh.spool_path.stat().st_size


def test_sync_events_all_replays_the_whole_history(fresh: Host) -> None:
    fresh.run("serve")
    (fresh.dir / "psql.sql").unlink()
    assert fresh.run("sync-events").returncode == 0
    assert fresh.psql_sql() == ""                       # nothing new: psql is not even called
    assert fresh.run("sync-events", "--all").returncode == 0
    assert fresh.psql_sql().count("INSERT INTO computer_events") == 2


def test_a_corrupt_line_in_the_spool_does_not_stop_later_events_reaching_postgres(
    fresh: Host,
) -> None:
    fresh.run("serve")
    with fresh.spool_path.open("a") as fh:
        fh.write("{half a line\n")
    result = fresh.run("policy-log")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "policy_log_read" in fresh.psql_sql()
    assert "skipping a line that is not an event" in result.stderr
