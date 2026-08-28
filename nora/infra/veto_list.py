"""The operator-owned promotion veto list (`21-substrate-contract` §10).

Its CONTENTS are configuration — the extra object strings THIS room decides are
not things. Its existence, ownership and versioning are the design's decision,
which is why it is a file the operator holds rather than a constant in code.

**Separate from `nora/app/composition.py`, which is where it started.**
Composition composes; a file format is a reader's job, and the read had put a
`read_text` in the one module that must not have one — `tests/test_boundaries`
enforces a single reader for doctrine files by scanning for exactly that pair,
and a composition that parses anything is how that boundary erodes.
"""

from __future__ import annotations

from pathlib import Path


def load_veto_list(path: Path | None) -> frozenset[str]:
    """One string per line; `#` comments and blank lines ignored.

    **A missing file vetoes nothing extra rather than refusing to start**,
    unlike `policy.toml`: the rules in code are the floor and this only NARROWS
    what promotes, so an absent file is a room that has not needed one —
    whereas an absent `policy.toml` would mean nobody decided who may talk to
    her. The asymmetry is the point; both are "a file is missing" and only one
    of them is a decision nobody made.

    An unreadable file raises rather than degrading: the operator wrote a path,
    so reading nothing from it is a wiring gap and not a choice.
    """
    if path is None or not path.is_file():
        return frozenset()
    return frozenset(
        stripped
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.split("#", 1)[0].strip())
    )
