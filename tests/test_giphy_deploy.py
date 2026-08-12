from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_passes_giphy_key_to_nora_container():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "GIPHY_API_KEY=${GIPHY_API_KEY:-}" in compose


def test_example_environment_documents_giphy_key():
    example = (ROOT / ".env.example").read_text()
    assert "GIPHY_API_KEY=your-key-here" in example
