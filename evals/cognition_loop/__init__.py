"""The Cognition loop eval (`docs/engineering/evaluation-cognition-loop.md`).

Runs a real Cognition session — the deployment's own
`CognitionSessionRunner` in the live lane — over a sequence of recorded
wakes of one unit, inside a per-case sandbox restored at the first wake
and advanced by copying only the world's Record rows between wakes. Each
wake is diffed across every owned store and judged fail-closed by two
independent judges.
"""

from evals.cognition_loop.cases import Case, Expectation, load_case
from evals.cognition_loop.verdicts import (
    ERROR,
    FAIL,
    PASS,
    REVIEW,
    JudgeAnswer,
    expectation_verdict,
    rollup,
    validate_citations,
)

__all__ = [
    "ERROR",
    "FAIL",
    "PASS",
    "REVIEW",
    "Case",
    "Expectation",
    "JudgeAnswer",
    "expectation_verdict",
    "load_case",
    "rollup",
    "validate_citations",
]
