"""`ToolRouteGrader` — S2 routing, graded on what EXECUTED.

Pure: fabricated `EvaluationData`, no subject. The grader is the instrument
the phase-C switch is decided on, so its three verdicts are pinned here
before any number from it is trusted.
"""

from __future__ import annotations

from strands_evals.types.evaluation import EnvironmentState, EvaluationData

from evals.backbone import ERROR, FAIL, NORA_ERROR, NORA_TOOLS, PASS, ToolRouteGrader, _tools_called


def _data(called, *, tools=("where_is",), not_tools=("where_been",), error=None):
    return EvaluationData(
        input="x",
        metadata={"tools": list(tools), "not_tools": list(not_tools)},
        actual_environment_state=[
            EnvironmentState(name=NORA_ERROR, state=error),
            EnvironmentState(name=NORA_TOOLS, state=called),
        ],
    )


def _label(data) -> str:
    return ToolRouteGrader().evaluate(data)[0].label


def test_the_expected_tool_is_a_pass():
    assert _label(_data(("where_is", "send_reply"))) == PASS


def test_the_confusable_sibling_is_a_fail_even_beside_the_right_one():
    assert _label(_data(("where_been", "where_is", "send_reply"))) == FAIL


def test_calling_nothing_useful_is_a_fail():
    assert _label(_data(("send_reply",))) == FAIL


def test_the_loader_is_never_the_answer():
    """A deferred turn that loaded `where_is` and then never called it did
    not route correctly — `load_tools` naming it is not calling it."""
    assert _label(_data(("load_tools", "send_reply"), tools=("where_is", "load_tools"))) == FAIL


def test_a_turn_that_did_not_run_is_an_error_not_a_fail():
    assert _label(_data(None, error="boom")) == ERROR
    assert _label(_data(None)) == ERROR


def test_tools_are_read_in_execution_order_from_tool_spans():
    class _Span:
        def __init__(self, name, start, attrs=True):
            self.attributes = {"gen_ai.tool.name": name} if attrs else {"x": 1}
            self.start_time = start

    class _Exporter:
        def get_finished_spans(self):
            return [_Span("send_reply", 3), _Span("model", 1, attrs=False), _Span("where_is", 2)]

    assert _tools_called(_Exporter()) == ("where_is", "send_reply")
