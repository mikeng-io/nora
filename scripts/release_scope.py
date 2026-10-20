"""Release only when a user-facing commit changes runtime inputs.

Read the existing release tag, commit subjects and actual changed paths. A docs
commit mislabeled feat/fix is insufficient. No tag or a failed git read is an
error, never permission to release. Runtime doctrine/skills are executable inputs.
"""

from __future__ import annotations

import re
import shutil
import subprocess

VISIBLE = re.compile(r"^(feat|fix|security|perf|change|doctrine|revert)(\([^)]*\))?!?:")
RUNTIME_DIRS = ("nora/", "app/", "site/", "docs-site/", "sidecars/", "deploy/")
RUNTIME_FILES = {
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
    "pyproject.toml",
    "uv.lock",
    "nora/documents/catalog-prompt.md",
    "alembic.ini",
    "config.example.toml",
    "identity.example.toml",
    "policy.example.toml",
    "scripts/standing_edit.py",
    "scripts/check_version_stamp.py",
    "scripts/gen_version_manifest.py",
    "scripts/build-image.sh",
}


def git(*args: str) -> str:
    return subprocess.check_output([shutil.which("git") or "/usr/bin/git", *args], text=True)  # noqa: S603


def eligible(base: str, head: str = "HEAD") -> bool:
    for row in git("log", "--no-merges", "--format=%H %s", f"{base}..{head}").splitlines():
        sha, subject = row.split(" ", 1)
        if not (VISIBLE.match(subject) or subject.startswith('Revert "')):
            continue
        paths = git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        if any(
            path in RUNTIME_FILES
            or (path.startswith(RUNTIME_DIRS) and not path.lower().endswith(".md"))
            or path.startswith(
                (
                    "nora/doctrine/",
                    "nora/skills/",
                    "nora/dreamer/doctrine/",
                    "nora/dreamer/objectives/",
                    "nora/dreamer/preludes/",
                    "nora/dreamer/skills/",
                )
            )
            for path in paths
        ):
            return True
    return False


if __name__ == "__main__":
    base = git("describe", "--tags", "--first-parent", "--abbrev=0", "--match", "v[0-9]*").strip()
    print(f"eligible={str(eligible(base)).lower()}")
