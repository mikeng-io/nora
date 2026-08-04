"""Frozen config tree (spec D.1), pydantic loader over config.toml."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from nora.config import ConfigError, load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def toml_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    return dst


def test_unknown_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text() + '\n[tagg]\ntrigger = "x"\n')
    with pytest.raises(ConfigError, match="tagg"):
        load(toml_path)


def test_unknown_nested_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'text = "nora.000"', 'text = "nora.000"\ntriger = "oops"'))
    with pytest.raises(ConfigError, match="triger"):
        load(toml_path)


def test_missing_required_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace('text = "nora.000"\n', ""))
    with pytest.raises(ConfigError, match="text"):
        load(toml_path)


def test_tilde_expands_and_paths_are_absolute(toml_path):
    cfg = load(toml_path)
    assert cfg.store.db_path.is_absolute()
    assert "~" not in str(cfg.store.db_path)
    assert cfg.obs.logging.path.is_absolute()
    assert cfg.persona.dir.is_absolute()


def test_relative_paths_resolve_against_the_config_file_directory(toml_path):
    cfg = load(toml_path)
    assert cfg.persona.dir == (toml_path.parent / "nora" / "persona").resolve()


def test_missing_env_secret_is_a_startup_failure(toml_path, monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OMNIROUTE_API_KEY"):
        load(toml_path)


def test_config_never_holds_the_secret_value(toml_path):
    cfg = load(toml_path)
    assert cfg.llm.api_key_env == "OMNIROUTE_API_KEY"
    assert "test-key" not in repr(cfg)


def test_invalid_persona_reload_mode_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'reload = "mtime"', 'reload = "inotify"'))
    with pytest.raises(ConfigError, match="reload"):
        load(toml_path)


def test_invalid_tracing_exporter_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'exporter = "file"', 'exporter = "jaeger"'))
    with pytest.raises(ConfigError, match="exporter"):
        load(toml_path)


def test_otlp_exporter_without_an_endpoint_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'exporter = "file"', 'exporter = "otlp"'))
    with pytest.raises(ConfigError, match="endpoint"):
        load(toml_path)


def test_config_is_immutable(toml_path):
    cfg = load(toml_path)
    with pytest.raises(ValidationError):
        cfg.identity_file = Path("/other")


def test_missing_config_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path / "does-not-exist.toml")


def test_malformed_toml_is_a_config_error(toml_path):
    toml_path.write_text("[engagement.trigger\ntext = 'unclosed table header'\n")
    with pytest.raises(ConfigError):
        load(toml_path)


def test_config_uses_the_target_shape(toml_path):
    cfg = load(toml_path)
    # platforms replaces signal_cli, keyed by adapter name
    assert cfg.platforms.signal.base_url.startswith("http")
    assert cfg.platforms.signal.enabled is True
    assert cfg.platforms.signal.nora_sender_id == "nora"
    # memory replaces honcho
    assert cfg.memory.workspace_id == "nora"
    # store replaces buffer
    assert cfg.store.max_per_conversation == 500
    assert cfg.store.context_window == 40
    # tiers replace default/deep, and carry their own timeout
    assert cfg.llm.tiers.flash.model == "deepseek-v4-flash"
    assert cfg.llm.tiers.deep.model == "deepseek-v4-pro"
    assert cfg.llm.tiers.flash.timeout_seconds == 30
    assert cfg.llm.tiers.flash.limits.turns == 4
    # engagement owns the trigger; policy is declared even though only one exists
    assert cfg.engagement.policy == "trigger_text"
    assert cfg.engagement.trigger.text == "nora.000"
    assert cfg.engagement.trigger.min_interval_seconds == 5
    # obs groups the three signals
    assert cfg.obs.metrics.port == 9100
    assert cfg.obs.tracing.exporter == "file"
    assert cfg.obs.logging.level == "INFO"
    # identity_file replaces persons_file
    assert cfg.identity_file.name == "identity.toml"
