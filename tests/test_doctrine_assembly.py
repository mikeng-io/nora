from pathlib import Path

import pytest

from nora.obs.metrics import Metrics
from nora.doctrine.loader import DoctrineStore
from tests.test_doctrine_loader import write  # reuse the frontmatter writer

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "nora" / "doctrine"
AT_RENAME = ROOT / "tests" / "fixtures" / "doctrine_assembled_at_rename.txt"


def test_the_rename_changed_only_the_wrapper_tag():
    """DEC-29 commit 1: `nora/persona/` -> `nora/doctrine/`, with PROOF that not
    one byte of what the model reads moved with it.

    The fixture is the assembled block as it stood at b0a460c, captured before
    the rename ran. Every file was renamed on disk, every class and every
    frontmatter-adjacent identifier changed — and the only difference the model
    can observe is the four characters of the wrapper tag. Section `name`s are
    still `soul`/`guardrails` here on purpose; they follow in the frontmatter
    commit, which rewrites the section tag wholesale anyway.

    This test is the baseline the two behavioural commits after it are measured
    against, and it is deliberately a whole-text comparison rather than a hash:
    when it does change, the diff is the review.
    """
    expected = AT_RENAME.read_text(encoding="utf-8")
    expected = (expected.replace("<nora_persona>", "<nora_doctrine>")
                        .replace("</nora_persona>", "</nora_doctrine>"))
    shipped = DoctrineStore(SHIPPED, canary="C", strict=True, metrics=Metrics()).get()
    assert shipped.doctrine_text == expected


@pytest.fixture
def doctrine_dir(tmp_path):
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "identity.md", name="soul", order=10, version=3, body="Who you are.")
    write(d, "voice.md", name="voice", order=20, version=2, body="The sound.")
    write(d, "constraints.md", name="guardrails", order=40, version=1, body="Hard rules.")
    return d


def _store(d) -> DoctrineStore:
    return DoctrineStore(d, canary="CANARY-XYZ", display_name="Nora",
                        trigger="nora.000", language_hint="hk-canto-en-traditional",
                        metrics=Metrics())


def test_assembly_wraps_each_section_with_name_and_version(doctrine_dir):
    text = _store(doctrine_dir).get().text
    assert '<section name="soul" version="3">' in text
    assert '<section name="voice" version="2">' in text
    assert text.count("</section>") == 3


def test_assembly_is_wrapped_in_nora_doctrine(doctrine_dir):
    text = _store(doctrine_dir).get().text
    assert text.startswith("<nora_doctrine>")
    assert "</nora_doctrine>" in text


def test_bodies_are_inserted_verbatim(doctrine_dir):
    text = _store(doctrine_dir).get().text
    assert "Who you are." in text and "Hard rules." in text


def test_guardrails_is_last_in_the_doctrine_block(doctrine_dir):
    doctrine = _store(doctrine_dir).get()
    block = doctrine.doctrine_text
    assert block.index("Hard rules.") > block.index("The sound.") > block.index("Who you are.")


def test_runtime_context_is_appended_after_the_doctrine_block(doctrine_dir):
    text = _store(doctrine_dir).get().text
    assert text.index("<runtime_context>") > text.index("</nora_doctrine>")
    assert "display_name: Nora" in text
    assert "trigger: nora.000" in text
    assert "CANARY-XYZ" in text


def test_sha256_excludes_runtime_context(doctrine_dir):
    """RK-6: the hash is doctrine identity, not deployment context."""
    a = DoctrineStore(doctrine_dir, canary="C1", trigger="nora.000", metrics=Metrics()).get()
    b = DoctrineStore(doctrine_dir, canary="C2", trigger="other.999", metrics=Metrics()).get()
    assert a.sha256 == b.sha256
    assert a.text != b.text


def test_assembly_determinism_across_store_instances(doctrine_dir):
    a = _store(doctrine_dir).get()
    b = _store(doctrine_dir).get()
    assert a.sha256 == b.sha256
    assert a.text == b.text


def test_editing_a_body_changes_the_sha256(doctrine_dir):
    before = _store(doctrine_dir).get().sha256
    write(doctrine_dir, "voice.md", name="voice", order=20, version=3, body="A new sound.")
    assert _store(doctrine_dir).get().sha256 != before


def test_versions_map_is_exposed_for_logging(doctrine_dir):
    assert _store(doctrine_dir).get().versions() == {"soul": 3, "voice": 2, "guardrails": 1}


def test_blocks_returns_one_system_content_block_per_section(doctrine_dir):
    blocks = _store(doctrine_dir).get().blocks()
    assert len(blocks) == 4          # 3 sections + runtime_context
    assert all(set(b) == {"text"} for b in blocks)


def test_get_is_cached_between_calls_when_nothing_changed(doctrine_dir):
    store = _store(doctrine_dir)
    assert store.get() is store.get()


def test_loaded_at_is_populated(doctrine_dir):
    assert _store(doctrine_dir).get().loaded_at > 0
