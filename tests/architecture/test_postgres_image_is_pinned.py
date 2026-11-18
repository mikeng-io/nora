"""The Postgres image is one digest-pinned reference, stated once.

`pgvector/pgvector:pg18` is a floating tag: the same commit can pull a
different Postgres on different days, so tests, compose and CI would not be
measuring the same image. `deploy/postgres/image` is the one owner; tests, the
CI pull step and the bakeoff runbook all derive from it, and the compose files
— which cannot read a file — name the same literal that this module proves
still equals the owner. The sweep compares the DIGEST of every reference it
finds against the owner's, so a literal pinned to a different digest fails
just as an unpinned tag does, and it asserts the known literals are still
there to be compared — a sweep that finds nothing fails.

Skipped on purpose (`NOT_PULL_INSTRUCTIONS`): `evals/retrieval_bakeoff/results/`
is recorded history of what ran, `openwiki/` is generated output, and this
file names the pattern it enforces, so it would flag itself.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OWNER = "deploy/postgres/image"
PIN = re.compile(r"pgvector/pgvector:[\w.-]+@sha256:[0-9a-f]{64}")
REFERENCE = re.compile(r"pgvector/pgvector(?::[\w.-]+)?(?:@sha256:[0-9a-f]{64})?")
DIGEST = re.compile(r"@sha256:([0-9a-f]{64})$")
COMPOSE_FILES = ("docker-compose.yml", "deploy/memory-graph-spike/compose.yaml")
NOT_PULL_INSTRUCTIONS = (
    "evals/retrieval_bakeoff/results/",
    "openwiki/",
    "tests/architecture/test_postgres_image_is_pinned.py",
)
# The literals the sweep must see to be non-vacuous. The CI pull and Python
# consumers derive from the owner instead, so ci.yml is not in this list.
KNOWN_REFERENCE_FILES = {
    "deploy/postgres/image",
    "docker-compose.yml",
    "deploy/memory-graph-spike/compose.yaml",
    "scripts/mutations/db-repair.json",
}


def owner() -> str:
    return (ROOT / OWNER).read_text(encoding="utf-8").strip()


def _digest(reference: str) -> str | None:
    match = DIGEST.search(reference)
    return match.group(1) if match else None


def _applicable_files(root: Path) -> Iterator[tuple[str, str]]:
    names = subprocess.check_output(
        ["git", "ls-files"], cwd=root, text=True
    ).splitlines()
    for name in names:
        if name.startswith(NOT_PULL_INSTRUCTIONS):
            continue
        path = root / name
        if path.is_file():
            yield name, path.read_text(encoding="utf-8", errors="replace")


def _references(files: Iterable[tuple[str, str]]) -> list[tuple[str, int, str]]:
    """Every (relpath, line, reference) a reader could mistake for the image."""
    return [
        (name, lineno, match.group(0))
        for name, text in files
        for lineno, line in enumerate(text.splitlines(), 1)
        for match in REFERENCE.finditer(line)
    ]


def _problems(
    hits: Iterable[tuple[str, int, str]], owner_digest: str
) -> list[str]:
    hits = list(hits)
    if not hits:
        return ["no pgvector/pgvector reference found — the sweep measured nothing"]
    return [
        f"{name}:{lineno}: {reference}"
        for name, lineno, reference in hits
        if _digest(reference) != owner_digest
    ]


def test_the_owner_is_a_digest_pinned_reference():
    assert PIN.fullmatch(owner()), f"{OWNER}: {owner()!r} is not <tag>@sha256:<digest>"


def test_every_compose_postgres_image_equals_the_owner():
    for path in COMPOSE_FILES:
        services = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))["services"]
        for name, service in services.items():
            image = service.get("image", "")
            if image.startswith("pgvector/"):
                assert image == owner(), f"{path}: {name} pins {image}, owner is {owner()}"


def test_every_live_reference_pins_the_owners_digest():
    hits = _references(_applicable_files(ROOT))
    seen = {name for name, _, _ in hits}
    assert seen >= KNOWN_REFERENCE_FILES, (
        f"the sweep lost sight of a known reference (saw {sorted(seen)})"
    )
    problems = _problems(hits, _digest(owner()))
    assert not problems, "references not pinning the owner's digest:\n  " + "\n  ".join(
        problems
    )


def test_the_sweep_fails_closed_on_each_bad_shape():
    owner_digest = _digest(owner())
    assert _problems([], owner_digest)                          # found nothing at all
    assert _problems(
        _references([("x.yml", "docker pull pgvector/pgvector:pg18")]), owner_digest
    )                                                            # an unpinned tag
    assert _problems(
        _references(
            [("x.yml", f"image: pgvector/pgvector:pg18@sha256:{'0' * 64}")]
        ),
        owner_digest,
    )                                                            # a different valid digest
    assert not _problems(
        _references([("x.yml", f"image: {owner()}")]), owner_digest
    )                                                            # positive control
