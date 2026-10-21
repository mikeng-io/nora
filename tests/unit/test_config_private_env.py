"""Private source refs resolve once; rejection never prints the private input."""
from __future__ import annotations

import argparse
import importlib.util
import traceback
from pathlib import Path

import pytest
import tomlkit

from nora.config import (
    PRIVATE_ENV_FIELDS,
    ConfigError,
    load,
    validate_schema,
)
from nora.policy import PolicyError, load_policy
from tests.builders import PRIVATE_CONFIG_ENV as ENV

SENTINEL = "SYNTHETIC_REJECTED_PRIVATE_SENTINEL"


def source_refs(path: Path) -> tomlkit.TOMLDocument:
    doc = tomlkit.parse(path.read_text())
    doc["agent"].pop("handles")
    doc["agent"]["handles_env"] = {
        n: f"NORA_HANDLE_{n.upper()}" for n in
        ("signal", "whatsapp", "x", "threads", "discord")
    }
    doc["speech"]["tts"]["sidecars"] = [
        {"guild_env": "NORA_TTS_GUILD_PRIMARY", "endpoint": "http://voice-sidecar:4600"},
        {"guild_env": "NORA_TTS_GUILD_SECONDARY", "endpoint": "http://voice-secondary:4600"},
    ]
    for field, name in PRIVATE_ENV_FIELDS.items():
        if field.startswith("agent.handles.") or ".sidecars." in field:
            continue
        parts = field.split(".")
        node = doc
        for part in parts[:-1]:
            node = node[part]
        node.pop(parts[-1], None)
        node[parts[-1] + "_env"] = name
    path.write_text(tomlkit.dumps(doc))
    return doc


def test_all_fourteen_resolve_without_changing_consumers_or_source(example_config_path):
    path = example_config_path
    source_refs(path)
    before = path.read_bytes()
    cfg = load(path, require_secrets=False, env=ENV)
    assert len(PRIVATE_ENV_FIELDS) == 14
    assert cfg.agent.handles == {
        network: f"@synthetic_{network}"
        for network in ("signal", "whatsapp", "x", "threads", "discord")
    }
    assert cfg.platforms.signal.account == ENV["NORA_SIGNAL_ACCOUNT"]
    assert cfg.social.instagram.summons.handle == "synthetic"
    assert cfg.participation.shadow_channel == ENV["NORA_PARTICIPATION_SHADOW_CHANNEL"]
    assert cfg.relevance_gate.shadow_channel == ENV["NORA_RELEVANCE_SHADOW_CHANNEL"]
    assert cfg.speech.tts.sidecars_by_guild == {
        ENV["NORA_TTS_GUILD_PRIMARY"]: "http://voice-sidecar:4600",
        ENV["NORA_TTS_GUILD_SECONDARY"]: "http://voice-secondary:4600",
    }
    assert cfg.tools.mcp["pulse"].base_url == ENV["NORA_PULSE_BASE_URL"]
    assert cfg.documents.public_url == ENV["NORA_DOCUMENTS_PUBLIC_URL"]
    assert cfg.doctrine.canary == ENV["NORA_DOCTRINE_CANARY"]
    assert path.read_bytes() == before
    # Literal transition config and ref source compute the same typed state.
    doc = tomlkit.parse(path.read_text())
    doc["agent"]["handles"] = dict(cfg.agent.handles)
    del doc["agent"]["handles_env"]
    for field, name in PRIVATE_ENV_FIELDS.items():
        if field.startswith("agent.handles."):
            continue
        node = doc
        parts = field.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdecimal() else node[part]
        del node[parts[-1] + "_env"]
        node[parts[-1]] = ENV[name]
    path.write_text(tomlkit.dumps(doc))
    assert load(path, require_secrets=False, env={}) == cfg
    for name in ("note-candidates", "note-curation", "proactive"):
        assert name in cfg.dreamer.budgets


