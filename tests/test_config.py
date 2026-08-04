import shutil
from pathlib import Path

import pytest
import yaml

from nora.config import ConfigError, load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", dst)
    return dst


def _edit(path: Path, mutate) -> Path:
    data = yaml.safe_load(path.read_text())
    mutate(data)
    path.write_text(yaml.safe_dump(data))
    return path


def test_loads_the_example_config(cfg_path):
    cfg = load(cfg_path)
    assert cfg.trigger.text == "nora.000"
    assert cfg.trigger.min_interval_seconds == 5
    assert cfg.trigger.invocation_timeout_seconds == 30
    assert cfg.llm.default.model == "deepseek-v4-flash"
    assert cfg.llm.deep.model == "deepseek-v4-pro"
    assert cfg.llm.limits.turns == 4
    assert cfg.llm.limits.total_tokens == 24000
    assert cfg.buffer.max_per_conversation == 500
    assert cfg.persona.reload == "mtime"
    assert cfg.persona.strict is True
    assert cfg.observability.tracing.enabled is True


def test_unknown_top_level_key_is_a_startup_failure(cfg_path):
    _edit(cfg_path, lambda d: d.update(tagg={"trigger": "nora.000"}))
    with pytest.raises(ConfigError, match=r"unknown config key.*tagg"):
        load(cfg_path)


def test_unknown_nested_key_is_a_startup_failure(cfg_path):
    _edit(cfg_path, lambda d: d["trigger"].update(triger="oops"))
    with pytest.raises(ConfigError, match=r"unknown config key.*trigger\.triger"):
        load(cfg_path)


def test_missing_required_key_is_a_startup_failure(cfg_path):
    _edit(cfg_path, lambda d: d["trigger"].pop("text"))
    with pytest.raises(ConfigError, match=r"missing config key.*trigger\.text"):
        load(cfg_path)


def test_tilde_expands_and_paths_are_absolute(cfg_path):
    cfg = load(cfg_path)
    assert cfg.buffer.db_path.is_absolute()
    assert "~" not in str(cfg.buffer.db_path)
    assert cfg.logging.path.is_absolute()
    assert cfg.persona.dir.is_absolute()


def test_relative_paths_resolve_against_the_config_file_directory(cfg_path):
    cfg = load(cfg_path)
    assert cfg.persona.dir == (cfg_path.parent / "nora" / "persona").resolve()


def test_missing_env_secret_is_a_startup_failure(cfg_path, monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OMNIROUTE_API_KEY"):
        load(cfg_path)


def test_config_never_holds_the_secret_value(cfg_path):
    cfg = load(cfg_path)
    assert cfg.llm.api_key_env == "OMNIROUTE_API_KEY"
    assert "test-key" not in repr(cfg)


def test_invalid_persona_reload_mode_is_a_startup_failure(cfg_path):
    _edit(cfg_path, lambda d: d["persona"].update(reload="inotify"))
    with pytest.raises(ConfigError, match=r"persona\.reload"):
        load(cfg_path)


def test_invalid_tracing_exporter_is_a_startup_failure(cfg_path):
    _edit(cfg_path, lambda d: d["observability"]["tracing"].update(exporter="jaeger"))
    with pytest.raises(ConfigError, match=r"tracing\.exporter"):
        load(cfg_path)


def test_otlp_exporter_without_an_endpoint_is_a_startup_failure(cfg_path):
    _edit(cfg_path, lambda d: d["observability"]["tracing"].update(
        exporter="otlp", endpoint=""))
    with pytest.raises(ConfigError, match=r"tracing\.endpoint"):
        load(cfg_path)


def test_config_is_frozen(cfg_path):
    import dataclasses

    cfg = load(cfg_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.persons_file = Path("/other")  # type: ignore[misc]
