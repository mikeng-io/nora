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


def test_spend_counts_model_calls_once_and_the_turn_apart_from_background():
    """`_model_calls` counted every span carrying usage, and Strands puts the
    agent's accumulated usage on `invoke_agent` too — so every call was
    counted twice (measured: 29 spans for one case, 16 of them real calls).
    And a per-turn number must exclude background passes the drive spawns."""
    from types import SimpleNamespace

    from evals.backbone import NORA_CALLS, NORA_INPUT_TOKENS, _model_calls

    def span(sid, parent, op, agent=None, tokens=0):
        attrs = {"gen_ai.operation.name": op, "gen_ai.usage.output_tokens": 1,
                 "gen_ai.usage.input_tokens": tokens}
        if agent:
            attrs["gen_ai.agent.name"] = agent
        return SimpleNamespace(
            context=SimpleNamespace(span_id=sid),
            parent=SimpleNamespace(span_id=parent) if parent else None,
            attributes=attrs,
        )

    spans = [
        span(1, None, "invoke_agent", agent="nora", tokens=300),
        span(2, 1, "chat", tokens=100),
        span(3, 1, "chat", tokens=200),
        span(4, None, "invoke_agent", agent="standing.event_fold", tokens=50),
        span(5, 4, "chat", tokens=50),
    ]

    class _Exporter:
        def get_finished_spans(self):
            return spans

    everything = _model_calls(_Exporter())
    assert everything[NORA_CALLS] == 3
    assert everything[NORA_INPUT_TOKENS] == 350
    turn = _model_calls(_Exporter(), agent="nora")
    assert turn[NORA_CALLS] == 2
    assert turn[NORA_INPUT_TOKENS] == 300