@pytest.mark.parametrize("name", PRIVATE_ENV_FIELDS.values())
@pytest.mark.parametrize("require_secrets", [False, True])
@pytest.mark.parametrize("value", [None, "", " \t"])
def test_declared_refs_never_fall_back(example_config_path, name, require_secrets, value):
    source_refs(example_config_path)
    env = {**ENV, "OMNIROUTE_API_KEY": "synthetic-key"}
    if value is None:
        del env[name]
    else:
        env[name] = value
    with pytest.raises(ConfigError, match="env_missing_or_empty"):
        load(example_config_path, require_secrets=require_secrets, env=env)


@pytest.mark.parametrize("field", PRIVATE_ENV_FIELDS)
@pytest.mark.parametrize("failure", ["collision", "malformed_ref"])
def test_one_source_and_valid_env_names(example_config_path, field, failure):
    doc = source_refs(example_config_path)
    parts = field.split(".")
    node = doc
    if field.startswith("agent.handles."):
        if failure == "collision":
            doc["agent"]["handles"] = {parts[-1]: SENTINEL}
        else:
            doc["agent"]["handles_env"][parts[-1]] = SENTINEL + "!"
    else:
        for part in parts[:-1]:
            node = node[int(part)] if part.isdecimal() else node[part]
        node[parts[-1] if failure == "collision" else parts[-1] + "_env"] = SENTINEL + "!"
    example_config_path.write_text(tomlkit.dumps(doc))
    with pytest.raises(ConfigError, match=r"literal_ref_collision|env_name") as error:
        load(example_config_path, require_secrets=False, env=ENV)
    assert SENTINEL not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("name,value", [
    ("NORA_SIGNAL_ACCOUNT", SENTINEL),
    ("NORA_HANDLE_SIGNAL", SENTINEL + " "),
    ("NORA_TTS_GUILD_PRIMARY", SENTINEL),
    ("NORA_PARTICIPATION_SHADOW_CHANNEL", SENTINEL),
    ("NORA_RELEVANCE_SHADOW_CHANNEL", SENTINEL),
    ("NORA_PULSE_BASE_URL", "https://" + SENTINEL + "@example.invalid/path"),
    ("NORA_DOCUMENTS_PUBLIC_URL", "https://example.invalid/" + SENTINEL),
])
def test_invalid_private_values_have_safe_errors(example_config_path, name, value):
    source_refs(example_config_path)
    with pytest.raises(ConfigError) as error:
        load(example_config_path, require_secrets=False, env={**ENV, name: value})
    assert SENTINEL not in "".join(traceback.format_exception(error.value))
    assert "input_value" not in str(error.value)


def test_duplicate_resolved_guilds_refused(example_config_path):
    source_refs(example_config_path)
    with pytest.raises(ConfigError, match="validation"):
        load(example_config_path, require_secrets=False, env={
            **ENV, "NORA_TTS_GUILD_SECONDARY": ENV["NORA_TTS_GUILD_PRIMARY"],
        })


def test_schema_only_cannot_supply_runtime_defaults(example_config_path):
    source_refs(example_config_path)
    assert validate_schema(example_config_path) is None
    with pytest.raises(ConfigError, match="env_missing_or_empty"):
        load(example_config_path, require_secrets=False, env={})


@pytest.mark.parametrize("text", [
    SENTINEL + ' = "unknown key"',
    '[units.' + SENTINEL + ']\nworkspace = 123',
    '[[conversations]]\nplatform = "discord"\nid = "' + SENTINEL + '"',
    '[[conversations]]\nplatform = "signal"\nid = "' + SENTINEL + '"\npersons = ["*"]',
    'bad = "' + SENTINEL + '\\q"',
])
def test_policy_never_echoes_keys_values_or_toml_errors(tmp_path, text):
    path = tmp_path / (SENTINEL + ".toml")
    path.write_text(text)
    with pytest.raises(PolicyError, match="policy:") as error:
        load_policy(path)
    assert SENTINEL not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("text", [
    SENTINEL + ' = "unknown"',
    'broken = "' + SENTINEL + '\\q"',
    '[agent.handles.' + SENTINEL + ']\nx = 1',
])
def test_config_never_echoes_unknown_keys_or_parser_errors(example_config_path, text):
    example_config_path.write_text(example_config_path.read_text() + "\n" + text)
    with pytest.raises(ConfigError) as error:
        load(example_config_path, require_secrets=False)
    assert SENTINEL not in "".join(traceback.format_exception(error.value))


