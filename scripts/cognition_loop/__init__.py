"""The Cognition loop eval (`docs/engineering/evaluation-cognition-loop.md`).

The shared report, judging and state-diff pieces of the autonomous journey: a
sandbox restored at an instant and advanced by copying only the world's Record
rows. Each wake is diffed across every owned store and judged fail-closed by two
independent judges.
"""

from scripts.cognition_loop.cases import Case, Expectation, load_case
from scripts.cognition_loop.verdicts import (
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
