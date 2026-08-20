"""Equality grading for the fact-adjudication corpus. No judge, no cache, no
REVIEW lane — a structured verdict does not need one, and spec §9.0 measured
that an LLM judge cannot grade this class of claim because it commits the
error under test.

`nora.domain.facts.FactVerdict` is NOT imported here: the import-linter
contract "evals may only reach nora.app and nora.testing" (pyproject.toml)
and its AST backstop (`tests/test_boundaries.py::test_evals_only_imports_
nora_app_and_nora_testing`) both forbid `evals/` from reaching `nora.domain`
at all — the domain package sits below the layer evals is allowed to see.
Rather than widen that contract for this one type, the grader takes anything
that is *structurally* a verdict (`status`, `removes_nothing`); the real
`FactVerdict` satisfies that shape without this module ever importing it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _Verdict(Protocol):
    """The two `FactVerdict` attributes this grader needs — expressed
    structurally so this module never imports `nora.domain.facts`.
    `@runtime_checkable` lets `isinstance(verdict, _Verdict)` be asserted
    (name-only, no signature check — see `evals/sink.py::EvalSink`'s same
    pattern) against the real `FactVerdict`, which is the only mechanical
    conformance check this shape gets: `evals/` sits outside the scoped mypy
    gate."""

    status: str

    @property
    def removes_nothing(self) -> bool: ...


def grade_adjudication(case: dict, verdict: _Verdict) -> bool:
    if verdict.status != case["expect_status"]:
        return False
    survives = verdict.removes_nothing
    return survives is bool(case["expect_fact_still_standing"])
