"""Journal recovery semantics (design/platform-bridges §journal)."""

import pytest

from nora.platform.bridge.journal import Journal, JournalError


def test_journal_torn_tail_truncates_and_replays_intact(tmp_path) -> None:
    p = tmp_path / "j.jsonl"
    j = Journal(p)
    j.append({"n": 1})
    j.append({"n": 2})
    with p.open("ab") as fh:  # crash mid-write — no newline
        fh.write(b'{"n":3, "par')
    records = j.recover()
    assert [r["n"] for r in records] == [1, 2]
    # The torn bytes are gone; a subsequent append lands cleanly.
    j.append({"n": 4})
    assert [r["n"] for r in j.recover()] == [1, 2, 4]


def test_journal_interior_corruption_refuses_instead_of_dropping(tmp_path) -> None:
    p = tmp_path / "j.jsonl"
    j = Journal(p)
    j.append({"n": 1})
    j.append({"n": 2})
    data = p.read_bytes()
    nl = data.find(b"\n")
    p.write_bytes(data[:nl + 1] + b"GARBAGE\n" + data[nl + 1:])
    with pytest.raises(JournalError):
        j.recover()


def test_journal_preflight_fails_closed(tmp_path) -> None:
    blocked = tmp_path / "a-file-not-a-dir"
    blocked.write_text("x")
    with pytest.raises(JournalError):
        Journal(blocked / "j.jsonl").probe()
