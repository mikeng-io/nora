"""`nora.instructions.reload.DirectoryWatcher` — the last-good machine.

The asymmetry these tests exist for: a broken edit to a RUNNING corpus keeps the
previous good value and counts a failure, while a broken corpus at STARTUP
raises. A process that comes up with no instructions and says nothing about it
is `unavailable` reported as `nothing_found`, one layer down.
"""

import logging
import os
import time

import pytest

from nora.instructions.reload import DirectoryWatcher
from nora.obs.metrics import Metrics

COUNTER = "nora_doctrine_reload_failures_total"   # a name Metrics already declares


class Boom(Exception):
    pass


def _touch_newer(path, text):
    """Rewrite with a bumped mtime so the fingerprint moves on fast filesystems."""
    path.write_text(text, encoding="utf-8")
    future = time.time() + 2
    os.utime(path, (future, future))


def _watcher(d, scan, *, metrics=None, reload="mtime"):
    return DirectoryWatcher(
        d, scan=scan, metrics=metrics or Metrics(), subject="doctrine",
        failure_counter=COUNTER, error=Boom, log=logging.getLogger("test.reload"),
        reload=reload,
    )


@pytest.fixture
def corpus(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "a.md").write_text("one", encoding="utf-8")
    return d


def test_an_unchanged_directory_is_not_rescanned(corpus):
    calls = []

    def scan():
        calls.append(1)
        return len(calls)

    watcher = _watcher(corpus, scan)
    assert watcher.current() == 1
    assert watcher.current() == 1
    assert len(calls) == 1


def test_a_touched_file_is_rescanned(corpus):
    calls = []

    def scan():
        calls.append(1)
        return (corpus / "a.md").read_text(encoding="utf-8")

    watcher = _watcher(corpus, scan)
    assert watcher.current() == "one"
    _touch_newer(corpus / "a.md", "two")
    assert watcher.current() == "two"
    assert len(calls) == 2


