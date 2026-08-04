"""The templates are contracts: config.py's parser is written against config.example.toml."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_config_example_exists_and_parses():
    data = tomllib.loads((ROOT / "config.example.toml").read_text())
    assert set(data) == {
        "signal_cli", "honcho", "llm", "trigger", "buffer",
        "persona", "persons_file", "logging", "metrics", "observability",
    }


def test_config_example_has_no_secret_values():
    text = (ROOT / "config.example.toml").read_text()
    assert "api_key_env" in text
    assert "api_key =" not in text  # only the env var NAME is ever in config


def test_env_example_names_the_secret():
    assert "OMNIROUTE_API_KEY" in (ROOT / ".env.example").read_text()
