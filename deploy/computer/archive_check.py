#!/usr/bin/env python3
"""Refuse a /workspace archive that could write outside it (DEC-263).

`nora-computer restore` extracts an archive INSIDE the VM as root. The archive is
a file on the host's disk, so it is not trusted just because Nora made it: a room
runs commands as its own user and the nightly tar reads whatever that user left,
and an archive someone swapped or hand-made is the same bytes to `tar`. GNU tar
strips a leading `/` and refuses `..` by default, but not everything below, and
a root extraction is the wrong place to rely on defaults, so the archive is read
here first, on the host, and refused whole on the first thing it should not
carry:

- a member that is not `.` or a room directory at the top: only the 16 to 64
  lowercase hex characters a scope is made of (`computerd`'s `SCOPE_RE`);
- an absolute name, or any `..` component;
- a type other than a regular file, directory, symlink or hard link (a device
  node or a fifo has no business in a room's files);
- a symlink or hard link whose target is absolute or resolves outside the SAME
  room's directory (a link to another room, or to anything of the VM's, is the
  whole attack: a later member written "through" it lands there);
- a member whose path passes through a symlink that is itself in the archive.

The VM's copy has the same digest as the host's (the restore compares them
before extracting), so what is checked here is what is extracted.

    archive_check.py FILE        exit 0 clean, 1 refused (reasons on stdout), 2 unreadable
"""

from __future__ import annotations

import posixpath
import re
import sys
import tarfile

SCOPE_RE = re.compile(r"^[0-9a-f]{16,64}$")


def problems(path: str) -> list[str]:
    found: list[str] = []
    symlinks: set[str] = set()
    with tarfile.open(path, "r:*") as tar:
        for member in tar:
            name = member.name
            norm = posixpath.normpath(name)
            if norm == ".":
                # The archive root is the one member that lands on the staging
                # child itself. Accept its mode, reject any other type: a `.`
                # symlink or hard link would aim root extraction elsewhere.
                if not member.isdir():
                    found.append(f"{name!r}: the archive root is not a directory")
                continue
            if name.startswith("/") or ".." in name.split("/"):
                found.append(f"{name!r}: an absolute or .. path")
                continue
            parts = norm.split("/")
            if not SCOPE_RE.match(parts[0]):
                found.append(f"{name!r}: not inside a room directory")
                continue
            room = parts[0]
            if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
                found.append(f"{name!r}: type {member.type!r} is not a file, directory or link")
                continue
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in symlinks:
                    found.append(f"{name!r}: written through the symlink {'/'.join(parts[:i])!r}")
                    break
            if member.issym() or member.islnk():
                target = member.linkname
                if member.issym():
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(norm), target))
                else:
                    resolved = posixpath.normpath(target)
                inside = (
                    not target.startswith("/")
                    and not resolved.startswith("..")
                    and (resolved == room or resolved.startswith(room + "/"))
                )
                if not inside:
                    kind = "symlink" if member.issym() else "hard link"
                    found.append(f"{name!r}: {kind} to {target!r} leaves its room")
                if member.issym():
                    symlinks.add(norm)
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: archive_check.py FILE", file=sys.stderr)
        return 2
    try:
        found = problems(argv[1])
    except (tarfile.TarError, OSError) as exc:
        print(f"unreadable: {exc}")
        return 2
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