def test_size_rides_beside_mtime_so_a_frozen_clock_cannot_hide_an_edit(corpus):
    """The fingerprint carries `st_size` as well as `st_mtime_ns`, and this is
    the test that measures it: the edit below is written back at the file's
    ORIGINAL mtime, so mtime alone reports no change. That is not hypothetical —
    a restored backup, an `rsync --times`, and an editor that preserves
    timestamps all produce exactly this shape, and the failure it causes is the
    worst kind: the operator sees their edit on disk and the running system is
    still serving the old text with nothing logged.
    """
    path = corpus / "a.md"
    stat = path.stat()
    watcher = _watcher(corpus, lambda: path.read_text(encoding="utf-8"))
    assert watcher.current() == "one"
    path.write_text("one but longer", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_mtime_ns == stat.st_mtime_ns      # the clock did not move
    assert watcher.current() == "one but longer"


def test_a_new_file_and_a_deleted_file_both_move_the_fingerprint(corpus):
    watcher = _watcher(corpus, lambda: sorted(p.name for p in corpus.glob("*.md")))
    assert watcher.current() == ["a.md"]
    (corpus / "b.md").write_text("two", encoding="utf-8")
    assert watcher.current() == ["a.md", "b.md"]
    (corpus / "a.md").unlink()
    assert watcher.current() == ["b.md"]


def test_startup_mode_freezes_the_corpus_after_the_first_good_scan(corpus):
    watcher = _watcher(
        corpus, lambda: (corpus / "a.md").read_text(encoding="utf-8"), reload="startup",
    )
    assert watcher.current() == "one"
    _touch_newer(corpus / "a.md", "two")
    assert watcher.current() == "one"


def test_a_failing_rescan_keeps_the_last_good_value_and_counts_it(corpus, caplog):
    state = {"ok": True}

    def scan():
        if not state["ok"]:
            raise Boom("a.md: broken")
        return "good"

    metrics = Metrics()
    watcher = _watcher(corpus, scan, metrics=metrics)
    assert watcher.current() == "good"

    state["ok"] = False
    _touch_newer(corpus / "a.md", "whatever")
    with caplog.at_level(logging.ERROR):
        assert watcher.current() == "good"

    assert watcher.failures == 1
    assert metrics.get(COUNTER) == 1
    assert any("doctrine_reload_failed" in r.getMessage() for r in caplog.records)


def test_a_failed_reload_does_not_advance_the_generation(corpus):
    """A caller holding derived state invalidates it on `generation`. A failed
    reload deliberately leaves the last good value in place, so it must not look
    like a change."""
    state = {"ok": True}

    def scan():
        if not state["ok"]:
            raise Boom("broken")
        return "good"

    watcher = _watcher(corpus, scan)
    watcher.current()
    before = watcher.generation
    state["ok"] = False
    _touch_newer(corpus / "a.md", "x")
    watcher.current()
    assert watcher.generation == before


def test_a_broken_file_is_not_reparsed_on_every_read(corpus):
    """The fingerprint is advanced past the failure, so a broken corpus costs
    one parse rather than one per tag for as long as it stays broken."""
    calls = []

    def scan():
        calls.append(1)
        if len(calls) > 1:
            raise Boom("broken")
        return "good"

    watcher = _watcher(corpus, scan)
    watcher.current()
    _touch_newer(corpus / "a.md", "x")
    watcher.current()
    watcher.current()
    watcher.current()
    assert len(calls) == 2
    assert watcher.failures == 1


def test_the_first_scan_raising_re_raises_because_there_is_no_last_good(corpus):
    def scan():
        raise Boom("a.md: broken")

    watcher = _watcher(corpus, scan)
    with pytest.raises(Boom, match=r"a\.md: broken"):
        watcher.current()


def test_only_the_callers_own_error_is_tolerated(corpus):
    """A `scan` that fails some other way is not a bad instruction file. Reporting
    a broken system as a stale one is the wrong of the two, and unrecoverable
    because nothing counts it."""
    state = {"ok": True}

    def scan():
        if not state["ok"]:
            raise ZeroDivisionError("a real bug")
        return "good"

    watcher = _watcher(corpus, scan)
    watcher.current()
    state["ok"] = False
    _touch_newer(corpus / "a.md", "x")
    with pytest.raises(ZeroDivisionError):
        watcher.current()


def test_a_repaired_file_reloads_after_a_failure(corpus):
    state = {"ok": True}

    def scan():
        if not state["ok"]:
            raise Boom("broken")
        return (corpus / "a.md").read_text(encoding="utf-8")

    watcher = _watcher(corpus, scan)
    assert watcher.current() == "one"
    state["ok"] = False
    _touch_newer(corpus / "a.md", "broken")
    assert watcher.current() == "one"
    state["ok"] = True
    _touch_newer(corpus / "a.md", "fixed")
    assert watcher.current() == "fixed"
    assert watcher.failures == 1


def test_an_absent_directory_fingerprints_empty_and_leaves_the_verdict_to_scan(tmp_path):
    """`Path.glob` on a directory that is not there yields nothing rather than
    raising, so the watcher cannot tell "empty" from "gone" and does not try to.
    Deciding that an empty corpus is an error is the CALLER's — the doctrine
    raises "no doctrine files (*.md)"; a corpus with an optional subdirectory
    might not. Pinned because a watcher that guessed here would either mask a
    vanished bind mount or refuse a legitimately empty one."""
    missing = tmp_path / "gone"

    def scan():
        raise Boom(f"no files in {missing}")

    with pytest.raises(Boom, match="no files in"):
        _watcher(missing, scan).current()


def test_a_directory_that_vanishes_mid_run_keeps_the_last_good_value(corpus):
    """The OSError path: `stat()` on a file the glob just listed can fail if the
    mount goes away between the two calls. That is not a bad instruction file,
    but it is also not a reason to take the running instructions away."""
    watcher = _watcher(corpus, lambda: "good")
    assert watcher.current() == "good"
    for path in corpus.glob("*.md"):
        path.unlink()
    corpus.rmdir()
    assert watcher.current() == "good"


def test_a_load_records_the_text_it_served(corpus):
    watcher = _watcher(corpus, lambda: "ok")
    loaded = watcher.loaded()
    assert loaded.files == (("a.md", "one"),)
    assert loaded.generation == 1


def test_a_file_vanishing_beside_a_good_scan_is_not_a_failure(corpus, monkeypatch):
    """A pull can remove a file between the scan and the read of its text.
    The scan was good: it is served, no reload failure is counted, and the
    next read rescans so the load that is recorded has its files."""
    import nora.instructions.reload as reload_module

    real = reload_module.texts_under
    calls = []

    def flaky(directory, glob):
        calls.append(1)
        if len(calls) == 1:
            raise FileNotFoundError("a.md went away mid-pull")
        return real(directory, glob)

    monkeypatch.setattr(reload_module, "texts_under", flaky)
    metrics = Metrics()
    watcher = _watcher(corpus, lambda: "ok", metrics=metrics)
    assert watcher.current() == "ok"
    assert watcher.failures == 0 and metrics.get(COUNTER) == 0
    first = watcher.loaded()  # rescans: the fingerprint was left unset
    assert first.files == (("a.md", "one"),)
    assert first.generation == 2
