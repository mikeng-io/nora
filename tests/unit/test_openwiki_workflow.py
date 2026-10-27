"""Manual planning context must not break ordinary unattended updates."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    "planning_request", ["", " \t\n", "Expand implementation details; $(false)"]
)
def test_update_request_is_optional_and_literal(tmp_path: Path, planning_request: str) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/openwiki-update.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["update"]["steps"]
        if step.get("name") == "Update repository wiki"
    )
    npm = tmp_path / "npm"
    npm.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    npm.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - execute the checked-in workflow against a recording npm fake
        ["bash", "-e", "-c", step["run"]],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "OPENWIKI_UPDATE_REQUEST": planning_request,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    expected = ["run", "wiki:update", "--prefix", "docs-site"]
    if planning_request.strip():
        expected += ["--", planning_request]
    assert json.loads(result.stdout) == expected


def test_generation_failure_preserves_exit_code_and_receipt(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/openwiki-update.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["update"]["steps"]
        if step.get("name") == "Update repository wiki"
    )
    expected = ["run", "wiki:update", "--prefix", "docs-site"]
    npm = tmp_path / "npm"
    npm.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"assert sys.argv[1:] == {expected!r}\n"
        "print(json.dumps(sys.argv[1:]))\nsys.exit(17)\n"
    )
    npm.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - execute the workflow against a recording failure fake
        ["bash", "-e", "-c", step["run"]],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "OPENWIKI_UPDATE_REQUEST": "",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(result.stdout) == expected
    assert result.returncode == 17, result.stderr
    assert (tmp_path / "openwiki-safe-summary" / "generation.rc").read_text() == "17\n"
