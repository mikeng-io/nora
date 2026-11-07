"""The recorded dependency map (`pytest-testmon`), shared between runs.

A post-merge full run (a `master` push or the weekly) records `.testmondata`
in both `scripts.pytest_gate` phases and uploads it as the artifact
`testmon-data-<dep hash>-<commit sha>`. Selection — a pull request's
contracts job, `make test` and `make gate` locally — fetches the newest
artifact whose dep hash matches this checkout's `uv.lock` +
`pyproject.toml` + Python version. Anything that cannot produce a matching
record (no artifact, no `gh`, no auth, a truncated file) leaves it absent
and the selector fails closed to ALL. Nothing here decides what runs;
absence is only ever "run everything".

The Python version is part of the hash because the record's own environment
check keys (packages, python_version): a record made under another
interpreter would answer "run everything" anyway — hashing it out front
skips a useless download.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = ".testmondata"
_ARTIFACT_PREFIX = "testmon-data"


def dep_hash(repo: Path) -> str:
    """Fingerprint of the inputs a record is valid for."""
    digest = hashlib.sha256()
    for name in ("pyproject.toml", "uv.lock"):
        digest.update((repo / name).read_bytes())
    digest.update(platform.python_version().encode())
    return digest.hexdigest()[:16]


def artifact_name(repo: Path, commit: str) -> str:
    return f"{_ARTIFACT_PREFIX}-{dep_hash(repo)}-{commit}"


def _gh(repo: Path, *args: str, binary: bool = False):
    kwargs = {} if binary else {"text": True}
    result = subprocess.run(  # noqa: S603 - fixed gh argv, repo-local
        ["gh", *args],  # noqa: S607
        cwd=repo, capture_output=True, check=False, timeout=120, **kwargs,
    )
    if result.returncode:
        err = result.stderr if binary else result.stderr.strip()
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {err}")
    return result.stdout


def _repo_name(repo: Path) -> str:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    return _gh(repo, "repo", "view", "--json", "nameWithOwner",
               "-q", ".nameWithOwner").strip()


def fetch(repo: Path) -> Path | None:
    """Newest matching artifact's `.testmondata`, or None (fail closed)."""
    try:
        name = _repo_name(repo)
        prefix = f"{_ARTIFACT_PREFIX}-{dep_hash(repo)}-"
        chosen = None
        for page in (1, 2, 3):
            listing = json.loads(_gh(
                repo, "api",
                f"repos/{name}/actions/artifacts?per_page=100&page={page}",
            ))
            artifacts = [
                a for a in listing.get("artifacts", [])
                if a.get("name", "").startswith(prefix) and not a.get("expired", True)
            ]
            if artifacts:
                chosen = max(artifacts, key=lambda a: a.get("created_at", ""))
                break
            if listing.get("total_count", 0) <= page * 100:
                break
        if chosen is None:
            return None
        archive = _gh(
            repo, "api", f"repos/{name}/actions/artifacts/{chosen['id']}/zip",
            binary=True,
        )
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            data = bundle.read(DB_NAME)
        target = repo / DB_NAME
        target.write_bytes(data)
        return target
    except (OSError, RuntimeError, KeyError, ValueError, zipfile.BadZipFile,
            subprocess.SubprocessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("hash", "name", "fetch"))
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args(argv)
    if args.command == "hash":
        print(dep_hash(args.repo))
        return 0
    if args.command == "name":
        if not args.commit:
            print("testmon_record: --commit or GITHUB_SHA required", file=sys.stderr)
            return 2
        print(artifact_name(args.repo, args.commit))
        return 0
    record = fetch(args.repo)
    if record is None:
        print("testmon_record: no matching dependency record", file=sys.stderr)
        return 1
    print(f"testmon_record: {record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
