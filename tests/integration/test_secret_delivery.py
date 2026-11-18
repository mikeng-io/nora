"""Native Compose consumes scoped synthetic credential delivery."""

import json
from pathlib import Path

import pytest

from scripts.secret_delivery import prepare


@pytest.mark.parametrize("enabled", [None, False])
def test_disabled_scoped_mode_does_not_admit_an_implicit_overlay(tmp_path, enabled):
    config = "" if enabled is None else "[computer.scoped]\nenabled=false\n"
    (tmp_path / "config.toml").write_text(config)
    assert prepare(tmp_path, {"NORA_COMPUTER_REDIS_URL": "synthetic-unused"}) == []
    assert not (tmp_path / ".secret-delivery").exists()


@pytest.mark.parametrize("alias", [None, "NORA_COMPUTER_REDIS_URL", "CUSTOM_COMPUTER_REDIS"])
@pytest.mark.parametrize("credential_source", ["environment", "dotenv", "secret"])
def test_prepare_delivers_scoped_redis_reference(tmp_path, alias, credential_source):
    key = alias or "NORA_COMPUTER_REDIS_URL"
    config = "[computer.scoped]\nenabled=true\n"
    if alias is not None:
        config += f'redis_url_env="{alias}"\n'
    (tmp_path / "config.toml").write_text(config)
    value = "synthetic-redis-credential"
    environ = {"UNRELATED_PRIVATE_KEY": "synthetic-unrelated"}
    if credential_source == "environment":
        environ[key] = value
    elif credential_source == "dotenv":
        (tmp_path / ".env").write_text(f"{key}={value}\n")
    else:
        (tmp_path / "secret.toml").write_text(f'[credentials]\n{key}="{value}"\n')
    arguments = prepare(tmp_path, environ)
    assert arguments
    overlay = json.loads(Path(arguments[-1]).read_text())
    assert overlay["services"]["nora"]["environment"] == {key: value}
    assert overlay["services"]["app"]["environment"] == {"NORA_IDENTITY_BACKEND": "file"}
    assert all(not service["environment"] for name, service in overlay["services"].items()
               if name not in {"nora", "app"})
    assert "UNRELATED_PRIVATE_KEY" not in Path(arguments[1]).read_text()


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("external_output", [False, True])
@pytest.mark.parametrize("signoz", [False, True])
def test_native_compose_consumes_projection_without_shared_secret_mount(
    tmp_path, external_output, signoz, managed,
):
    import os
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI unavailable")
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "config.toml").write_text(
        '[providers.primary]\napi_key_env="CUSTOM_ENDPOINT_KEY"\n'
        '[otp]\napi_token_env="CUSTOM_OTP"\n'
        '[platforms.discord]\ntoken_env="CUSTOM_DISCORD"\n'
        '[presence]\nclient_credential_env="CUSTOM_LIFE360"\n'
        'geocode_api_key_env="CUSTOM_MAPS"\n')
    if managed:
        with (tmp_path / "config.toml").open("a") as config:
            config.write('[identity]\nbackend="managed"\n'
                         '[identity.authentication.portal]\nkey_env="CUSTOM_PORTAL"\n')
    if signoz:
        with (tmp_path / "config.toml").open("a") as config:
            config.write('[obs.tracing]\nenabled=true\n'
                         'endpoint="http://signoz-ingester:4318/v1/traces"\n')
        overlay = tmp_path / "deploy/signoz/docker-compose.signoz.yaml"
        overlay.parent.mkdir(parents=True)
        shutil.copyfile(root / "deploy/signoz/docker-compose.signoz.yaml", overlay)
    synthetic = dict.fromkeys([
        "SIGNAL_ACCOUNT", "NORA_BRIDGE_TOKEN", "POSTGRES_PASSWORD", "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET", "AUTH_SECRET", "RUSTFS_ROOT_USER", "RUSTFS_ROOT_PASSWORD",
        "CUSTOM_ENDPOINT_KEY", "CUSTOM_OTP", "CUSTOM_DISCORD", "CUSTOM_LIFE360", "CUSTOM_MAPS",
    ], "synthetic-canary")
    # Every name compose refuses to interpolate without must be DELIVERED —
    # reading the set off the file keeps the proof honest as services grow.
    import re

    required = re.findall(
        r"\$\{([A-Z][A-Z0-9_]*):\?",
        (root / "docker-compose.yml").read_text(encoding="utf-8"))
    for name in required:
        synthetic.setdefault(name, "synthetic-canary")
    synthetic["AUTH_URL"] = "https://example.invalid"
    synthetic["CUSTOM_PORTAL"] = "synthetic-portal-signing-key-32-bytes"
    synthetic["AUTH_TELEGRAM_ID"] = "synthetic-telegram-client"
    synthetic["AUTH_TELEGRAM_SECRET"] = "synthetic-telegram-credential"  # noqa: S105 - generated
    synthetic["CUSTOM_ENDPOINT_KEY"] = "synthetic-dollar$literal"
    (tmp_path / "secret.toml").write_text(
        '[credentials]\n' + ''.join(f'{k}={json.dumps(v)}\n' for k, v in synthetic.items()))
    control = tmp_path / "control"
    control.mkdir()
    options = {"output_directory": control / ".secret-delivery"} if external_output else {}
    arguments = prepare(tmp_path, {}, **options)
    result = subprocess.run(  # noqa: S603 -- fixed synthetic local Compose validation
        ["docker", "compose", "-f", str(root / "docker-compose.yml"), *arguments,
         "config", "--format", "json"], capture_output=True, check=True,
        env={"PATH": os.environ["PATH"]})
    services = json.loads(result.stdout)["services"]
    for service in ("nora", "presence", "analytics"):
        if signoz:
            assert "signoz" in services[service]["networks"]
            assert "nora" in services[service]["networks"]
            assert services[service]["logging"]["driver"] == "journald"
            assert services[service]["logging"]["options"] == {"tag": service}
        else:
            assert "signoz" not in services[service]["networks"]
    delivered = services["nora"]["environment"]["CUSTOM_ENDPOINT_KEY"].replace("$$", "$")
    assert delivered == synthetic["CUSTOM_ENDPOINT_KEY"]
    assert services["app"]["environment"]["NORA_OTP_API_TOKEN"] == synthetic["CUSTOM_OTP"]
    portal = services["app"]["environment"]
    assert portal["NORA_IDENTITY_BACKEND"] == ("managed" if managed else "file")
    assert portal["AUTH_TELEGRAM_ID"] == synthetic["AUTH_TELEGRAM_ID"]
    assert portal["AUTH_TELEGRAM_SECRET"] == synthetic["AUTH_TELEGRAM_SECRET"]
    if managed:
        assert portal["NORA_AUTHENTICATION_ISSUER"] == "portal"
        assert portal["NORA_AUTHENTICATION_KEY"] == synthetic["CUSTOM_PORTAL"]
        assert services["nora"]["environment"]["CUSTOM_PORTAL"] == synthetic["CUSTOM_PORTAL"]
    else:
        assert "NORA_AUTHENTICATION_KEY" not in portal
    assert (services["voice-sidecar"]["environment"]["DISCORD_BOT_TOKEN"]
            == synthetic["CUSTOM_DISCORD"])
    assert "alert-bridge" not in services
    assert "CUSTOM_LIFE360" not in services["nora"]["environment"]
    assert services["presence"]["environment"]["CUSTOM_LIFE360"] == synthetic["CUSTOM_LIFE360"]
    assert "CUSTOM_MAPS" not in services["presence"]["environment"]
    assert services["nora"]["environment"]["CUSTOM_MAPS"] == synthetic["CUSTOM_MAPS"]
    assert "CUSTOM_ENDPOINT_KEY" not in services["app"]["environment"]
    assert "CUSTOM_ENDPOINT_KEY" not in services["presence"]["environment"]
    assert services["db"]["environment"]["POSTGRES_PASSWORD"] == synthetic["POSTGRES_PASSWORD"]
    assert not any("secret.toml" in str(volume) for service in services.values()
                   for volume in service.get("volumes", []))


