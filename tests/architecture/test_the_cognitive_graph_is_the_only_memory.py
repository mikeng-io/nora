"""The Cognitive Graph is the only memory: the retired backend, Honcho, is named
nowhere in the code, the compose file or the two config templates.

The test runs the project's locked command itself, against the working tree,
so the gate and its test cannot drift apart:

    git grep -niE 'honcho' -- nora docker-compose.yml config.example.toml identity.example.toml

`git grep` exits 1 when nothing matches and 0 when something does; any other
exit is an error and fails here rather than reading as silence. The positive
control runs the same command over this file, which names the backend in both
cases, so a blind or case-sensitive instrument cannot pass.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().relative_to(ROOT).as_posix()


def _locked_gate(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "grep", "-niE", "honcho", "--", *paths],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )


def test_the_retired_backend_is_named_nowhere_the_gate_reads() -> None:
    found = _locked_gate(
        "nora", "docker-compose.yml", "config.example.toml", "identity.example.toml",
    )
    assert found.returncode in (0, 1), f"git grep failed: {found.stderr}"
    assert (found.returncode, found.stdout) == (1, ""), (
        "the retired memory backend is named again; say what the surviving thing "
        f"does instead:\n{found.stdout}"
    )


def test_the_gate_matches_the_name_in_either_case() -> None:
    found = _locked_gate(HERE)
    assert found.returncode == 0, f"the gate found nothing in {HERE}: {found.stderr}"
    lines = found.stdout.splitlines()
    assert any("Honcho" in line for line in lines)
    assert any("'honcho'" in line for line in lines)
