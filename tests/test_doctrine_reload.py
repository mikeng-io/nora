import pytest

from nora.doctrine.loader import DoctrineStore
from nora.obs.metrics import Metrics
from tests.test_doctrine_loader import write


@pytest.fixture
def doctrine_dir(tmp_path):
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "identity.md", name="soul", order=10, version=1, body="Original soul.")
    write(d, "voice.md", name="voice", order=20, version=1, body="Original voice.")
    return d


def _touch_newer(path, text):
    """Rewrite with a bumped mtime so the fingerprint changes on fast filesystems."""
    import os
    import time

    path.write_text(text)
    future = time.time() + 2
    os.utime(path, (future, future))


def test_mtime_reload_picks_up_an_edit_without_a_restart(doctrine_dir):
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", metrics=Metrics())
    before = store.get()
    assert "Original voice." in before.text

    _touch_newer(
        doctrine_dir / "voice.md",
        (doctrine_dir / "voice.md").read_text().replace("Original voice.", "Edited voice."),
    )
    after = store.get()
    assert "Edited voice." in after.text
    assert after.sha256 != before.sha256


def test_mtime_reload_does_nothing_when_nothing_changed(doctrine_dir):
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", metrics=Metrics())
    assert store.get() is store.get()


def test_mtime_reload_picks_up_an_edit_inside_a_subfolder(doctrine_dir):
    """The shipped layout is scope folders (`core/`, `rooms/`, `instruments/`),
    not a flat directory — a hot-reload that only fingerprinted the top level
    would never notice an operator's edit to `nora/doctrine/rooms/conduct.md`
    on `norty`, where an edit to `nora/doctrine` IS a deploy. Same shape as
    `test_mtime_reload_picks_up_an_edit_without_a_restart`, one folder down."""
    sub = doctrine_dir / "rooms"
    sub.mkdir()
    write(sub, "conduct.md", name="conduct", order=30, version=1,
          kind="conduct", body="Original conduct.")
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", metrics=Metrics())
    before = store.get()
    assert "Original conduct." in before.text

    _touch_newer(
        sub / "conduct.md",
        (sub / "conduct.md").read_text().replace("Original conduct.", "Edited conduct."),
    )
    after = store.get()
    assert "Edited conduct." in after.text
    assert after.sha256 != before.sha256


def test_a_new_file_in_a_new_subfolder_is_picked_up(doctrine_dir):
    """A file added under a scope folder that did not exist yet at store
    construction time — the mtime watcher fingerprints by glob, not by a
    directory list taken once, so a brand-new `instruments/` subfolder is
    noticed exactly like a brand-new file at the top level
    (`test_a_new_file_is_picked_up`)."""
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", metrics=Metrics())
    store.get()
    sub = doctrine_dir / "instruments"
    sub.mkdir()
    write(sub, "reaction.md", name="reaction", order=6, applies="reaction",
          kind="instrument", body="New reaction instrument.")
    # Its own audience ("reaction"), not the default "tag" `get()` — a new
    # instrument file was never going to reach a tag turn; the reload is what
    # this test measures, so ask for the one audience that would carry it.
    assert "New reaction instrument." in store.get(applies_to="reaction").text


def test_a_new_file_is_picked_up(doctrine_dir):
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", metrics=Metrics())
    store.get()
    write(doctrine_dir, "conduct.md", name="conduct", order=30, body="New conduct.")
    assert "New conduct." in store.get().text


def test_a_deleted_file_is_picked_up(doctrine_dir):
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", metrics=Metrics())
    store.get()
    (doctrine_dir / "voice.md").unlink()
    assert "Original voice." not in store.get().text


def test_startup_mode_ignores_later_edits(doctrine_dir):
    store = DoctrineStore(doctrine_dir, canary="c", reload="startup", metrics=Metrics())
    store.get()
    _touch_newer(
        doctrine_dir / "voice.md",
        (doctrine_dir / "voice.md").read_text().replace("Original voice.", "Edited voice."),
    )
    assert "Original voice." in store.get().text


def test_failed_reload_keeps_last_good(doctrine_dir, caplog):
    store = DoctrineStore(
        doctrine_dir, canary="c", reload="mtime", strict=True, metrics=Metrics(),
    )
    good = store.get()

    _touch_newer(doctrine_dir / "voice.md", "totally broken, no frontmatter")
    after = store.get()

    assert after.sha256 == good.sha256
    assert "Original voice." in after.text
    assert store.reload_failures == 1
    assert any("doctrine_reload_failed" in r.message for r in caplog.records)


def test_a_failed_reload_increments_the_prometheus_counter(doctrine_dir):
    metrics = Metrics()
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", strict=True, metrics=metrics)
    store.get()

    _touch_newer(doctrine_dir / "voice.md", "totally broken, no frontmatter")
    store.get()

    assert store.reload_failures == 1
    assert metrics.get("nora_doctrine_reload_failures_total") == 1


def test_a_repaired_file_reloads_after_a_failure(doctrine_dir):
    store = DoctrineStore(doctrine_dir, canary="c", reload="mtime", metrics=Metrics())
    store.get()
    _touch_newer(doctrine_dir / "voice.md", "broken")
    store.get()
    _touch_newer(
        doctrine_dir / "voice.md",
        "---\nname: voice\ndescription: d\norder: 20\napplies_to: [tag]\nversion: 2\n"
        "kind: identity\nauthority_above: []\nauthority_below: []\n"
        "not_here: anything else\nchange_policy: operator-only\n"
        "---\nFixed voice.",
    )
    assert "Fixed voice." in store.get().text
    assert store.reload_failures == 1


def test_the_first_load_still_raises_because_there_is_no_last_good(tmp_path):
    from nora.doctrine.loader import DoctrineError

    d = tmp_path / "doctrine"
    d.mkdir()
    (d / "identity.md").write_text("broken")
    with pytest.raises(DoctrineError):
        DoctrineStore(d, canary="c", metrics=Metrics()).get()