@pytest.mark.parametrize("issuer", ["portal", "alternate"])
def test_managed_portal_uses_only_its_configured_key_and_preserves_otp(tmp_path, issuer):
    (tmp_path / "config.toml").write_text(
        '[identity]\nbackend="managed"\n'
        '[identity.authentication.portal]\nkey_env="PORTAL_KEY"\n'
        '[identity.authentication.alternate]\nkey_env="ALTERNATE_KEY"\n'
        '[identity.managed]\nkratos_admin_key_env="ORY_KEY"\n'
        '[identity.provisioning.bridge]\ntoken_env="PROVISIONER_TOKEN"\n'
        '[otp]\napi_token_env="CUSTOM_OTP"\n')
    values = {"PORTAL_KEY": "synthetic-portal-signing-key-32-bytes",
              "ALTERNATE_KEY": "synthetic-alternate-signing-key-32-bytes",
              "ORY_KEY": "synthetic-private-admin", "PROVISIONER_TOKEN": "synthetic-provisioner",
              "CUSTOM_OTP": "synthetic-otp-bearer"}
    (tmp_path / "secret.toml").write_text('[credentials]\n' + ''.join(
        f'{key}={json.dumps(value)}\n' for key, value in values.items()))
    arguments = prepare(tmp_path, {"NORA_AUTHENTICATION_ISSUER": issuer,
                                   "NORA_IDENTITY_BACKEND": "file"})
    services = json.loads(Path(arguments[-1]).read_text())["services"]
    assert services["app"]["environment"] == {
        "NORA_IDENTITY_BACKEND": "managed", "NORA_AUTHENTICATION_ISSUER": issuer,
        "NORA_AUTHENTICATION_KEY": values["PORTAL_KEY" if issuer == "portal" else "ALTERNATE_KEY"],
        "NORA_OTP_API_TOKEN": values["CUSTOM_OTP"],
    }
    assert services["nora"]["environment"]["ORY_KEY"] == values["ORY_KEY"]
    for reader in ("app", "presence", "analytics"):
        assert "ORY_KEY" not in services[reader]["environment"]
        assert "PROVISIONER_TOKEN" not in services[reader]["environment"]


