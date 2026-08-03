import pytest

from nora.persona.loader import PersonaStore
from tests.test_persona_loader import write


@pytest.fixture
def persona_dir(tmp_path):
    d = tmp_path / "persona"
    d.mkdir()
    write(d, "soul.md", name="soul", order=10, version=1, body="Original soul.")
    write(d, "voice.md", name="voice", order=20, version=1, body="Original voice.")
    return d


def _touch_newer(path, text):
    """Rewrite with a bumped mtime so the fingerprint changes on fast filesystems."""
    import os
    import time

    path.write_text(text)
    future = time.time() + 2
    os.utime(path, (future, future))


def test_mtime_reload_picks_up_an_edit_without_a_restart(persona_dir):
    store = PersonaStore(persona_dir, canary="c", reload="mtime")
    before = store.get()
    assert "Original voice." in before.text

    _touch_newer(
        persona_dir / "voice.md",
        (persona_dir / "voice.md").read_text().replace("Original voice.", "Edited voice."),
    )
    after = store.get()
    assert "Edited voice." in after.text
    assert after.sha256 != before.sha256


def test_mtime_reload_does_nothing_when_nothing_changed(persona_dir):
    store = PersonaStore(persona_dir, canary="c", reload="mtime")
    assert store.get() is store.get()


def test_a_new_file_is_picked_up(persona_dir):
    store = PersonaStore(persona_dir, canary="c", reload="mtime")
    store.get()
    write(persona_dir, "conduct.md", name="conduct", order=30, body="New conduct.")
    assert "New conduct." in store.get().text


def test_a_deleted_file_is_picked_up(persona_dir):
    store = PersonaStore(persona_dir, canary="c", reload="mtime")
    store.get()
    (persona_dir / "voice.md").unlink()
    assert "Original voice." not in store.get().text


def test_startup_mode_ignores_later_edits(persona_dir):
    store = PersonaStore(persona_dir, canary="c", reload="startup")
    store.get()
    _touch_newer(
        persona_dir / "voice.md",
        (persona_dir / "voice.md").read_text().replace("Original voice.", "Edited voice."),
    )
    assert "Original voice." in store.get().text


def test_failed_reload_keeps_last_good(persona_dir, caplog):
    store = PersonaStore(persona_dir, canary="c", reload="mtime", strict=True)
    good = store.get()

    _touch_newer(persona_dir / "voice.md", "totally broken, no frontmatter")
    after = store.get()

    assert after.sha256 == good.sha256
    assert "Original voice." in after.text
    assert store.reload_failures == 1
    assert any("persona_reload_failed" in r.message for r in caplog.records)


def test_a_repaired_file_reloads_after_a_failure(persona_dir):
    store = PersonaStore(persona_dir, canary="c", reload="mtime")
    store.get()
    _touch_newer(persona_dir / "voice.md", "broken")
    store.get()
    _touch_newer(
        persona_dir / "voice.md",
        "---\nname: voice\ndescription: d\norder: 20\napplies_to: [tag]\nversion: 2\n---\nFixed voice.",
    )
    assert "Fixed voice." in store.get().text
    assert store.reload_failures == 1


def test_the_first_load_still_raises_because_there_is_no_last_good(tmp_path):
    from nora.persona.loader import PersonaError

    d = tmp_path / "persona"
    d.mkdir()
    (d / "soul.md").write_text("broken")
    with pytest.raises(PersonaError):
        PersonaStore(d, canary="c").get()
