"""The templates are contracts: config.py's parser is written against config.example.toml."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Matches `api_key` used as a bare key (any amount of whitespace, or none, before `=`),
# e.g. `api_key = "sk-..."` or the whitespace-hole `api_key="sk-..."`. Deliberately does
# NOT match `api_key_env`, which is the only form allowed in the committed template.
_BARE_API_KEY_RE = re.compile(r"\bapi_key\s*=")


def test_config_example_exists_and_parses():
    data = tomllib.loads((ROOT / "config.example.toml").read_text())
    assert set(data) == {
        "agent",
        "platforms", "llm", "memory", "store", "media", "route", "presence",
        "search", "weather", "preview", "heartbeat", "reminders",
        "engagement", "doctrine", "identity_file", "policy_file", "obs", "evals",
        "tools",
    }


def test_config_example_has_no_secret_values():
    text = (ROOT / "config.example.toml").read_text()
    assert "api_key_env" in text
    # Only the env var NAME (api_key_env) may ever appear in the committed config — a
    # real key value (`api_key = "..."` or the no-space `api_key="..."`, both valid
    # TOML) must never appear on any line.
    for line in text.splitlines():
        assert not _BARE_API_KEY_RE.search(line), f"secret-shaped key found: {line!r}"


def test_env_example_names_the_secret():
    assert "OMNIROUTE_API_KEY" in (ROOT / ".env.example").read_text()
