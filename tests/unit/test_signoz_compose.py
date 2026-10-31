"""Backend credentials use the existing private source and fail closed."""
from pathlib import Path

import pytest

from scripts import signoz_compose


def test_backend_secrets_reach_compose_without_command_line_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(signoz_compose.sys, "argv", ["signoz_compose", "config", "--quiet"])
    values = {"SIGNOZ_CLICKHOUSE_PASSWORD": "a" * 32, "SIGNOZ_POSTGRES_PASSWORD": "b" * 32}
    (tmp_path / "secret.toml").write_text(
        "[credentials]\n" + "".join(f'{k}="{v}"\n' for k, v in values.items()))
    calls = []
    monkeypatch.setattr(signoz_compose.os, "execvpe", lambda *args: calls.append(args))
    signoz_compose.main()
    command, arguments, environment = calls[0]
    assert command == "docker"
    assert arguments == ["docker", "compose", "-f",
                         str(Path.cwd() / "deploy/signoz/deployment/compose.yaml"),
                         "config", "--quiet"]
    assert all(environment[k] == v for k, v in values.items())
    assert all(v not in " ".join(arguments) for v in values.values())


@pytest.mark.parametrize("value", ["", "short", "z" * 32, "a" * 31, "a" * 32 + "\n"])
def test_missing_or_dsn_unsafe_password_refuses_before_docker(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIGNOZ_CLICKHOUSE_PASSWORD", value)
    monkeypatch.setenv("SIGNOZ_POSTGRES_PASSWORD", "b" * 32)
    with pytest.raises(SystemExit, match="Set SIGNOZ_CLICKHOUSE_PASSWORD"):
        signoz_compose.main()
