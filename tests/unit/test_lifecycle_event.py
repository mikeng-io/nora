"""`deploy/computer/lifecycle_event.py` (DEC-261): the closed vocabulary, the
quoting, the spool, and that the script only records kinds it knows."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.unit.nora_computer_harness import SCRIPT, load_lifecycle_event

lifecycle = load_lifecycle_event()


def test_an_unknown_event_or_status_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown lifecycle event"):
        lifecycle.make_event("reboot")
    with pytest.raises(ValueError, match="unknown status"):
        lifecycle.make_event("create", "maybe")


def test_every_event_the_script_records_is_in_the_vocabulary() -> None:
    """A typo in a `record_event` call would be a runtime error on the deploy
    host, at the moment of a recreate; read it here instead."""
    used = set(re.findall(r"record_event ([a-z_]+) ", SCRIPT.read_text()))
    assert used, "no record_event call found, so this check read nothing"
    assert used <= lifecycle.EVENTS, used - lifecycle.EVENTS
    # And the other way: a kind the vocabulary lists that nothing records is a
    # row nobody will ever see.
    assert used >= lifecycle.EVENTS, lifecycle.EVENTS - used


def test_the_fingerprint_is_twelve_hex_and_not_the_token() -> None:
    token = "t" * 64
    fp = lifecycle.fingerprint(token)
    assert re.fullmatch(r"[0-9a-f]{12}", fp)
    assert token not in fp
    assert lifecycle.fingerprint(token) == fp != lifecycle.fingerprint(token + "x")


def test_a_nul_is_stripped_at_the_source_and_refused_at_the_sql() -> None:
    """A NUL in a detail would become `\\u0000` in the jsonb literal, which
    Postgres refuses, and one such line would park every later sync."""
    made = lifecycle.make_event("create", name="a\x00b", detail={"k\x00": "v\x00w"})
    assert (made["name"], made["detail"]) == ("ab", {"k": "vw"})
    lifecycle.insert_sql(made)
    with pytest.raises(ValueError, match="NUL"):
        lifecycle.insert_sql({**made, "status": "ok\x00"})


def test_a_corrupt_line_is_logged_and_skipped_and_never_parks_the_ones_after_it(
    tmp_path: Path, capsys,
) -> None:
    """psql stops at the first error and a sync that raised would retry the same
    line forever, so one bad line would hold every later event out of the trail.
    It is logged and skipped, and the offset moves past it."""
    spool = tmp_path / "spool.jsonl"
    lifecycle.main(["record", "--spool", str(spool), "--event", "create"])
    with spool.open("a") as fh:
        fh.write("{not json\n")
        fh.write('{"id": "x"}\n')                       # JSON, not an event
    lifecycle.main(["record", "--spool", str(spool), "--event", "destroy"])
    capsys.readouterr()
    events, offset = lifecycle.read_spool(spool)
    assert [e["event"] for e in events] == ["create", "destroy"]
    assert offset == spool.stat().st_size
    err = capsys.readouterr().err
    assert "skipping a line that is not an event" in err
    assert "skipping an invalid event" in err


def test_an_event_that_cannot_become_sql_is_skipped_not_raised(tmp_path: Path, capsys) -> None:
    spool = tmp_path / "spool.jsonl"
    good = lifecycle.make_event("create")
    bad = {**good, "id": "not-a-uuid"}
    spool.write_text(json.dumps(bad) + "\n" + json.dumps(good) + "\n")
    events, _ = lifecycle.read_spool(spool)
    assert [e["id"] for e in events] == [good["id"]]
    assert "skipping an invalid event" in capsys.readouterr().err


def test_a_half_written_last_line_is_left_for_the_next_sync(tmp_path: Path) -> None:
    """No trailing newline means the writer has not finished: not corrupt, not
    consumed. The offset stops before it, and once it is complete it is read."""
    spool = tmp_path / "spool.jsonl"
    lifecycle.main(["record", "--spool", str(spool), "--event", "create"])
    whole = spool.stat().st_size
    second = json.dumps(lifecycle.make_event("destroy"), sort_keys=True)
    with spool.open("a") as fh:
        fh.write(second[:30])
    events, offset = lifecycle.read_spool(spool)
    assert [e["event"] for e in events] == ["create"] and offset == whole
    with spool.open("a") as fh:
        fh.write(second[30:] + "\n")
    events, offset = lifecycle.read_spool(spool, whole)
    assert [e["event"] for e in events] == ["destroy"] and offset == spool.stat().st_size


def test_a_sync_reads_only_what_is_after_the_synced_offset(tmp_path: Path, capsys) -> None:
    spool, state = tmp_path / "spool.jsonl", tmp_path / "synced"
    lifecycle.main(["record", "--spool", str(spool), "--event", "create"])
    capsys.readouterr()
    lifecycle.main(["sql", "--spool", str(spool), "--state", str(state)])
    first = capsys.readouterr().out.splitlines()
    assert sum(ln.startswith("INSERT") for ln in first) == 1
    lifecycle.main(["mark", "--state", str(state), "--offset", first[-1].split("=")[1]])
    lifecycle.main(["record", "--spool", str(spool), "--event", "destroy"])
    capsys.readouterr()
    lifecycle.main(["sql", "--spool", str(spool), "--state", str(state)])
    second = capsys.readouterr().out.splitlines()
    assert sum(ln.startswith("INSERT") for ln in second) == 1       # not the first again
    lifecycle.main(["sql", "--spool", str(spool), "--state", str(state), "--all"])
    everything = capsys.readouterr().out.splitlines()
    assert sum(ln.startswith("INSERT") for ln in everything) == 2


def test_the_synced_offset_never_moves_back_and_survives_a_replaced_spool(
    tmp_path: Path, capsys,
) -> None:
    state, spool = tmp_path / "synced", tmp_path / "spool.jsonl"
    lifecycle.write_offset(state, 500)
    lifecycle.write_offset(state, 100)          # a slower sync finishing late
    assert lifecycle.read_offset(state) == 500
    lifecycle.main(["record", "--spool", str(spool), "--event", "create"])
    capsys.readouterr()
    events, offset = lifecycle.read_spool(spool, lifecycle.read_offset(state))
    # The spool is shorter than the offset: it was replaced; read it all.
    assert len(events) == 1 and offset == spool.stat().st_size
    assert "shorter than the synced offset" in capsys.readouterr().err


def test_record_appends_one_line_per_event_and_prints_its_id(tmp_path: Path, capsys) -> None:
    spool = tmp_path / "deep" / "spool.jsonl"
    assert lifecycle.main(["record", "--spool", str(spool), "--event", "create",
                           "--detail", "image=nora-computer:latest"]) == 0
    printed = capsys.readouterr().out.strip()
    lifecycle.main(["record", "--spool", str(spool), "--event", "destroy"])
    events, _ = lifecycle.read_spool(spool)
    assert [e["event"] for e in events] == ["create", "destroy"]
    assert events[0]["id"] == printed
    assert events[0]["detail"] == {"image": "nora-computer:latest"}


def test_a_detail_without_an_equals_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="key=value"):
        lifecycle.main(["record", "--spool", str(tmp_path / "s"), "--event", "create",
                        "--detail", "oops"])
