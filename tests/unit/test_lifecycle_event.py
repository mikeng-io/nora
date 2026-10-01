"""`deploy/computer/lifecycle_event.py` (DEC-262): the closed vocabulary, the
quoting, the spool, and that the script only records kinds it knows."""

from __future__ import annotations

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


def test_a_spool_line_that_is_not_an_event_is_an_error_not_skipped(tmp_path: Path) -> None:
    """A spool that silently drops a line is an audit that does."""
    spool = tmp_path / "spool.jsonl"
    lifecycle.main(["record", "--spool", str(spool), "--event", "create"])
    with spool.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(ValueError, match="not a lifecycle event"):
        lifecycle.read_spool(spool)


def test_record_appends_one_line_per_event_and_prints_its_id(tmp_path: Path, capsys) -> None:
    spool = tmp_path / "deep" / "spool.jsonl"
    assert lifecycle.main(["record", "--spool", str(spool), "--event", "create",
                           "--detail", "image=nora-computer:latest"]) == 0
    printed = capsys.readouterr().out.strip()
    lifecycle.main(["record", "--spool", str(spool), "--event", "destroy"])
    events = lifecycle.read_spool(spool)
    assert [e["event"] for e in events] == ["create", "destroy"]
    assert events[0]["id"] == printed
    assert events[0]["detail"] == {"image": "nora-computer:latest"}


def test_a_detail_without_an_equals_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="key=value"):
        lifecycle.main(["record", "--spool", str(tmp_path / "s"), "--event", "create",
                        "--detail", "oops"])
