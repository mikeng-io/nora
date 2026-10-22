"""config.toml is the one source for the wake gate's model knobs.

Pins: (1) `[speech.wake]` + `[speech.stt]` values reach `Config`; (2) a missing
file refuses startup while a missing section retains defaults; (3) env
does NOT override a config.toml knob (compose `STT_MODEL=sensevoice` must not
diverge from the example's `noise-detection`);
(4) a malformed file raises instead of silently defaulting.
"""

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def wake_config(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "deploy/voice-wake/wakegate"
    name = "synthetic_wakegate"
    package_spec = importlib.util.spec_from_file_location(
        name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    package = importlib.util.module_from_spec(package_spec)
    monkeypatch.setitem(sys.modules, name, package)
    package_spec.loader.exec_module(package)
    spec = importlib.util.spec_from_file_location(name + ".__main__", root / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_config, module.Config


def test_toml_values_reach_config(tmp_path: Path, wake_config) -> None:
    build_config, _config_type = wake_config
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[speech.stt]\nendpoint = 'http://stt-b:8000'\nlanguage = 'auto'\ntimeout_seconds = 12.5\n"
        "[speech.wake]\nthreshold = 0.42\nmodel_path = '/app/models/x.onnx'\n"
        "stt_model = 'noise-detection'\n"
        "silence_tail_ms = 900\nmax_utterance_s = 6.0\nsilence_rms = 250\n"
        "vad_enabled = false\nvad_threshold = 0.7\n",
        encoding="utf-8",
    )
    cfg = build_config(cfg_file, env={})
    assert (cfg.threshold, cfg.model_path) == (0.42, "/app/models/x.onnx")
    assert cfg.stt_model == "noise-detection"
    assert (cfg.stt_url, cfg.stt_language, cfg.stt_timeout_s) == ("http://stt-b:8000", "auto", 12.5)
    assert (cfg.silence_tail_ms, cfg.max_utterance_s, cfg.silence_rms) == (900, 6.0, 250.0)
    assert (cfg.vad_enabled, cfg.vad_threshold) == (False, 0.7)
    # NO deployment identity on Config: the guild arrives per connection in the
    # sidecar's hello, so one gate serves every guild and nothing per-guild is
    # configured.
    assert not hasattr(cfg, "guild_id")
    assert not hasattr(cfg, "conversation_id")


def test_missing_file_refuses_start_but_missing_section_keeps_defaults(
    tmp_path: Path, wake_config
) -> None:
    build_config, _config_type = wake_config
    d = _config_type()
    with pytest.raises(RuntimeError, match="config: unreadable"):
        build_config(tmp_path / "nope.toml", env={})
    partial = tmp_path / "config.toml"
    partial.write_text("[speech.stt]\nendpoint = 'http://stt:8000'\n", encoding="utf-8")
    cfg = build_config(partial, env={})
    assert (cfg.threshold, cfg.model_path) == (d.threshold, d.model_path)
    assert cfg.silence_tail_ms == d.silence_tail_ms


def test_env_no_longer_overrides_model_knobs(tmp_path: Path, wake_config) -> None:
    build_config, _config_type = wake_config
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[speech.wake]\nthreshold = 0.6\nstt_model = 'sensevoice'\n", encoding="utf-8"
    )
    stale_env = {"WAKE_THRESHOLD": "0.1", "STT_MODEL": "noise-detection"}
    cfg = build_config(cfg_file, env=stale_env)
    assert cfg.threshold == 0.6
    assert cfg.stt_model == "sensevoice"


def test_env_still_owns_identity_and_binds(tmp_path: Path, wake_config) -> None:
    build_config, _config_type = wake_config
    env = {"WAKE_LISTEN_PORT": "4700", "NORA_INGEST_URL": "http://n:1"}
    path = tmp_path / "config.toml"
    path.write_text("")
    cfg = build_config(path, env=env)
    assert (cfg.listen_port, cfg.nora_url) == (4700, "http://n:1")


def test_malformed_config_raises(tmp_path: Path, wake_config) -> None:
    build_config, _config_type = wake_config
    bad = tmp_path / "config.toml"
    bad.write_text("[speech.wake\nthreshold = ", encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017 — any parse error must surface, never default
        build_config(bad, env={})


def test_code_defaults_equal_the_committed_example_defaults(
    wake_config, example_config_path
) -> None:
    _build_config, _config_type = wake_config
    """A live config.toml without [speech.wake] runs on the CODE defaults; the example
    file is the operator's record of those defaults; if they diverge the wake path
    silently takes a different engine. Every key in the example's [speech.wake] must equal
    Config()."""
    import tomllib

    wake = tomllib.loads(example_config_path.read_text(encoding="utf-8"))["speech"]["wake"]
    d = _config_type()
    mapping = {
        "threshold": d.threshold,
        "model_path": d.model_path,
        "stt_model": d.stt_model,
        "vad_enabled": d.vad_enabled,
        "vad_threshold": d.vad_threshold,
        "silence_tail_ms": d.silence_tail_ms,
        "max_utterance_s": d.max_utterance_s,
        "silence_rms": d.silence_rms,
    }
    for key, code_default in mapping.items():
        assert wake[key] == code_default, (
            f"[speech.wake].{key}: example {wake[key]!r} != code {code_default!r}"
        )
