"""`evals.backbone.AbsenceGrader` — pure, no network/DB, cheap to pin.

Regression for the gap found while re-running `design/evaluation/
GOAL-2-backbone.md`'s Step 0 falsification against `relevance_gate`:
`AbsenceGrader` read only `NORA_SAID`, which the `gate` entry point never
populates (it answers `admitted`, not `said`), so it graded every gate case
as silence regardless of the actual verdict. `NORA_ADMITTED` fixes it.
"""

from __future__ import annotations

from strands_evals.types.evaluation import EnvironmentState, EvaluationData

from evals.backbone import ERROR, FAIL, NORA_ADMITTED, NORA_ERROR, NORA_SAID, PASS, AbsenceGrader

grader = AbsenceGrader()


def _data(
    *, expect: str, admitted: bool | None = None, said: int = 0, error: str = "",
) -> EvaluationData:
    return EvaluationData(
        input="x",
        metadata={"expect": expect},
        actual_environment_state=[
            EnvironmentState(name=NORA_ERROR, state=error),
            EnvironmentState(name=NORA_SAID, state=said),
            EnvironmentState(name=NORA_ADMITTED, state=admitted),
        ],
    )


def test_a_gate_no_go_case_that_was_admitted_fails():
    """THE BUG: before `NORA_ADMITTED`, this scored PASS — `said` is always
    `0` for a gate case, so any `no_go`-expecting case looked silent no
    matter what the gate actually decided."""
    out = grader.evaluate(_data(expect="no_go", admitted=True))[0]
    assert out.label == FAIL


def test_a_gate_no_go_case_that_was_refused_passes():
    out = grader.evaluate(_data(expect="no_go", admitted=False))[0]
    assert out.label == PASS


def test_a_gate_go_case_that_was_admitted_passes():
    out = grader.evaluate(_data(expect="go", admitted=True))[0]
    assert out.label == PASS


def test_a_gate_go_case_that_was_refused_fails():
    out = grader.evaluate(_data(expect="go", admitted=False))[0]
    assert out.label == FAIL


def test_a_participation_style_case_is_unaffected_by_the_gate_signal():
    """`admitted=None` (never ran a gate) must fall back to `said`, exactly
    as before this fix — `participation`/`tag`/`dreamer` cases carry no
    `NORA_ADMITTED` signal at all."""
    assert grader.evaluate(_data(expect="silence", admitted=None, said=0))[0].label == PASS
    assert grader.evaluate(_data(expect="silence", admitted=None, said=1))[0].label == FAIL
    assert grader.evaluate(_data(expect="speak", admitted=None, said=1))[0].label == PASS
    assert grader.evaluate(_data(expect="speak", admitted=None, said=0))[0].label == FAIL


def test_an_error_is_never_a_pass_regardless_of_the_admitted_signal():
    out = grader.evaluate(_data(expect="no_go", admitted=False, error="boom"))[0]
    assert out.label == ERROR
