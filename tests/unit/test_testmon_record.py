"""Artifact provenance for scripts/testmon_record.py — a fake `gh` that
answers like the real API (listings, run details, zip downloads) including the
wrong-call shapes: artifacts produced by runs that must NOT be trusted."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts import testmon_record
from scripts.testmon_record import DB_NAME, fetch

REPO = "mikeng-io/nora"
GOOD_SHA = "0" * 40


def _repo(root: Path) -> Path:
    (root / "pyproject.toml").write_text("[project]\nversion = '1'\n")
    (root / "uv.lock").write_text("x\n")
    return root


def _zip() -> bytes:
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as z:
        z.writestr(DB_NAME, b"record")
    return blob.getvalue()


def _artifact(name: str, run_id: int, *, expired: bool = False,
              created: str = "2026-01-01") -> dict:
    return {"id": 1000 + run_id, "name": name, "expired": expired,
            "created_at": created, "workflow_run": {"id": run_id}}


def _run(run_id: int, **over) -> dict:
    base = {"path": ".github/workflows/ci.yml", "event": "push",
            "head_branch": "master", "status": "completed",
            "conclusion": "success",
            "head_repository": {"full_name": REPO}, "head_sha": GOOD_SHA}
    base.update(over)
    return base


def _fake_gh(artifacts: list[dict], runs: dict[int, dict]):
    """Answers `gh api <endpoint>` like the real one: listing pages, run
    details by id, zip bytes for an artifact id. Unknown endpoints error."""
    def gh(repo: Path, *args: str, binary: bool = False):
        endpoint = args[1]
        if "actions/artifacts" in endpoint and endpoint.endswith("/zip"):
            run_id = int(endpoint.rsplit("/", 2)[1]) - 1000
            return _zip()
        if "actions/artifacts" in endpoint:
            return json.dumps({"artifacts": artifacts,
                               "total_count": len(artifacts)})
        if "actions/runs/" in endpoint:
            run_id = int(endpoint.rsplit("/", 1)[1])
            if run_id not in runs:
                raise RuntimeError("404")
            return json.dumps(runs[run_id])
        raise RuntimeError(f"unexpected endpoint {endpoint}")
    return gh


def _name(sha: str) -> str:
    return f"{testmon_record._ARTIFACT_PREFIX}-deadbeef-{sha}"


def test_the_right_artifact_is_downloaded(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(
        testmon_record, "dep_hash", lambda r: "deadbeef")
    artifacts = [_artifact(_name(GOOD_SHA), 1)]
    monkeypatch.setattr(
        testmon_record, "_gh",
        _fake_gh(artifacts, {1: _run(1)}))
    assert fetch(repo) == repo / DB_NAME
    assert (repo / DB_NAME).read_bytes() == b"record"


@pytest.mark.parametrize("bad_run", [
    _run(1, event="pull_request"),
    _run(1, conclusion="failure"),
    _run(1, path=".github/workflows/admin.yml"),
    _run(1, head_repository={"full_name": "fork-owner/nora"}),
    _run(1, head_sha="f" * 40),
    _run(1, head_branch="release/x"),
    _run(1, status="in_progress", conclusion=None),
])
def test_a_wrong_run_is_not_accepted(tmp_path, monkeypatch, bad_run):
    repo = _repo(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(testmon_record, "dep_hash", lambda r: "deadbeef")
    monkeypatch.setattr(
        testmon_record, "_gh",
        _fake_gh([_artifact(_name(GOOD_SHA), 1)], {1: bad_run}))
    assert fetch(repo) is None
    assert not (repo / DB_NAME).exists()


def test_an_expired_artifact_is_not_accepted(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(testmon_record, "dep_hash", lambda r: "deadbeef")
    monkeypatch.setattr(
        testmon_record, "_gh",
        _fake_gh([_artifact(_name(GOOD_SHA), 1, expired=True)], {1: _run(1)}))
    assert fetch(repo) is None


def test_the_newest_valid_artifact_wins_over_a_newer_bad_one(
    tmp_path, monkeypatch,
):
    """A newer artifact from a pull-request run does not shadow the older
    master-push record."""
    repo = _repo(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(testmon_record, "dep_hash", lambda r: "deadbeef")
    artifacts = [
        _artifact(_name(GOOD_SHA), 1, created="2026-01-01"),
        _artifact(_name(GOOD_SHA), 2, created="2026-01-02"),
    ]
    runs = {1: _run(1), 2: _run(2, event="pull_request")}
    monkeypatch.setattr(testmon_record, "_gh", _fake_gh(artifacts, runs))
    assert fetch(repo) == repo / DB_NAME
