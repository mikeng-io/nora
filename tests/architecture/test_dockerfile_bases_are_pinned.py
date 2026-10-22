"""Every image the release builds starts from a digest, and Dependabot keeps it current.

A mutable `FROM python:3.12-slim-trixie` means two builds of the same commit can differ and a
compromised or broken tag reaches the release unseen. The release records the built digest
afterwards, but the inputs were not pinned. The tag is kept beside the digest so the line stays
readable and Dependabot's docker ecosystem can propose the next digest. OS packages fetched by
`apt` at build time are NOT pinned by this (a dated snapshot feed would be); see the triage record.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FROM = re.compile(
    r"^FROM\s+(?:--\S+\s+)?(?P<image>\S+)(?:\s+AS\s+(?P<alias>\S+))?\s*$", re.I | re.M
)
PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")


def dockerfiles() -> list[Path]:
    names = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    return [
        ROOT / n
        for n in names
        if re.search(r"(^|/)Dockerfile[^/]*$|\.Dockerfile$", n) and not n.endswith("ignore")
    ]


def test_there_are_dockerfiles_to_check():
    assert len(dockerfiles()) >= 10


def test_every_from_is_pinned_to_a_digest():
    unpinned = []
    for path in dockerfiles():
        stages: set[str] = set()
        for match in FROM.finditer(path.read_text(encoding="utf-8")):
            image, alias = match["image"], match["alias"]
            is_stage = image.lower() in stages
            if not is_stage and image.lower() != "scratch" and not PINNED.search(image):
                unpinned.append(f"{path.relative_to(ROOT)}: FROM {image}")
            if alias:
                stages.add(alias.lower())
    assert not unpinned, "mutable base images (pin tag@sha256:<digest>):\n  " + "\n  ".join(
        unpinned
    )


def test_a_pinned_line_keeps_a_readable_tag_dependabot_can_move():
    for path in dockerfiles():
        for match in FROM.finditer(path.read_text(encoding="utf-8")):
            image = match["image"]
            if PINNED.search(image) and "ghcr.io/moghtech" not in image and "ubuntu@" not in image:
                assert ":" in image.split("@")[0], f"{path.name}: {image} has a digest but no tag"


def test_dependabot_watches_every_directory_that_has_a_dockerfile():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    docker = next(u for u in config["updates"] if u["package-ecosystem"] == "docker")
    watched = set(docker["directories"])
    needed = {"/" + str(p.parent.relative_to(ROOT)).replace(".", "") for p in dockerfiles()}
    needed = {n.rstrip("/") or "/" for n in needed}
    assert needed <= watched, f"Dependabot does not watch: {sorted(needed - watched)}"


def test_security_only_ecosystems_commit_as_a_visible_type_so_a_patch_releases():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    for update in config["updates"]:
        if update["package-ecosystem"] == "github-actions":
            continue
        message = update.get("commit-message", {})
        assert message.get("prefix") == "security" and message.get("include") == "scope", update[
            "package-ecosystem"
        ]


def test_node_images_stay_on_an_even_lts_line_and_dependabot_cannot_bump_the_major():
    for path in dockerfiles():
        for match in re.finditer(r"^FROM\s+node:(\d+)", path.read_text(encoding="utf-8"), re.M):
            assert int(match[1]) % 2 == 0, f"{path.relative_to(ROOT)}: node {match[1]} is not LTS"
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    docker = next(u for u in config["updates"] if u["package-ecosystem"] == "docker")
    assert {"dependency-name": "node", "update-types": ["version-update:semver-major"]} in docker[
        "ignore"
    ]
