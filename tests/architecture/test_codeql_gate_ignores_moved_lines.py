"""The CodeQL gate counts a finding on a line moved unchanged as old code."""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts import ci_plan


@pytest.mark.parametrize(
    ("moved_text", "expected"), [("risky(key)\n", 0), ("risky(key, more)\n", 1)],
)
def test_codeql_does_not_gate_a_line_moved_unchanged_from_another_file(
    tmp_path, moved_text, expected,
):
    """Moving code between modules re-adds its lines; a finding on a line the
    same diff removes elsewhere is old code, while an edited line still gates."""
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()  # noqa: S603

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    (tmp_path / "old.py").write_text("keep\nrisky(key)\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "old.py").write_text("keep\n")
    (tmp_path / "new.py").write_text("header\n" + moved_text)
    git("add", ".")
    git("commit", "-qm", "head")
    head = git("rev-parse", "HEAD")
    git("switch", "-qc", "tested-base", base)
    git("merge", "--no-ff", head, "-qm", "tested merge")
    event = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    directory = tmp_path / "sarif"
    directory.mkdir()
    (directory / "python.sarif").write_text(json.dumps({"runs": [{
        "tool": {"driver": {"rules": [{
            "id": "synthetic/rule", "properties": {"security-severity": "8.0"},
        }]}},
        "results": [{"ruleId": "synthetic/rule", "locations": [{
            "physicalLocation": {
                "artifactLocation": {"uri": "new.py"}, "region": {"startLine": 2},
            },
        }]}],
    }]}))
    assert ci_plan.codeql_findings(event, tmp_path, directory) == expected
