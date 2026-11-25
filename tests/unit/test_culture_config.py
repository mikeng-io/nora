"""Startup validation of the Culture compatibility settings."""

import tomllib

import pytest
import tomli_w

from nora.config import ConfigError, load


def test_an_unknown_culture_key_is_refused(example_config_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    data = tomllib.loads(example_config_path.read_text())
    data.setdefault("culture", {})["unknown_culture_key"] = True
    example_config_path.write_text(tomli_w.dumps(data))

    with pytest.raises(ConfigError, match=r"culture\.unknown_culture_key"):
        load(example_config_path)
