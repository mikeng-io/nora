"""The owner settings command must preserve privacy and send structured API bodies."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "public_settings", ROOT / "scripts/configure_public_github.py"
)
assert spec and spec.loader
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


def test_private_repository_is_read_only_even_with_apply(monkeypatch):
    reads = []

    def private_api(repo, path="", method="GET", body=None):
        reads.append((path, method))
        if path or method != "GET" or body:
            pytest.fail("private repository received a mutation")
        return {"private": True}

    monkeypatch.setattr(settings, "api", private_api)
    monkeypatch.setattr("sys.argv", ["configure_public_github.py", "--apply"])
    with pytest.raises(SystemExit) as exit_info:
        settings.main()
    assert exit_info.value.code == 1
    assert reads == [("", "GET")]


def test_api_sends_json_body_through_stdin_without_shell(monkeypatch):
    calls = []

    def gh(command, *, input, text):
        calls.append((command, input, text))
        # Mirror gh --input behavior: invalid/missing JSON cannot reach the API.
        body = json.loads(input)
        return json.dumps({"enabled": body["enabled"]})

    monkeypatch.setattr(settings.subprocess, "check_output", gh)
    result = settings.api("example/repository", "/actions/permissions", "PUT", {"enabled": True})
    assert result == {"enabled": True}
    command, stdin, as_text = calls[0]
    assert command[1:] == [
        "api",
        "repos/example/repository/actions/permissions",
        "--method",
        "PUT",
        "--input",
        "-",
    ]
    assert json.loads(stdin) == {"enabled": True}
    assert as_text is True
