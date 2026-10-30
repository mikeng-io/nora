"""Synthetic credential custody and endpoint migration checks."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from nora.config import Config, ConfigError, load
from nora.secret import SecretError, read, require, resolve, write
from scripts.secret_delivery import migrate, prepare, projection, source


def test_absent_empty_override_and_private_writer(tmp_path):
    path = tmp_path / "secret.toml"
    write(path, "CUSTOM_KEY", "synthetic-canary")
    assert resolve(path, {})["CUSTOM_KEY"] == "synthetic-canary"
    assert resolve(path, {"CUSTOM_KEY": ""})["CUSTOM_KEY"] == ""
    with pytest.raises(SecretError, match="CUSTOM_KEY") as error:
        require(resolve(path, {"CUSTOM_KEY": ""}), "CUSTOM_KEY")
    assert "synthetic-canary" not in str(error.value)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    write(path, "BOOTSTRAP", "synthetic-bootstrap")
    assert read(path)["CUSTOM_KEY"] == "synthetic-canary"


def test_bad_secret_does_not_echo_values(tmp_path):
    path = tmp_path / "secret.toml"
    path.write_text('credentials = "synthetic-canary"')
    with pytest.raises(SecretError) as error:
        read(path)
    assert "synthetic-canary" not in str(error.value)


def routed(example_config_path):
    cfg = load(example_config_path, require_secrets=False)
    data = cfg.model_dump(mode="python", exclude_unset=True)
    data["providers"] = {
        "alpha": {"base_url": "https://alpha.invalid/v1", "api_key_env": "ALPHA"},
        "beta": {"base_url": "https://beta.invalid/v1", "api_key_env": "BETA"},
    }
    data["llm"]["provider"] = "alpha"
    data["model_routes"] = {"standing": {"provider": "beta", "model": "other-model"}}
    return data


def test_named_endpoints_default_and_raw_route(example_config_path, monkeypatch):
    monkeypatch.setenv("ALPHA", "synthetic-alpha")
    monkeypatch.setenv("BETA", "synthetic-beta")
    cfg = Config.model_validate(
        routed(example_config_path), context={"base": example_config_path.parent}
    )
    assert cfg.endpoint("standing", "unrelated") == (
        "https://beta.invalid/v1",
        "synthetic-beta",
        "other-model",
    )
    assert cfg.endpoint("reaction", "reaction-model") == (
        "https://alpha.invalid/v1",
        "synthetic-alpha",
        "reaction-model",
    )
    assert cfg.endpoint("tag")[2] == cfg.llm.model
    from nora.agent.factory import create_client
    from nora.app.composition import _RelevanceGateModelClient

    url, key, model = cfg.endpoint("standing")
    client = create_client(base_url=url, api_key=key)
    raw = _RelevanceGateModelClient(
        client=client,
        model=model,
        max_tokens=16,
        temperature=0,
        timeout_seconds=1,
        reasoning_effort="none",
    )
    assert raw.model == "other-model"
    assert str(client.base_url) == "https://beta.invalid/v1/"


@pytest.mark.parametrize("change", ["model", "provider", "api"])
def test_invalid_routes_refused(example_config_path, change):
    data = routed(example_config_path)
    if change == "model":
        del data["model_routes"]["standing"]["model"]
    elif change == "provider":
        data["model_routes"]["standing"]["provider"] = "unknown"
    else:
        data["providers"]["beta"]["api"] = "anthropic-messages"
    with pytest.raises(ValueError):
        Config.model_validate(data, context={"base": example_config_path.parent})


def test_named_media_missing_key_degrades_without_losing_other_clients(
    example_config_path, monkeypatch, caplog,
):
    from nora.app.composition import _direct_describer_clients

    data = routed(example_config_path)
    data["media"]["describe"] = {
        "image": {"enabled": True, "provider": "alpha", "model": "image-model"},
        "video": {"enabled": True, "provider": "beta", "model": "video-model"},
    }
    monkeypatch.setenv("ALPHA", "synthetic-alpha")
    monkeypatch.setenv("BETA", "")
    cfg = Config.model_validate(data, context={"base": example_config_path.parent})
    clients = _direct_describer_clients(cfg)
    assert set(clients) == {"alpha"}
    assert str(clients["alpha"].base_url) == "https://alpha.invalid/v1/"
    assert "provider 'beta' has no configured credential" in caplog.text
    assert "synthetic-alpha" not in caplog.text


def test_schema_validation_does_not_read_secret(example_config_path, tmp_path):
    config = tmp_path / "config.toml"
    config.write_bytes(example_config_path.read_bytes())
    (tmp_path / "secret.toml").write_text("malformed")
    assert load(config, require_secrets=False)
    with pytest.raises(ConfigError, match="values withheld"):
        load(config)


def test_bounded_delivery_aliases_legacy_and_empty(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[providers.custom]\napi_key_env="CUSTOM"\n[store]\ndatabase_url_env="DB_ALIAS"\n'
    )
    (tmp_path / "secret.toml").write_text(
        '[credentials]\nCUSTOM="synthetic-file"\nDB_ALIAS="synthetic-db"\nAUTH_SECRET="portal"\n'
    )
    (tmp_path / ".env").write_text("CUSTOM=\nDISCORD_CLIENT_SECRET=legacy\n")
    values = source(tmp_path, {})
    assert values["CUSTOM"] == ""
    overlay = projection({"providers": {"a": {"api_key_env": "CUSTOM"}}}, values)
    assert overlay["services"]["nora"]["environment"] == {"CUSTOM": ""}
    assert overlay["services"]["presence"]["environment"] == {}
    args = prepare(tmp_path, {})
    generated = json.loads(Path(args[-1]).read_text())
    assert "AUTH_SECRET" not in generated["services"]["nora"]["environment"]
    assert Path(args[1]).stat().st_mode & 0o777 == 0o600
    (tmp_path / "secret.toml").unlink()
    assert source(tmp_path, {})["DISCORD_CLIENT_SECRET"] == "legacy"  # noqa: S105 -- synthetic fixture


def test_delivery_external_output_leaves_runtime_untouched(tmp_path, monkeypatch, capsys):
    import errno

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "config.toml").write_text('[providers.custom]\napi_key_env="CUSTOM"\n')
    (runtime / "secret.toml").write_text('[credentials]\nCUSTOM="synthetic-key"\n')
    before = {p.name: p.read_bytes() for p in runtime.iterdir()}
    output = tmp_path / "control" / ".secret-delivery"
    output.parent.mkdir()
    mkdir = Path.mkdir

    def refuse_runtime_directory(path, *args, **kwargs):
        if path.is_relative_to(runtime):
            raise OSError(errno.EROFS, "Read-only file system")
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse_runtime_directory)
    args = prepare(runtime, {}, output_directory=output)
    assert args == ["--env-file", str(output / "compose.env"),
                    "-f", str(output / "compose.json")]
    assert {p.name: p.read_bytes() for p in runtime.iterdir()} == before
    assert {p.name for p in output.iterdir()} == {"compose.env", "compose.json"}
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.iterdir())
    assert (output / "compose.env").read_text() == "CUSTOM='synthetic-key'\n"
    assert json.loads((output / "compose.json").read_text())["services"]["nora"][
        "environment"] == {"CUSTOM": "synthetic-key"}
    assert capsys.readouterr().out == ""


def test_migration_keeps_oauth_chain_and_bootstrap(tmp_path, capsys):
    (tmp_path / ".env").write_text("OPENAI_CODEX_REFRESH_TOKEN=synthetic-bootstrap\n")
    state = tmp_path / "state"
    state.mkdir()
    chain = state / "openai-codex-oauth.json"
    chain.write_text('{"refresh_token":"synthetic-rotated"}')
    before = chain.read_bytes()
    migrate(tmp_path, apply=False)
    assert not (tmp_path / "secret.toml").exists()
    migrate(tmp_path, apply=True)
    assert read(tmp_path / "secret.toml")["OPENAI_CODEX_REFRESH_TOKEN"] == "synthetic-bootstrap"  # noqa: S105 -- synthetic bootstrap
    assert chain.read_bytes() == before
    assert "synthetic" not in capsys.readouterr().out
    assert (
        next(tmp_path.glob("secret-migration-*/state/openai-codex-oauth.json")).read_bytes()
        == before
    )



def test_enabled_model_empty_override_fails_without_value(example_config_path, monkeypatch):
    monkeypatch.delenv('OMNIROUTE_API_KEY', raising=False)
    secret = example_config_path.parent / 'secret.toml'
    write(secret, 'OMNIROUTE_API_KEY', 'synthetic-private-value')
    assert load(example_config_path)
    monkeypatch.setenv('OMNIROUTE_API_KEY', '')
    with pytest.raises(ConfigError, match='OMNIROUTE_API_KEY') as error:
        load(example_config_path)
    assert 'synthetic-private-value' not in str(error.value)


def test_computer_lifecycle_resolves_only_configured_token(tmp_path):
    import subprocess

    root = Path(__file__).resolve().parents[2]
    script = (root / 'deploy/computer/nora-computer').read_text()
    function = script.split('load_token() {', 1)[1].split('\n}\n', 1)[0]
    (tmp_path / 'config.toml').write_text('[computer]\ntoken_env="CUSTOM_COMPUTER"\n')
    (tmp_path / 'secret.toml').write_text('[credentials]\nCUSTOM_COMPUTER="synthetic-token"\n')
    command = ('load_token() {' + function + '\n}\n'
               'log() { :; }\nload_token\n'
               'test "$COMPUTERD_TOKEN" = "synthetic-token"\n')
    import os
    env = {'PATH': os.environ['PATH'], 'ENV_FILE': str(tmp_path / '.env')}
    subprocess.run(['bash', '-c', command], env=env, check=True, capture_output=True)  # noqa: S603
    env['CUSTOM_COMPUTER'] = ''
    result = subprocess.run(['bash', '-c', command], env=env, check=False,  # noqa: S603
                            capture_output=True)
    assert result.returncode == 3
    assert b'synthetic-token' not in result.stdout + result.stderr


def test_migration_keeps_noncredential_deployment_values_in_env(tmp_path):
    (tmp_path / "config.toml").write_text('[providers.primary]\napi_key_env="CUSTOM_KEY"\n')
    (tmp_path / ".env").write_text(
        'CUSTOM_KEY=synthetic-key\nAUTH_URL=https://portal.invalid\nEMBEDDER_MODELS_DIR=/models\n'
    )
    migrate(tmp_path, apply=True)
    assert read(tmp_path / "secret.toml") == {"CUSTOM_KEY": "synthetic-key"}
    assert "AUTH_URL=" in (tmp_path / ".env").read_text()
