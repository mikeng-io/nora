"""`evals.backbone`'s graders — pure, no network/DB, cheap to pin.

`AbsenceGrader`: regression for the gap found while re-running
`design/evaluation/GOAL-2-backbone.md`'s Step 0 falsification against
`relevance_gate`: it read only `NORA_SAID`, which the `gate` entry point
never populates (it answers `admitted`, not `said`), so it graded every gate
case as silence regardless of the actual verdict. `NORA_ADMITTED` fixes it.

`ContextGrader`: S4 (`10-the-scenario-map.md` §3) — deterministic, on
`NORA_BLOCKS_EMPTY`.

`render_transcript_at`/`SubstringGrader`: S5 — deterministic, calling the
REAL `nora.domain.blocks.render_messages`/`nora.domain.prompt.age` directly
(pure functions, no `Subject`/database/network).
"""

from __future__ import annotations

from strands_evals import Case
from strands_evals.types.evaluation import EnvironmentState, EvaluationData

from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ADMITTED,
    NORA_BLOCKS_EMPTY,
    NORA_ERROR,
    NORA_SAID,
    PASS,
    AbsenceGrader,
    ContextGrader,
    SubstringGrader,
    render_transcript_at,
)

grader = AbsenceGrader()
context_grader = ContextGrader()
substring_grader = SubstringGrader()


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


def _context_data(
    *, must_render: tuple[str, ...], blocks_empty: str = "", error: str = "",
) -> EvaluationData:
    return EvaluationData(
        input="x",
        metadata={"must_render": must_render},
        actual_environment_state=[
            EnvironmentState(name=NORA_ERROR, state=error),
            EnvironmentState(name=NORA_BLOCKS_EMPTY, state=blocks_empty),
        ],
    )


def test_a_block_the_case_needed_but_came_back_empty_fails():
    out = context_grader.evaluate(
        _context_data(must_render=("standing",), blocks_empty="notes,standing"),
    )[0]
    assert out.label == FAIL
    assert "standing" in out.reason


def test_blocks_the_case_did_not_ask_about_do_not_affect_the_verdict():
    out = context_grader.evaluate(
        _context_data(must_render=("standing",), blocks_empty="notes,self_card"),
    )[0]
    assert out.label == PASS


def test_a_case_naming_no_block_is_ungradable():
    out = context_grader.evaluate(_context_data(must_render=()))[0]
    assert out.label == ERROR


def test_a_context_error_is_never_a_pass():
    out = context_grader.evaluate(
        _context_data(must_render=("standing",), blocks_empty="", error="boom"),
    )[0]
    assert out.label == ERROR


def _time_case(*, minutes_ago: int, expect_age: str) -> Case:
    return Case(
        name="s5",
        input="x",
        expected_output=f'age="{expect_age}"',
        metadata={
            "now": "2026-09-19T12:00:00+00:00",
            "rows": [{"body": "去邊食", "name": "Grant", "minutes_ago": minutes_ago}],
        },
    )


class _RenderedData:
    def __init__(self, output: str, expected: str):
        self.actual_output = output
        self.expected_output = expected


def _rendered(case: Case) -> _RenderedData:
    return _RenderedData(render_transcript_at(case)["output"], case.expected_output)


def test_a_row_181_minutes_old_renders_3h_ago():
    """The real `nora.domain.prompt.age` boundary: `seconds < 5400` (90 min)
    is minutes, `< 172800` (48h) is hours. 181 minutes = 10860s, hours."""
    out = substring_grader.evaluate(_rendered(_time_case(minutes_ago=181, expect_age="3h ago")))[0]
    assert out.label == PASS


def test_a_row_1_minute_old_renders_just_now():
    out = substring_grader.evaluate(_rendered(_time_case(minutes_ago=1, expect_age="just now")))[0]
    assert out.label == PASS


def test_the_wrong_expected_age_fails_rather_than_passing_silently():
    """Falsification: a substring check that always passes would be
    decoration. A deliberately wrong expectation must FAIL."""
    out = substring_grader.evaluate(_rendered(_time_case(minutes_ago=181, expect_age="5h ago")))[0]
    assert out.label == FAIL


def test_a_case_with_no_expected_output_is_ungradable():
    out = substring_grader.evaluate(_RenderedData("<m age=\"3h ago\">x</m>", ""))[0]
    assert out.label == ERROR
