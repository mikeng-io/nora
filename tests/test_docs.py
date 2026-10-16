from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_superseded_design_doc_is_gone():
    # `DESIGN.md` names the visual system, so the file may exist; the v1 spec
    # must not come back as it.
    path = ROOT / "DESIGN.md"
    if path.exists():
        text = path.read_text()
        assert "Ambient Signal Group Agent" not in text
        assert "max_per_group" not in text          # the v1 config key


def test_the_readme_describes_v2_not_v1():
    text = (ROOT / "README.md").read_text()
    assert "strands" in text.lower()
    assert "nora/doctrine/" in text
    assert "@Nora" not in text                  # the v1 trigger
    assert "max_per_group" not in text          # the v1 config key


def test_the_readme_documents_the_doctrine_edit_loop():
    text = (ROOT / "README.md").read_text()
    assert "no restart" in text.lower()
    assert "voice.md" in text


def test_the_readme_never_contains_a_secret_value():
    text = (ROOT / "README.md").read_text()
    assert "OMNIROUTE_API_KEY" in text
    assert "sk-" not in text


def test_the_runbook_covers_the_operator_scenarios():
    text = (ROOT / "docs" / "runbook.md").read_text().lower()
    for topic in ("nora is silent", "rollback", "doctrine", "watchdog",
                  "silent failure rate", "restore"):
        assert topic in text
