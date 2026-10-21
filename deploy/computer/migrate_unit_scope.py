"""Offline, privileged workspace migration. No policy names/room IDs enter the VM.

inventory -> plan -> apply -> verify -> retire. Admission must remain stopped
through host metadata binding and readback. The plan and receipt live OUTSIDE
/workspace and must be retained with the backups. Copy first, verify all moves,
then retire sources separately. A partial copy is a conflict, never overwritten.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from pathlib import Path
from typing import Any

from computerd import Computer, scope_user

DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
SCOPE = re.compile(r"[0-9a-f]{32}\Z")


def snapshot(fd: int, *, owner: tuple[int, int] | None = None) -> dict[str, Any]:
    """Inventory bytes, paths, modes and symlink text without following links."""
    out: dict[str, Any] = {}

    def walk(dfd: int, prefix: str) -> None:
        for name in sorted(os.listdir(dfd)):
            path = prefix + name
            info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            if owner is not None and (info.st_uid, info.st_gid) != owner:
                raise ValueError(f"wrong ownership: {path}")
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                out[path] = {"kind": "directory", "mode": mode}
                child = os.open(name, DIR, dir_fd=dfd)
                try:
                    walk(child, path + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(f, "rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                out[path] = {"kind": "file", "mode": mode, "size": info.st_size, "sha256": digest}
            elif stat.S_ISLNK(info.st_mode):
                out[path] = {"kind": "link", "target": os.readlink(name, dir_fd=dfd)}
            else:
                raise ValueError(f"unsupported file (stop its owner first): {path}")

    walk(fd, "")
    return out


def _read(root: Path, parts: list[str], owner: tuple[int, int] | None = None) -> dict:
    fd = os.open(root, DIR)
    try:
        for part in parts:
            child = os.open(part, DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if owner is not None and (info.st_uid, info.st_gid) != owner:
            raise ValueError("wrong root ownership")
        return {"mode": stat.S_IMODE(info.st_mode), "entries": snapshot(fd, owner=owner)}
    finally:
        os.close(fd)


def inventory(root: Path) -> list[str]:
    fd = os.open(root, DIR)
    try:
        scopes = sorted(s for s in os.listdir(fd) if s not in {".tmux", ".unit-migration-sources"})
        if any(not SCOPE.fullmatch(s) for s in scopes):
            raise ValueError("unrecognised workspace entry; operator mapping required")
        for scope in scopes:
            child = os.open(scope, DIR, dir_fd=fd)
            os.close(child)
        return scopes
    finally:
        os.close(fd)


def plan(root: Path, mapping: dict[str, str]) -> dict:
    if any(not SCOPE.fullmatch(s) for pair in mapping.items() for s in pair):
        raise ValueError("only opaque scope hashes may enter the VM")
    if set(mapping) & set(mapping.values()):
        raise ValueError("source/destination scope collision")
    unknown = set(inventory(root)) - mapping.keys() - set(mapping.values())
    if unknown:
        raise ValueError(f"unmapped scopes held untouched: {sorted(unknown)}")
    return {"mapping": mapping, "sources": {s: _read(root, [s]) for s in mapping}}


def _copy(src: int, dst: int, ids: tuple[int, int]) -> None:
    """Exclusive, descriptor-relative copy, including hidden files and links."""
    for name in sorted(os.listdir(src)):
        info = os.stat(name, dir_fd=src, follow_symlinks=False)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            os.mkdir(name, 0o700, dir_fd=dst)
            a, b = os.open(name, DIR, dir_fd=src), os.open(name, DIR, dir_fd=dst)
            try:
                _copy(a, b, ids)
                os.fchown(b, *ids)
                os.fchmod(b, mode)
            finally:
                os.close(a)
                os.close(b)
        elif stat.S_ISREG(info.st_mode):
            a = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src)
            b = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dst
            )
            with os.fdopen(a, "rb") as source, os.fdopen(b, "wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fchown(target.fileno(), *ids)
                os.fchmod(target.fileno(), mode)
                os.fsync(target.fileno())
        elif stat.S_ISLNK(info.st_mode):
            os.symlink(os.readlink(name, dir_fd=src), name, dir_fd=dst)
            os.chown(name, *ids, dir_fd=dst, follow_symlinks=False)
        else:
            raise ValueError(f"unsupported file: {name}")
    os.fsync(dst)


def _drained(mapping: dict[str, str]) -> None:
    import pwd

    uids = set()
    for scope in set(mapping) | set(mapping.values()):
        try:
            uids.add(pwd.getpwnam(scope_user(scope)).pw_uid)
        except KeyError:
            continue
    fd = os.open("/proc", DIR)
    try:
        for name in os.listdir(fd):
            if not name.isdecimal():
                continue
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue  # process exited during the read
            if stat.S_ISDIR(info.st_mode) and info.st_uid in uids:
                raise ValueError("scope not drained; stop its browser/tmux/processes first")
    finally:
        os.close(fd)


def migrate(
    root: Path, document: dict, journal: Path, *, action: str, isolate: bool = True
) -> dict:
    """Run only while computerd/Nora admission and old scope processes are stopped."""
    if isolate and os.geteuid() != 0:
        raise ValueError("migration requires root")
    if journal.resolve().is_relative_to(root.resolve()):
        raise ValueError("journal must be outside the working disk")
    mapping = document["mapping"]
    if any(not SCOPE.fullmatch(s) for pair in mapping.items() for s in pair):
        raise ValueError("invalid scope")
    if set(mapping) & set(mapping.values()) or set(document["sources"]) != set(mapping):
        raise ValueError("invalid migration plan")
    computer = Computer(root=root, token=secrets.token_hex(16), isolate_scopes=isolate)
    if isolate:
        _drained(mapping)
    signature = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    state = (
        json.loads(journal.read_text())
        if journal.exists()
        else {
            "plan_sha256": signature,
            "completed": [],
            "retired": [],
        }
    )
    if state["plan_sha256"] != signature:
        raise ValueError("journal belongs to another plan")

    def save() -> None:
        temporary = journal.with_suffix(".next")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, journal)

    # Preflight every source/destination before touching any source.
    for old, new in mapping.items():
        expected = document["sources"][old]
        if old not in state["retired"] and _read(root, [old]) != expected:
            raise ValueError(f"source changed: {old}")
        destination = root / new / "rooms" / old
        if destination.exists() or destination.is_symlink():
            if old not in state["completed"]:
                raise ValueError(f"conflicting or interrupted destination: {old}")
        elif action != "apply" or old in state["completed"]:
            raise ValueError(f"missing destination: {old}")
    if action == "apply":
        save()
        for old, new in mapping.items():
            if old in state["completed"]:
                continue
            ids = computer.owner.ids(new) if isolate else (os.getuid(), os.getgid())
            if ids is None:
                raise ValueError("scope ownership is unavailable")
            with computer._dir(new, ["rooms"], create=True) as parent:
                os.mkdir(old, 0o700, dir_fd=parent)
                dst = os.open(old, DIR, dir_fd=parent)
                src = os.open(root / old, DIR)
                try:
                    _copy(src, dst, ids)
                    os.fchown(dst, *ids)
                    os.fchmod(dst, document["sources"][old]["mode"])
                finally:
                    os.close(dst)
                    os.close(src)
            if _read(root, [new, "rooms", old], ids) != document["sources"][old]:
                raise ValueError(f"copy verification failed: {old}")
            state["completed"].append(old)
            save()
    # Verify ALL content and ownership before any source can be retired.
    for old, new in mapping.items():
        ids = computer.owner.ids(new) if isolate else (os.getuid(), os.getgid())
        if _read(root, [new, "rooms", old], ids) != document["sources"][old]:
            raise ValueError(f"destination changed: {old}")
        unit_root = (root / new).stat(follow_symlinks=False)
        if stat.S_IMODE(unit_root.st_mode) != 0o700 or (unit_root.st_uid, unit_root.st_gid) != ids:
            raise ValueError("unit root must stay private and owned by its unit")
    if action == "retire":
        for old in mapping:
            if old in state["retired"]:
                continue
            # Rename on the same disk; never delete. This root-only directory
            # is outside every unit scope, including when /workspace is a mount.
            root_fd = os.open(root, DIR)
            try:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(".unit-migration-sources", 0o700, dir_fd=root_fd)
                quarantine = os.open(".unit-migration-sources", DIR, dir_fd=root_fd)
                try:
                    os.fchown(quarantine, os.getuid(), os.getgid())
                    os.fchmod(quarantine, 0o700)
                    try:
                        os.stat(old, dir_fd=quarantine, follow_symlinks=False)
                    except FileNotFoundError:
                        os.rename(old, old, src_dir_fd=root_fd, dst_dir_fd=quarantine)
                    else:
                        raise ValueError("quarantined source exists; reconcile interruption")
                finally:
                    os.close(quarantine)
            finally:
                os.close(root_fd)
            state["retired"].append(old)
            save()
    return {
        "plan_sha256": signature,
        "verified": True,
        "mapping": mapping,
        "retired": state["retired"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inventory", "plan", "apply", "verify", "retire"])
    parser.add_argument("--root", type=Path, default=Path("/workspace"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    if args.action == "inventory":
        result = inventory(args.root)
    else:
        document = json.loads(args.input.read_text())
        result = (
            plan(args.root, document)
            if args.action == "plan"
            else migrate(args.root, document, args.journal, action=args.action)
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
