"""MOVED to `evals.rebuilt.rubric` (`design/evaluation/02-the-rebuild.md` §3).

The rubric is one of the three things that survive the delete — *"what a
verdict MEANS does not depend on how the subject was built"* — so it moved to
the layer that survives, and this shim keeps the old suite running until Step 5
removes it.

A shim rather than a copy: two rubrics would drift, and a criterion whose
wording differs between two files is two different questions wearing one name.
"""

from evals.rebuilt.rubric import (
    CONTRADICTED,
    OPTIONAL_CRITERIA,
    PARTICIPATION_RUBRIC,
    RUBRIC,
    SOURCE_SCOPE,
    Criterion,
)

__all__ = [
    "CONTRADICTED",
    "OPTIONAL_CRITERIA",
    "PARTICIPATION_RUBRIC",
    "RUBRIC",
    "SOURCE_SCOPE",
    "Criterion",
]
