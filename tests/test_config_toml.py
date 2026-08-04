"""The TOML loader must be observationally identical to the YAML loader it replaces."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from nora.config import load as load_yaml
from nora.config_toml import ConfigError, load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def toml_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    return dst


def test_toml_loader_matches_the_yaml_loader_field_for_field(tmp_path, monkeypatch):
    """The whole point of this task: same values, different format and library."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    yaml_cfg = load_yaml(_copy(ROOT / "config.example.yaml", tmp_path / "config.yaml"))
    toml_cfg = load(_copy(ROOT / "config.example.toml", tmp_path / "config.toml"))

    assert toml_cfg.signal_cli.base_url == yaml_cfg.signal_cli.base_url
    assert toml_cfg.signal_cli.account == yaml_cfg.signal_cli.account
    assert toml_cfg.signal_cli.nora_sender_id == yaml_cfg.signal_cli.nora_sender_id
    assert toml_cfg.honcho.workspace_id == yaml_cfg.honcho.workspace_id
    assert toml_cfg.llm.api_key_env == yaml_cfg.llm.api_key_env
    assert toml_cfg.llm.default.model == yaml_cfg.llm.default.model
    assert toml_cfg.llm.deep.model == yaml_cfg.llm.deep.model
    assert toml_cfg.llm.limits.turns == yaml_cfg.llm.limits.turns
    assert toml_cfg.trigger.text == yaml_cfg.trigger.text
    assert (
        toml_cfg.trigger.invocation_timeout_seconds
        == yaml_cfg.trigger.invocation_timeout_seconds
    )
    assert toml_cfg.buffer.max_per_conversation == yaml_cfg.buffer.max_per_conversation
    assert toml_cfg.buffer.context_window == yaml_cfg.buffer.context_window
    assert toml_cfg.persona.canary == yaml_cfg.persona.canary
    assert toml_cfg.persona.strict == yaml_cfg.persona.strict
    assert toml_cfg.logging.level == yaml_cfg.logging.level
    assert toml_cfg.metrics.port == yaml_cfg.metrics.port
    assert toml_cfg.observability.tracing.exporter == yaml_cfg.observability.tracing.exporter
    # Paths resolve against each file's own directory, so compare the tails.
    assert toml_cfg.buffer.db_path.name == yaml_cfg.buffer.db_path.name
    assert toml_cfg.persona.dir.name == yaml_cfg.persona.dir.name


def _copy(src, dst):
    shutil.copy(src, dst)
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
    assert cfg.buffer.db_path.is_absolute()
    assert "~" not in str(cfg.buffer.db_path)
    assert cfg.logging.path.is_absolute()
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
        cfg.persons_file = Path("/other")


def test_missing_config_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path / "does-not-exist.toml")


def test_malformed_toml_is_a_config_error(toml_path):
    toml_path.write_text("[trigger\ntext = 'unclosed table header'\n")
    with pytest.raises(ConfigError):
        load(toml_path)
