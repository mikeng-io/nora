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

    **NO PATH CONFIGURED vetoes nothing extra**, unlike `policy.toml`: the
    rules in code are the floor and this only NARROWS what promotes, so no
    `veto_file` is a room that has not needed one — whereas an absent
    `policy.toml` would mean nobody decided who may talk to her. The asymmetry
    is the point.

    **A PATH THAT IS NOT THERE REFUSES TO START**, and that is a different
    case which this collapsed into the first for its whole life: `path is None
    or not path.is_file()` treated «the operator wrote no path» and «the
    operator wrote a path and it is not there» identically, so a bind mount
    that failed to attach, a typo, or a broken symlink meant promotion minted
    entities for every string the room had decided is not a thing — silently,
    with no counter that could say so. The paragraph below already said this
    was the intent; the code did the opposite.

    A path that exists and cannot be READ raises too, from `read_text`, and
    for the same reason: the operator wrote a path, so reading nothing from it
    is a wiring gap and not a choice.
    """
    if path is None:
        return frozenset()
    if not path.is_file():
        raise FileNotFoundError(
            f"[facts] veto_file points at {path}, which is not a readable "
            f"file. An absent veto list and a veto list that failed to mount "
            f"are different facts: the first is a room that has not needed "
            f"one, the second is every string this room decided is not a "
            f"thing being promoted to an entity with nothing to say so."
        )
    return frozenset(
        stripped
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.split("#", 1)[0].strip())
    )
