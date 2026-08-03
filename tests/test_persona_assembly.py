import pytest

from nora.persona.loader import PersonaStore
from tests.test_persona_loader import write  # reuse the frontmatter writer


@pytest.fixture
def persona_dir(tmp_path):
    d = tmp_path / "persona"
    d.mkdir()
    write(d, "soul.md", name="soul", order=10, version=3, body="Who you are.")
    write(d, "voice.md", name="voice", order=20, version=2, body="The sound.")
    write(d, "guardrails.md", name="guardrails", order=40, version=1, body="Hard rules.")
    return d


def _store(d) -> PersonaStore:
    return PersonaStore(d, canary="CANARY-XYZ", display_name="Nora",
                        trigger="nora.000", language_hint="hk-canto-en-traditional")


def test_assembly_wraps_each_section_with_name_and_version(persona_dir):
    text = _store(persona_dir).get().text
    assert '<section name="soul" version="3">' in text
    assert '<section name="voice" version="2">' in text
    assert text.count("</section>") == 3


def test_assembly_is_wrapped_in_nora_persona(persona_dir):
    text = _store(persona_dir).get().text
    assert text.startswith("<nora_persona>")
    assert "</nora_persona>" in text


def test_bodies_are_inserted_verbatim(persona_dir):
    text = _store(persona_dir).get().text
    assert "Who you are." in text and "Hard rules." in text


def test_guardrails_is_last_in_the_persona_block(persona_dir):
    persona = _store(persona_dir).get()
    block = persona.persona_text
    assert block.index("Hard rules.") > block.index("The sound.") > block.index("Who you are.")


def test_runtime_context_is_appended_after_the_persona_block(persona_dir):
    text = _store(persona_dir).get().text
    assert text.index("<runtime_context>") > text.index("</nora_persona>")
    assert "display_name: Nora" in text
    assert "trigger: nora.000" in text
    assert "CANARY-XYZ" in text


def test_sha256_excludes_runtime_context(persona_dir):
    """RK-6: the hash is persona identity, not deployment context."""
    a = PersonaStore(persona_dir, canary="C1", trigger="nora.000").get()
    b = PersonaStore(persona_dir, canary="C2", trigger="other.999").get()
    assert a.sha256 == b.sha256
    assert a.text != b.text


def test_assembly_determinism_across_store_instances(persona_dir):
    a = _store(persona_dir).get()
    b = _store(persona_dir).get()
    assert a.sha256 == b.sha256
    assert a.text == b.text


def test_editing_a_body_changes_the_sha256(persona_dir):
    before = _store(persona_dir).get().sha256
    write(persona_dir, "voice.md", name="voice", order=20, version=3, body="A new sound.")
    assert _store(persona_dir).get().sha256 != before


def test_versions_map_is_exposed_for_logging(persona_dir):
    assert _store(persona_dir).get().versions() == {"soul": 3, "voice": 2, "guardrails": 1}


def test_blocks_returns_one_system_content_block_per_section(persona_dir):
    blocks = _store(persona_dir).get().blocks()
    assert len(blocks) == 4          # 3 sections + runtime_context
    assert all(set(b) == {"text"} for b in blocks)


def test_get_is_cached_between_calls_when_nothing_changed(persona_dir):
    store = _store(persona_dir)
    assert store.get() is store.get()


def test_loaded_at_is_populated(persona_dir):
    assert _store(persona_dir).get().loaded_at > 0
