"""`identity.example.toml` is the only committed record of what an
`identity.toml` may say, and it is the repo's designated source of PLACEHOLDER
identifiers (AGENTS.md, «Never paste production rows into a fixture»). Both
jobs fail the same way: an example that names a key the loader would refuse is
a trap an operator copies, and it fails at THEIR startup rather than here.

The file is commented out end to end — which is correct, since a live
`[[persons.…]]` block would make a fresh clone claim to know somebody — so no
parser sees it and a typo'd kind could sit there indefinitely.

What this reads is the COMMENTED example blocks, uncommented, and it checks
every key against the same vocabulary `nora.persons` enforces at startup:
`id_kinds(platform)` for matchable ids, `metadata_keys(platform)` plus the
account `label` for the readable rest. Anything else is a key that would be
refused, and the message names it.

Scoped to `architecture/` because it is a property of the SOURCE — a file in
the tree — and needs no runtime, no config fixture and no I/O beyond reading
it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from nora.domain.identity import ACCOUNT_LABEL_KEY, id_kinds, metadata_keys

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "identity.example.toml"

#: `[[persons.<person>.<platform>]]`, as the example writes it behind a `# `.
_BLOCK = re.compile(r"^#\s*\[\[persons\.([A-Za-z0-9_-]+)\.([a-z0-9_]+)\]\]\s*$")
#: `# key = "value"` — the lines inside such a block.
_ENTRY = re.compile(r"^#\s*([a-z0-9_]+)\s*=")


def _example_blocks() -> list[tuple[str, str, list[str]]]:
    """`[(person, platform, [key, …]), …]` from the commented example blocks."""
    blocks: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    for line in EXAMPLE.read_text().splitlines():
        stripped = line.strip()
        header = _BLOCK.match(stripped)
        if header:
            current = (header.group(1), header.group(2), [])
            blocks.append(current)
            continue
        if current is None:
            continue
        entry = _ENTRY.match(stripped)
        if entry:
            current[2].append(entry.group(1))
        elif not stripped.startswith("#") or stripped == "#":
            current = None          # the block ended at a blank or prose line
    return blocks


def test_the_example_parses_as_toml_at_all():
    """The live (uncommented) part of the file. A fresh clone loads this."""
    tomllib.loads(EXAMPLE.read_text())


def test_the_example_shows_at_least_one_block_per_platform_it_documents():
    """Positive control: a detector that stopped matching the comment style
    would find NO blocks and every check below would pass vacuously."""
    platforms = {platform for _person, platform, _keys in _example_blocks()}

    assert "whatsapp" in platforms
    assert "instagram" in platforms, (
        "identity.example.toml no longer shows an Instagram account block; it is "
        "the only committed record of the fbid/igid pair an operator has to write"
    )


@pytest.mark.parametrize("block", _example_blocks(), ids=lambda b: f"{b[0]}.{b[1]}")
def test_every_example_key_is_one_the_loader_would_accept(block):
    """A key outside the platform's vocabulary is a typo, and a typo must be a
    startup error rather than a silently-ignored line (`nora.domain.identity`).
    An EXAMPLE carrying one is worse: it is refused at the operator's startup,
    not at ours."""
    person, platform, keys = block
    allowed = id_kinds(platform) | metadata_keys(platform) | {ACCOUNT_LABEL_KEY}

    assert allowed, f"{platform} has no declared vocabulary at all"
    for key in keys:
        assert key in allowed, (
            f"identity.example.toml shows `{key}` under [[persons.{person}."
            f"{platform}]], which nora.persons would refuse; allowed: {sorted(allowed)}"
        )


def test_the_instagram_example_shows_BOTH_numeric_ids():
    """They answer different questions and neither is derivable from the
    other: `fbid` is the messaging identity a mention carries, `igid` is the
    public id the story reads take. An example showing one teaches the
    operator to record one."""
    keys = {
        key
        for _person, platform, block_keys in _example_blocks() if platform == "instagram"
        for key in block_keys
    }

    assert "fbid" in keys
    assert "igid" in keys


def test_the_instagram_example_never_presents_a_handle_as_a_MATCHABLE_id():
    """`username` is allowed in the file — it is what keeps identity.toml
    legible — and it must be a metadata key rather than an id kind, because a
    handle is renameable and its next holder would resolve AS this person."""
    assert "username" in metadata_keys("instagram")
    assert "username" not in id_kinds("instagram")
