import re
from pathlib import Path

import pytest

from nora.doctrine.loader import DoctrineStore
from nora.obs.metrics import Metrics
from tests.test_doctrine_loader import write  # reuse the frontmatter writer

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "nora" / "doctrine"
FIXTURES = ROOT / "tests" / "fixtures"
_SECTION_RE = re.compile(r"<section[^>]*>\n(.*?)\n</section>", re.DOTALL)


def _bodies(assembled: str) -> list[str]:
    return _SECTION_RE.findall(assembled)


# No golden of the shipped doctrine is kept: it would be a second copy of files
# that hot-reload, regenerated every time the operator edits `voice.md`, which is
# the friction DEC-29 avoids. `test_doctrine_content.py` asserts WHERE each line
# landed instead.


def test_without_a_generated_block_there_is_no_capability_section_at_all():
    """Never a placeholder, and never a stale one. If nothing generated it, the
    prompt says nothing about capability rather than something out of date.
    DEC-150: nothing generates it, so this is the always-true state, and the
    test is the invariant that keeps it that way."""
    doctrine = DoctrineStore(SHIPPED, canary="C", strict=True, metrics=Metrics()).get()
    assert [s.name for s in doctrine.sections] == \
        ["identity", "belonging", "voice", "conduct", "pictures", "constraints",
         "room-constraints"]


def test_version_is_not_paid_for_on_every_call():
    """`version` is build metadata and does not belong in the prompt.

    It stays in frontmatter — `Doctrine.versions()` reads it for the log line
    that says which doctrine answered a tag — but is not sent to the model.
    There is nothing she can do with it except reason about it.
    """
    shipped = DoctrineStore(SHIPPED, canary="C", strict=True, metrics=Metrics()).get()
    assert "version=" not in shipped.doctrine_text
    assert shipped.versions()          # still available off the prompt path


@pytest.fixture
def doctrine_dir(tmp_path):
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "identity.md", name="soul", order=10, version=3, body="Who you are.",
          desc="Who she is")
    write(d, "voice.md", name="voice", order=20, version=2, body="The sound.",
          desc="How she sounds")
    write(d, "constraints.md", name="guardrails", order=40, version=1, body="Hard rules.",
          kind="constraint", desc='Hard rules — "never", and it means it')
    return d


def _store(d) -> DoctrineStore:
    return DoctrineStore(d, canary="CANARY-XYZ", display_name="Nora",
                        trigger="nora.000", language_hint="hk-canto-en-traditional",
                        metrics=Metrics())


def test_assembly_wraps_each_section_with_its_kind_and_description(doctrine_dir):
    text = _store(doctrine_dir).get().text
    assert '<section kind="identity" name="soul" description="Who she is">' in text
    assert '<section kind="identity" name="voice" description="How she sounds">' in text
    assert text.count("</section>") == 3


def test_a_description_containing_a_quote_cannot_break_out_of_the_attribute(doctrine_dir):
    """R-13's habit, applied to operator-authored text that is not an attack surface.

    The cost of escaping here is nothing; the cost of the habit of not escaping
    a value on its way into an attribute is what R-13 exists for.
    """
    text = _store(doctrine_dir).get().text
    assert 'description="Hard rules — &quot;never&quot;, and it means it">' in text
    assert text.count("<section ") == 3


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


def test_get_is_cached_between_calls_when_nothing_changed(doctrine_dir):
    store = _store(doctrine_dir)
    assert store.get() is store.get()


def test_loaded_at_is_populated(doctrine_dir):
    assert _store(doctrine_dir).get().loaded_at > 0


def test_the_assembled_doctrine_is_byte_stable(doctrine_dir):
    """A golden of `_assemble`'s output over the fixture corpus.

    Properties-only assertions cannot catch a refactor that changes a single
    newline in `_assemble` — and that newline lands in the live system prompt
    and in `doctrine_sha256`, which every trace's provenance is bound to. The
    golden is over the FIXTURE corpus, never the shipped doctrine: those files
    are operator-edited and bind-mounted, so a golden over them would be red on
    every legitimate edit (see the note at the top of this file). A synthetic
    corpus this file already owns has no such cost — it changes only when
    `doctrine_dir` above changes.
    """
    store = _store(doctrine_dir)
    golden = (FIXTURES / "doctrine_assembly_golden.txt").read_text(encoding="utf-8")
    assert store.get(applies_to="tag").doctrine_text == golden