def test_missing_configured_portal_key_refuses_without_values(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[identity]\nbackend="managed"\n'
        '[identity.authentication.portal]\nkey_env="PORTAL_KEY"\n')
    with pytest.raises(ValueError, match="configured portal authentication key unavailable"):
        prepare(tmp_path, {})
    assert not (tmp_path / ".secret-delivery/compose.json").exists()


def test_unconfigured_gateway_cannot_borrow_another_issuers_key(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[identity]\nbackend="managed"\n'
        '[identity.authentication.other]\nkey_env="OTHER_KEY"\n')
    arguments = prepare(tmp_path, {"OTHER_KEY": "synthetic-unrelated-signing-key-32-bytes"})
    services = json.loads(Path(arguments[-1]).read_text())["services"]
    assert services["app"]["environment"] == {
        "NORA_IDENTITY_BACKEND": "managed", "NORA_AUTHENTICATION_ISSUER": "portal"}
    assert services["nora"]["environment"]["OTHER_KEY"]


def test_managed_without_a_portal_issuer_still_refuses_file_fallback(tmp_path):
    (tmp_path / "config.toml").write_text('[identity]\nbackend="managed"\n')
    arguments = prepare(tmp_path, {})
    services = json.loads(Path(arguments[-1]).read_text())["services"]
    assert services["app"]["environment"] == {
        "NORA_IDENTITY_BACKEND": "managed", "NORA_AUTHENTICATION_ISSUER": "portal"}
