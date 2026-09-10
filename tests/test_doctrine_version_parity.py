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
    # v8 (2026-09-10): the group is «this group», not one named room. The same
    # doctrine directory serves six rooms across three workspaces, and the
    # prompt never renders a room label — so a hard-coded name was the only
    # group identity she got, and it was wrong in five of the six.
    "identity": (8, "0d496e3515c0ab030a341cc2bef8df1da22dc3088f8f1e169d4371d5e7bbeb55"),
    # v19 (2026-09-10): same room-name removal as identity v8.
    "voice": (19, "f85e5206f79ce51cf84b98fdf5aab82a00414e92cde3dcce09ccd2624031b4e0"),
    # conduct v25 / silence v3 (2026-09-10, `14a55ab8`): that commit edited both
    # bodies and bumped both versions in their front matter, and never came back
    # here. So the contract held in the direction that matters -- the version a
    # human reads did move with the body -- and only the pin was left behind.
    # Repinned to the version each file already carries; NOT bumped again, because
    # nothing has moved since.
    "silence": (3, "9fd50a24d311348710b0c199a6931f4a50a3d1d448fee19789b4efa77af750c3"),
    "conduct": (25, "1e8dcbd8171a73ed74343bf639ef0f558d5217255060232d5cc8a569ba2e358b"),
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
    # v9 (2026-09-10): R-14 restated around the ROOM rather than the summons.
    # The file is served to four occasions and named only two ways a turn can
    # open, so on a proactive turn the highest-authority file in the prompt
    # forbade the turn — and proactive silence is the healthy outcome, so that
    # would never have shown up as a failure.
    "constraints": (9, "9a25c5e95b95b426a4e6d2f74bc4557ced8d36b7f29f33ad66484d688960194a"),
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
