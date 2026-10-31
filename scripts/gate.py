"""Reuse a successful gate receipt only for the same clean local candidate/context."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from scripts import affected_tests
from scripts.pytest_gate import _GENERATION_ENV, LOCK_PATH, PROJECT_ROOT, _run, host_lease


def command(*args: str, repo: Path) -> str:
    return subprocess.check_output(  # noqa: S603 — fixed local tool argv, never a shell
        args,
        cwd=repo,
        text=True,
        stderr=subprocess.PIPE,
        timeout=30,
    ).strip()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def candidate(repo: Path) -> dict | None:
    """Uncommitted/unclassified inputs never receive or reuse an acceptance receipt."""
    if command("git", "diff", "--name-only", "HEAD", repo=repo):
        return None
    for entry in command("git", "ls-files", "--stage", repo=repo).splitlines():
        mode, name = entry.split("\t", 1)
        if mode.startswith("120000 ") and not (repo / name).resolve().is_relative_to(
            repo.resolve()
        ):
            return None
    if any(
        line.startswith(("S ", "s ")) or line[:1].islower()
        for line in command("git", "ls-files", "-v", repo=repo).splitlines()
    ):
        return None  # Hidden index changes (skip-worktree/assume-unchanged) are not evidence.
    if any(
        p.startswith("deploy/instagram-bridge/")
        for p in affected_tests.git_changed_paths(repo, "origin/master")
    ):
        return None  # ponytail: local Python acceptance only; Go cache inputs remain out of scope.
    untracked = command("git", "ls-files", "--others", "--exclude-standard", repo=repo).splitlines()
    if any(not affected_tests.generated_lfs_hook(repo, p) for p in untracked):
        return None
    return {
        "head": command("git", "rev-parse", "HEAD", repo=repo),
        "tree": command("git", "rev-parse", "HEAD^{tree}", repo=repo),
        "base": command("git", "rev-parse", "origin/master", repo=repo),
        "repo": str(repo.resolve()),
    }


def context(repo: Path) -> str:
    """Private digest only: do not serialize credential-bearing environment/config."""
    packages = []
    for distribution in importlib.metadata.distributions():
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        if direct.get("dir_info", {}).get("editable"):
            source = Path(unquote(urlparse(direct["url"]).path)).resolve()
            if source != repo.resolve():
                raise ValueError("External editable dependency needs fresh validation")
        packages.append(("distribution", distribution.metadata["Name"], distribution.version))
        for rel in distribution.files or []:
            if str(rel).endswith((".pyc", ".pyo")) or "__pycache__" in str(rel):
                continue
            file = Path(distribution.locate_file(rel))
            if file.is_file():
                st = file.stat()
                packages.append((str(file.resolve()), st.st_size, st.st_mtime_ns, st.st_mode))
    tools = {}
    for name in ("uv", "git", "git-lfs", "make", "go", "node", "docker"):
        executable = shutil.which(name)
        if executable:
            file = Path(executable).resolve()
            st = file.stat()
            tools[name] = (str(file), st.st_size, st.st_mtime_ns)
    private = {}
    files = set(repo.glob("*.toml")) | set(repo.glob(".env*"))
    files.update(Path(v) for v in os.environ.values() if v.startswith("/") and ":" not in v)
    for file in sorted(files):
        if file.is_file():
            private[str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
    docker = {
        "context": command("docker", "context", "show", repo=repo),
        "daemon": command("docker", "info", "--format", "{{.ID}}", repo=repo),
        "version": command("docker", "version", "--format", "{{json .}}", repo=repo),
        "images": sorted(
            command(
                "docker",
                "image",
                "ls",
                "--no-trunc",
                "--digests",
                "--format",
                "{{.ID}} {{.Digest}}",
                repo=repo,
            ).splitlines()
        ),
    }
    version = json.loads(docker["version"])
    if (
        not docker["daemon"]
        or not docker["context"]
        or not isinstance(version, dict)
        or not isinstance(version.get("Server"), dict)
        or not version["Server"].get("Version")
    ):
        raise ValueError("Docker context cannot be verified")
    ids = sorted(command("docker", "ps", "-aq", "--no-trunc", repo=repo).splitlines())
    if ids:
        containers = json.loads(command("docker", "inspect", *ids, repo=repo))
        docker["containers"] = [
            {
                "Id": c["Id"],
                "Image": c["Image"],
                "Config": c["Config"],
                "Mounts": c["Mounts"],
                "State": {
                    k: c["State"].get(k)
                    for k in (
                        "Status",
                        "Running",
                        "Paused",
                        "Restarting",
                        "OOMKilled",
                        "Dead",
                        "ExitCode",
                        "StartedAt",
                        "FinishedAt",
                    )
                },
                "Health": (c["State"].get("Health") or {}).get("Status"),
            }
            for c in containers
        ]
    env = {k: v for k, v in os.environ.items() if k != _GENERATION_ENV}
    return digest(
        {
            "python": [sys.executable, sys.version],
            "packages": sorted(packages),
            "tools": tools,
            "git_config": command("git", "config", "--null", "--list", "--show-origin", repo=repo),
            "private": private,
            "environment": env,
            "docker": docker,
        }
    )


def fingerprint(repo: Path) -> dict | None:
    try:
        source = candidate(repo)
        if source is None:
            return None
        measured = context(repo)
        if candidate(repo) != source:
            return None
        return {**source, "context": measured, "scope": "make gate v1"}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None  # A context that cannot be verified is never a cache hit.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh", action="store_true", help="force a real validation")
    parser.add_argument("--full", action="store_true", help="always run the full serial gate")
    args = parser.parse_args(argv)
    repo = PROJECT_ROOT
    receipt = None
    try:
        with host_lease(LOCK_PATH, os.environ.pop(_GENERATION_ENV, None)) as generation:
            git_dir = Path(command("git", "rev-parse", "--absolute-git-dir", repo=repo))
            receipt = git_dir / "nora-gate-receipt.json"
            # Hosted CI always validates afresh. Local receipts are cooperative, not attestations.
            key = (
                None
                if (
                    args.full
                    or os.environ.get("CI")
                    or os.environ.get("MAKEOVERRIDES")
                    or os.environ.get("PYTHONPATH")
                )
                else fingerprint(repo)
            )
            try:
                changed = affected_tests.git_changed_paths(
                    repo, "origin/master", include_worktree=True
                )
                docs_only = bool(changed) and all(
                    affected_tests._is_doc_or_data(p) for p in changed
                )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                docs_only = False
                key = None
            run = ["make", "--no-print-directory", "--directory", str(repo)]
            run += (
                ["_full-gate"]
                if args.full
                else ["test-affected", "BASE=origin/master", "INCLUDE_WORKTREE=--include-worktree"]
                if docs_only
                else ["_gate"]
            )
            try:
                saved = json.loads(receipt.read_text())
                completed = datetime.fromisoformat(saved["completed_at"])
                if completed.tzinfo is None or completed > datetime.now(UTC):
                    raise ValueError("Invalid receipt completion")
            except (OSError, ValueError, TypeError, KeyError):
                saved = {}
            if (
                not args.fresh
                and not args.full
                and key is not None
                and isinstance(saved, dict)
                and type(saved.get("rc")) is int
                and set(saved) == {"key", "rc", "command", "completed_at"}
                and saved["key"] == key
                and saved["rc"] == 0
                and saved["command"] == run
                and isinstance(saved["completed_at"], str)
            ):
                print(
                    "gate receipt: REUSED successful identical candidate/context; no checks rerun",
                    flush=True,
                )
                return 0
            # Invalidate BEFORE execution: a later failure/interrupt cannot leave an old green hit.
            receipt.unlink(missing_ok=True)
            print(
                "gate receipt: MISS; "
                + (
                    "full serial gate"
                    if args.full
                    else "document readers"
                    if docs_only
                    else "static + affected tests"
                ),
                flush=True,
            )
            # Keep ownership across make, both pytest phases and receipt publication.
            env = {**os.environ, _GENERATION_ENV: generation}
            rc = _run(run, env, group=True)
            if rc == 0 and key is not None and fingerprint(repo) == key:
                temporary = receipt.with_suffix(".tmp")
                fd = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(fd, "w") as stream:
                    json.dump(
                        {
                            "key": key,
                            "rc": 0,
                            "command": run,
                            "completed_at": datetime.now(UTC).isoformat(),
                        },
                        stream,
                        sort_keys=True,
                    )
                temporary.replace(receipt)
                print("gate receipt: STORED successful unchanged candidate/context", flush=True)
            return rc if rc >= 0 else 128 - rc
    except KeyboardInterrupt:
        if receipt is not None:
            receipt.unlink(missing_ok=True)
        print("gate: interrupted; no reusable success receipt", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"gate: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
