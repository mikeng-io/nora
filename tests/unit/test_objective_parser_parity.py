"""Shipped objectives have named readers in the source."""

from pathlib import Path

import nora

NORA_ROOT = Path(nora.__file__).resolve().parent
OBJECTIVES = NORA_ROOT / "dreamer" / "objectives"


def test_every_shipped_objective_is_read_by_something():
    """The inversion: the half that catches an objective nobody reads rather than
    a reader with no objective.

    Walking readers can only ever police the readers that exist. An objective
    file is a prompt shipped on the bind mount: it can be added, renamed or
    orphaned with nothing in the codebase mentioning it, and every counter in
    every tier stays healthy because no tier was ever asked to run it. So the
    directory is the population and the code is checked against it, not the
    other way round.

    The floor is a quoted reference to the objective name in the Python source.
    """
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in NORA_ROOT.rglob("*.py")
    )
    shipped = sorted(OBJECTIVES.glob("*.md"))
    assert shipped, "no shipped Dreamer objectives found"
    orphans = [
        path.stem for path in shipped
        if f'"{path.stem}"' not in source and f"'{path.stem}'" not in source
    ]
    assert not orphans, (
        f"objectives no code names: {orphans}. A prompt shipped on the bind "
        f"mount that nothing loads is inert, and no counter can say so."
    )


def test_the_orphan_check_can_actually_fire():
    """The run-that-ran control for the check above: a name that IS in the
    directory but not in the source has to come back as an orphan, or the
    assertion is `not []` over a list that is empty for the wrong reason."""
    source = "nothing here names anything"
    orphans = [
        path.stem for path in sorted(OBJECTIVES.glob("*.md"))
        if f'"{path.stem}"' not in source
    ]
    # Every objective shipped in the directory is an orphan against this source.
    assert len(orphans) == len(list(OBJECTIVES.glob("*.md"))) >= 3
