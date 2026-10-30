"""Native Compose consumes scoped synthetic credential delivery."""

import json
from pathlib import Path

import pytest

from scripts.secret_delivery import prepare


@pytest.mark.parametrize("external_output", [False, True])
@pytest.mark.parametrize("signoz", [False, True])
def test_native_compose_consumes_projection_without_shared_secret_mount(
    tmp_path, external_output, signoz,
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
    synthetic["AUTH_URL"] = "https://example.invalid"
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
