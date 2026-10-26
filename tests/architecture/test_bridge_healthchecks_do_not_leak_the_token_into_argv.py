"""The bridges' bearer token is readable by anything in its container. The
healthcheck must hand it to curl on stdin (`-H @-`), never through an expanded
Authorization header in curl's argv, which `/proc/<pid>/cmdline` exposes.

Runs both compose healthchecks with a synthetic token and a curl that records
its argv: the assertion is on the process arguments, not on the YAML text.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    ROOT / "docker-compose.yml",
    ROOT / "deploy/instagram-bridge/compose.fragment.yml",
)


def _healthcheck_commands() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in COMPOSE_FILES:
        document = yaml.safe_load(path.read_text())
        for service, body in document["services"].items():
            test = (body.get("healthcheck") or {}).get("test")
            if isinstance(test, list) and "NORA_BRIDGE_TOKEN" in " ".join(test):
                found.append((f"{path.name}:{service}", test[-1]))
    return found


def test_every_healthcheck_that_carries_the_bearer_keeps_it_out_of_curls_actual_argv(tmp_path):
    commands = _healthcheck_commands()
    # Both bridges, and the voice sidecar, whose /health requires the bearer.
    assert len(commands) == 3, commands

    stub = tmp_path / "bin"
    stub.mkdir()
    curl = stub / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{tmp_path}/curl-argv"\n'
        f'cat > "{tmp_path}/curl-stdin"\n'
        "exit 0\n",
    )
    curl.chmod(0o755)
    token = "synthetic-bridge-token-never-argv"  # noqa: S105 - a fake
    env = dict(os.environ, NORA_BRIDGE_TOKEN=token, PATH=f"{stub}:{os.environ['PATH']}")

    for label, command in commands:
        argv_log = tmp_path / "curl-argv"
        stdin_log = tmp_path / "curl-stdin"
        done = subprocess.run(  # noqa: S603 - the command is this repo's compose text
            ["sh", "-c", command.replace("$$", "$")],
            env=env, capture_output=True, text=True, check=False,
        )
        assert done.returncode == 0, (label, done.stderr)
        argv = argv_log.read_text().splitlines()
        assert "-H" in argv and "@-" in argv, (label, argv)
        assert not any(token in arg for arg in argv), (label, argv)
        assert stdin_log.read_text() == f"Authorization: Bearer {token}\n", label