@pytest.mark.asyncio
async def test_daemon_safe_exception(example_config_path):
    from nora.__main__ import StartupError, build_app
    example_config_path.write_text(SENTINEL + ' = "bad"')
    with pytest.raises(StartupError) as error:
        await build_app(example_config_path)
    assert SENTINEL not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("entrypoint", ["analytics", "presence", "daemon", "cli"])
def test_entrypoint_output_safe(example_config_path, monkeypatch, capsys, caplog, entrypoint):
    import sys

    import nora.__main__ as daemon
    import nora.analytics.__main__ as analytics
    import nora.presence.__main__ as presence
    source_refs(example_config_path)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NORA_TTS_GUILD_PRIMARY", SENTINEL)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "synthetic-key")
    args = ["nora", "--config", str(example_config_path)]
    module = {"analytics": analytics, "presence": presence}.get(entrypoint, daemon)
    if entrypoint == "cli":
        args = ["nora", "config", "validate", "--config", str(example_config_path)]
    monkeypatch.setattr(sys, "argv", args)
    try:
        result = module.main()
        assert result == 1
    except SystemExit as exc:
        assert exc.code == 1
    out = capsys.readouterr()
    assert SENTINEL not in out.out + out.err + caplog.text


def test_schema_only_cli_has_explicit_unresolved_message(example_config_path, capsys):
    from nora.app.config_cli import _cmd_validate
    source_refs(example_config_path)
    assert _cmd_validate(argparse.Namespace(config=example_config_path, schema_only=True)) == 0
    assert capsys.readouterr().out == "schema valid; runtime env unresolved\n"


@pytest.mark.parametrize("service", ["nora", "presence", "analytics"])
def test_all_full_loaders_receive_every_private_env(service):
    import yaml
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    entries = compose["services"][service]["environment"]
    for name in PRIVATE_ENV_FIELDS.values():
        assert f"{name}=${{{name}:-}}" in entries


@pytest.mark.parametrize("service", ["analytics", "presence"])
def test_service_specific_environment_cannot_borrow_noras_refs(example_config_path, service):
    source_refs(example_config_path)
    assert load(example_config_path, require_secrets=False, env=ENV)
    service_env = dict(ENV)
    del service_env["NORA_HANDLE_SIGNAL"]
    with pytest.raises(ConfigError, match=r"agent\.handles\.signal: env_missing_or_empty"):
        load(example_config_path, require_secrets=False, env=service_env)


def test_wrapper_suppresses_subprocess_private_error(capsys):
    import sys
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "komodo_deploy", root / "scripts/komodo_deploy.py",
    )
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    command = [sys.executable, "-c",
               f"import sys; print({SENTINEL!r}, file=sys.stderr); sys.exit(1)"]
    with pytest.raises(RuntimeError) as error:
        wrapper.run(command, output=True)
    out = capsys.readouterr()
    assert SENTINEL not in str(error.value) + out.out + out.err


@pytest.mark.parametrize("reader", ["deploy/stt/config_toml.py",
                                    "deploy/voice-wake/wakegate/config_toml.py"])
def test_speech_readers_refuse_missing_and_malformed_files(tmp_path, reader):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("speech_config", root / reader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    read = getattr(module, "preload_model", None) or module.load_sections
    path = tmp_path / (SENTINEL + ".toml")
    with pytest.raises(RuntimeError, match="config: unreadable"):
        read(path)
    path.write_text('bad = "' + SENTINEL + '\\q"')
    with pytest.raises(RuntimeError, match="config: invalid_toml") as error:
        read(path)
    assert SENTINEL not in "".join(traceback.format_exception(error.value))
