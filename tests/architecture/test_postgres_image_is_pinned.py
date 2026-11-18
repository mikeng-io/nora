"""The Postgres image is one digest-pinned reference, stated once.

`pgvector/pgvector:pg18` is a floating tag: the same commit can pull a
different Postgres on different days, so tests, compose and CI would not be
measuring the same image. `deploy/postgres/image` is the one owner; tests, the
CI pull step and the bakeoff runbook all derive from it, and the compose files
— which cannot read a file — name the same literal that this module proves
still equals the owner. Recorded bakeoff results and generated openwiki pages
keep what they recorded: they are history, not pull instructions, so the sweep
does not read them. This file names the pattern it enforces, so it would flag
itself.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OWNER = "deploy/postgres/image"
PIN = re.compile(r"pgvector/pgvector:[\w.-]+@sha256:[0-9a-f]{64}")
REFERENCE = re.compile(r"pgvector/pgvector(?::[\w.-]+)?(?:@sha256:[0-9a-f]{64})?")
COMPOSE_FILES = ("docker-compose.yml", "deploy/memory-graph-spike/compose.yaml")
NOT_PULL_INSTRUCTIONS = (
    "evals/retrieval_bakeoff/results/",
    "openwiki/",
    "tests/architecture/test_postgres_image_is_pinned.py",
)


def owner() -> str:
    return (ROOT / OWNER).read_text(encoding="utf-8").strip()


def test_the_owner_is_a_digest_pinned_reference():
    assert PIN.fullmatch(owner()), f"{OWNER}: {owner()!r} is not <tag>@sha256:<digest>"


def test_every_compose_postgres_image_equals_the_owner():
    for path in COMPOSE_FILES:
        services = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))["services"]
        for name, service in services.items():
            image = service.get("image", "")
            if image.startswith("pgvector/"):
                assert image == owner(), f"{path}: {name} pins {image}, owner is {owner()}"


def test_no_live_reference_names_an_unpinned_pgvector_tag():
    offenders = []
    for name in subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines():
        if name.startswith(NOT_PULL_INSTRUCTIONS):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for match in REFERENCE.finditer(line):
                if not PIN.fullmatch(match.group(0)):
                    offenders.append(f"{name}:{lineno}: {match.group(0)}")
    assert not offenders, "unpinned pgvector/pgvector references:\n  " + "\n  ".join(
        offenders
    )
