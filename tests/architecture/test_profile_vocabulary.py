"""Retired typed-tier vocabulary survives only in enumerated historical uses."""
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / "tests/fixtures/profile-vocabulary/retired-name-allowlist.json"
# Segment/camel boundaries, never the prefix of personal or personality.
RETIRED = re.compile(r"persona(?=$|[^a-z]|s(?:$|[^a-z]))|"
                     r"Persona(?=$|[^a-z]|s(?:$|[^a-z]))|"
                     r"PERSONA(?=$|[^A-Z]|S(?:$|[^A-Z]))")
TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def vocabulary_hits():
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode().split("\0")
    found = {}
    for relative in sorted(set(paths)):
        path = ROOT / relative
        if (not relative or path == ALLOWLIST or not path.is_file() or path.is_symlink()
                or relative.startswith(".codebase-memory/")
                or relative in {".githooks/post-checkout", ".githooks/post-commit",
                                ".githooks/post-merge"}):
            continue
        try:
            content = path.read_text()
        except UnicodeError:
            continue
        matches = Counter(token for token in TOKEN.findall(content) if RETIRED.search(token))
        if RETIRED.search(relative):
            matches["<path> " + relative] += 1
        if matches:
            found[relative] = dict(sorted(matches.items()))
    return found


@pytest.mark.parametrize("word", ["personal", "personalisation", "personality",
                                 "Personal", "PERSONALITY", "PersonAvatar"])
def test_ordinary_words_are_not_retired_identifiers(word):
    assert not RETIRED.search(word)


@pytest.mark.parametrize("word", ["persona", "personas", "persona_line", "_persona_key",
                                 "SqlPersonaStore", "PERSONA_LINE", "persona-person"])
def test_retired_identifier_forms_are_caught(word):
    assert RETIRED.search(word)


def test_no_retired_tier_identifier_is_reintroduced():
    allowlist = json.loads(ALLOWLIST.read_text())
    assert all(item["reason"].strip() for item in allowlist.values())
    assert vocabulary_hits() == {path: item["matches"] for path, item in allowlist.items()}
