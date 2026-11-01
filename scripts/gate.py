"""Reuse a successful gate receipt only for the same clean local candidate/context."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from scripts import affected_tests
from scripts.pytest_gate import _GENERATION_ENV, LOCK_PATH, PROJECT_ROOT, _run, host_lease

MAX_INPUT_ENTRIES = 4096
MAX_INPUT_BYTES = 64 * 1024 * 1024
INPUT_SECONDS = 2.0


def input_manifest(repo: Path, env: dict[str, str]) -> dict:
    """Bound private filesystem inputs; unsupported/oversized trees cannot reuse."""
    deadline = time.monotonic() + INPUT_SECONDS
    roots = set(repo.glob("*.toml")) | set(repo.glob(".env*"))
    for value in env.values():
        if "://" in value:
            continue
        for part in value.split(os.pathsep):
            path = repo / part
            if part and (Path(part).is_absolute() or path.exists() or path.is_symlink()):
                roots.add(path)
            elif not part and os.pathsep in value:
                roots.add(repo)
    roots.update(repo / p for p in env.get("PATH", os.defpath).split(os.pathsep))
    cache = env.get("TIKTOKEN_CACHE_DIR", env.get("DATA_GYM_CACHE_DIR"))
    if cache == "":
        raise ValueError("Uncached tokenizer network input needs fresh validation")
    roots.add(repo / cache if cache is not None else Path(tempfile.gettempdir()) / "data-gym-cache")
    manifest = {}
    observed = {}
    files = []
    size = 0
    pending = list(roots)
    while pending:
        if time.monotonic() > deadline or len(manifest) >= MAX_INPUT_ENTRIES:
            raise ValueError("Input manifest traversal budget exceeded")
        path = pending.pop()
        name = str(path.absolute())
        if name in manifest:
            continue
        try:
            before = path.lstat()
        except FileNotFoundError:
            manifest[name] = ["missing"]
            observed[path] = None
            continue
        observed[path] = before
        if stat.S_ISLNK(before.st_mode):
            raise ValueError("Symlinked input needs fresh validation")
        if stat.S_ISDIR(before.st_mode):
            manifest[name] = ["directory", before.st_mode]
            with os.scandir(path) as entries:
                for entry in entries:
                    if (len(manifest) + len(pending) >= MAX_INPUT_ENTRIES
                            or time.monotonic() > deadline):
                        raise ValueError("Input manifest traversal budget exceeded")
                    pending.append(Path(entry.path))
        elif stat.S_ISREG(before.st_mode):
            size += before.st_size
            if size > MAX_INPUT_BYTES:
                raise ValueError("Input manifest byte budget exceeded")
            manifest[name] = ["file", before.st_mode]
            files.append((path, before))
        else:
            raise ValueError("Special input needs fresh validation")
    # Enumerate/size-check first: a large HOME/PATH is rejected before file reads.
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)

    for path, before in files:
        hashed = hashlib.sha256()
        consumed = 0
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if identity(opened) != identity(before) or not stat.S_ISREG(opened.st_mode):
                raise ValueError("Input changed before reading")
            while True:
                if time.monotonic() > deadline:
                    raise ValueError("Input manifest read budget exceeded")
                block = stream.read(min(65536, before.st_size - consumed + 1))
                if not block:
                    break
                consumed += len(block)
                if consumed > before.st_size:
                    raise ValueError("Input grew during reading")
                hashed.update(block)
            if (consumed != before.st_size
                    or identity(os.fstat(stream.fileno())) != identity(before)):
                raise ValueError("Input changed during reading")
        manifest[str(path.absolute())].append(hashed.hexdigest())
    for path, before in observed.items():
        if time.monotonic() > deadline:
            raise ValueError("Input manifest verification budget exceeded")
        try:
            after = path.lstat()
        except FileNotFoundError:
            if before is not None:
                raise ValueError("Input disappeared during measurement") from None
        else:
            if before is None or identity(after) != identity(before):
                raise ValueError("Input changed during measurement")
    return manifest


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
    for entry in affected_tests._git(repo, "ls-files", "--stage", "-z", nul=True):
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
    untracked = affected_tests._git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        nul=True,
    )
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
    private = input_manifest(repo, dict(os.environ))
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
                "Mounts": sorted(c["Mounts"], key=lambda mount: mount["Destination"]),
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
    parser.add_argument(
        "--reuse-only", action="store_true", help="verify reuse without running checks"
    )
    args = parser.parse_args(argv)
    if args.reuse_only and (args.fresh or args.full):
        parser.error("--reuse-only cannot request fresh execution")
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
                    affected_tests._is_doc_or_data(p, repo) for p in changed
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
            if args.reuse_only:
                print("gate receipt: MISS; no checks run (--reuse-only)", flush=True)
                return 2
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
