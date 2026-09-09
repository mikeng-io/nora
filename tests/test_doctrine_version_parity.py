"""A doctrine file's body may not change without its `version` changing.

**Bought by a live defect, 2026-08-27.** A section was added to `conduct.md`
and `version: 13` was left alone. The `sha256` moved on its own — it is
computed over the body — so provenance survived, and that is exactly what made
the miss quiet: nothing failed. What broke is the number a human reads.
`doctrine_versions={'conduct': 13, ...}` is logged on EVERY tag and is the
operator-facing handle for «which doctrine is she running», so version 13
silently came to mean two different documents depending on when you looked.

This is `docs/CONVENTIONS.md`'s unguarded-copy class with the copy and the
original in ONE file: `version` is a restatement of «what this document is»,
it stays plausible after the body moves, and it fails somewhere else later —
in a log line, during an incident, when somebody is trying to work out what she
was running at the time.

**Why a pin and not a rule.** «Bump the version when you change the body» is
unenforceable by inspection: no test can know which version is the RIGHT one.
What a test can do is refuse to let the body move while the version stands
still. So the table below records both, and a legitimate edit updates two
things on purpose — the same shape as `tests/infra/test_schema_parity.py`,
and the same reason.

`conduct.md` carries `change_policy: agent-may-edit`, so this fires often BY
DESIGN. That is not friction to be tuned away: an agent editing doctrine is
precisely the case where nobody is around to notice the version did not move.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nora.doctrine.loader import parse_section

DOCTRINE_DIR = Path(__file__).resolve().parents[1] / "nora" / "doctrine"

# name -> (version, sha256 of the BODY as `parse_section` computes it).
#
# To change a doctrine file: edit it, bump its `version`, run this test, and
# paste the sha it reports. Updating the sha WITHOUT bumping the version is the
# one move this table exists to make somebody stop and think about.
PINNED: dict[str, tuple[int, str]] = {
    "identity": (7, "dd8390f3c03f2848fa47a2454efd03ab3c9747037a73ade016040b1c841c5a09"),
    "voice": (18, "e1e0195dc4d8d7cc5cbc04d0800b1481d56a7956f5b02afdb631dce8bf509db2"),
    "silence": (2, "55abb8beab124e1d7f696ebcdcd577093b0afa7d063c031cc01e4dd4e3e1b6cc"),
    "conduct": (24, "a304ae577f27a9fc547896185f13b4ac382f77b5643af03868a97af235e4136a"),
    # NOT Nora's: the Relevance Gate's instruction, pinned here like any other
    # authored file so a silent edit to what the gate is told is impossible.
    # v5 (2026-09-10, issue #144): the digest v4 pointed at is not shipped —
    # measured over the suite's 40 windows it made the gate worse — so the
    # pointer is gone and the five shapes stand on their own again, with one
    # line saying they are shapes to judge and not a menu to match. Same
    # accuracy as v3 and v4 (3 false NO_GO, safety 6/6, no dead capability),
    # 189 fewer prompt tokens, and it no longer names a block that is absent.
    "participation": (
        5, "461d4b663b1b213b52c77ac0b549ba6b039883ff13b64f2d2963072b4969fb48",
    ),
    "participation-conduct": (
        1, "21512fb426ba2ac017b550d52f21d5ee40f58cb4e73e9d309389113db149458b",
    ),
    "constraints": (8, "f2b59db09d5892e299f5ddcf0f2d89eb38a6bee71bf03d15c03fa71cbdcdffb7"),
}


def _sections() -> dict[str, object]:
    return {
        s.name: s
        for s in (parse_section(p) for p in sorted(DOCTRINE_DIR.glob("*.md")))
    }


def test_every_authored_doctrine_file_is_pinned():
    """A new doctrine file joins the pin, or it is ungoverned by this gate and
    nobody finds out until its version has been wrong for a while."""
    assert set(_sections()) == set(PINNED), (
        "a doctrine file was added or removed; add it to PINNED with its "
        "version and body sha, or remove its row"
    )


@pytest.mark.parametrize("name", sorted(PINNED))
def test_the_body_has_not_moved_without_the_version_moving(name: str):
    section = _sections()[name]
    pinned_version, pinned_sha = PINNED[name]

    assert section.version == pinned_version, (
        f"{name}.md is at version {section.version}, pinned at "
        f"{pinned_version}. If you bumped it deliberately, update PINNED to "
        f"({section.version}, {section.sha256!r})."
    )
    if not pinned_sha:
        pytest.skip(
            f"{name} has no sha pinned yet — record {section.sha256!r} to arm it"
        )
    assert section.sha256 == pinned_sha, (
        f"{name}.md's BODY changed but `version: {section.version}` did not.\n"
        f"  expected sha {pinned_sha}\n"
        f"       got sha {section.sha256}\n"
        "`doctrine_versions` is logged on every tag and is what a human reads "
        "to know which doctrine was running. Bump the version in the file's "
        "front matter, then update PINNED with the new (version, sha)."
    )


def test_the_pinned_sha_is_computed_the_way_the_loader_computes_it():
    """Guards the pin itself. If this test hashed the WHOLE file while the
    loader hashes the body, every sha here would be wrong in a way that still
    compared equal to itself — a pin that only ever agrees with its own method
    is not measuring the loader."""
    section = _sections()["conduct"]
    raw = (DOCTRINE_DIR / "conduct.md").read_text(encoding="utf-8")
    whole_file = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    assert section.sha256 != whole_file, (
        "loader.sha256 must be over the BODY, not the whole file — if this "
        "ever passes, the pin above is measuring the wrong bytes"
    )
