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


# --- what the call carried (2026-09-24) ---------------------------------------
# «draw yourself» answered with an empty room is ONE `generate_image` call, the
# same as the right answer. Routing cannot see the difference; the argument can.

_SELF = "535e4da5" + "0" * 56


def _carried(inputs, *, clock=None, **md):
    from evals.backbone import NORA_SHOWN_NOW, NORA_TOOL_INPUTS

    called = tuple(name for name, _ in inputs) if inputs is not None else ("generate_image",)
    return EvaluationData(
        input="x",
        metadata={"tools": ["generate_image"], **md},
        actual_environment_state=[
            EnvironmentState(name=NORA_ERROR, state=None),
            EnvironmentState(name=NORA_TOOLS, state=called),
            EnvironmentState(name=NORA_TOOL_INPUTS, state=inputs),
            EnvironmentState(name=NORA_SHOWN_NOW, state=clock),
        ],
    )


def test_a_required_argument_that_was_passed_is_a_pass():
    data = _carried((("generate_image", f'{{"prompt": "me", "reference": "{_SELF}"}}'),),
                    args_include={"generate_image": [_SELF]})
    assert _label(data) == PASS


def test_the_right_tool_without_the_required_argument_is_a_fail():
    """The live failure: the right tool, and no reference."""
    data = _carried((("generate_image", '{"prompt": "an empty room at night"}'),),
                    args_include={"generate_image": [_SELF]})
    assert _label(data) == FAIL


def test_a_forbidden_argument_is_a_fail():
    data = _carried((("generate_image", f'{{"prompt": "a cat", "reference": "{_SELF}"}}'),),
                    args_exclude={"generate_image": ["535e4da5"]})
    assert _label(data) == FAIL
    clean = _carried((("generate_image", '{"prompt": "a cat"}'),),
                     args_exclude={"generate_image": ["535e4da5"]})
    assert _label(clean) == PASS


def test_an_input_that_was_not_captured_is_an_error_not_a_pass_or_fail():
    assert _label(_carried(None, args_exclude={"generate_image": ["x"]})) == ERROR
    assert _label(_carried((("generate_image", ""),),
                           args_exclude={"generate_image": ["x"]})) == ERROR


_CLOCK = ("Right now it is Wednesday 23 September 2026, 23:07, "
          "Hong Kong time (HKT, Asia/Hong_Kong).")


def test_this_saturday_is_graded_against_the_clock_she_was_shown():
    ok = _carried((("generate_image", '{"prompt": "Dinner, Saturday 26 September"}'),),
                  clock=_CLOCK, args_date={"generate_image": "saturday"})
    assert _label(ok) == PASS
    relative = _carried((("generate_image", '{"prompt": "Dinner this Saturday"}'),),
                        clock=_CLOCK, args_date={"generate_image": "saturday"})
    assert _label(relative) == FAIL
    wrong_week = _carried((("generate_image", '{"prompt": "Dinner, 3 October"}'),),
                          clock=_CLOCK, args_date={"generate_image": "saturday"})
    assert _label(wrong_week) == FAIL


def test_a_date_case_with_no_clock_line_is_an_error():
    data = _carried((("generate_image", '{"prompt": "26 September"}'),),
                    args_date={"generate_image": "saturday"})
    assert _label(data) == ERROR


def test_the_usual_ways_of_writing_a_date_all_count():
    from datetime import datetime

    from evals.backbone import states_the_date, this_weekday

    day = this_weekday(datetime(2026, 9, 23), "saturday")
    assert day == datetime(2026, 9, 26)
    assert this_weekday(datetime(2026, 9, 26), "Saturday") == day
    for text in ("26 September", "Sept 26", "September 26th", "2026-09-26",
                 "9月26日", "26/9", "26th of Sep"):
        assert states_the_date(text, day), text
    for text in ("126 September", "September 2", "16/9", "this Saturday"):
        assert not states_the_date(text, day), text


def test_tool_inputs_and_the_clock_line_are_read_off_the_spans():
    """The input is the `gen_ai.tool.message` event's `content` (legacy) or
    `gen_ai.input.messages` (latest); the result event is never read as input.
    The clock line comes off the first chat span, as the model received it."""
    from types import SimpleNamespace

    from evals.backbone import _shown_now, _tool_inputs

    def event(name, **attrs):
        return SimpleNamespace(name=name, attributes=attrs)

    spans = [
        SimpleNamespace(start_time=2, attributes={"gen_ai.tool.name": "generate_image"},
                        events=[event("gen_ai.tool.message", content='{"prompt": "p"}'),
                                event("gen_ai.choice", message='{"note": "535e4da5"}')]),
        SimpleNamespace(start_time=3, attributes={"gen_ai.tool.name": "send_reply",
                                                  "gen_ai.input.messages": '[{"text": "hi"}]'},
                        events=[]),
        SimpleNamespace(start_time=1, attributes={"gen_ai.operation.name": "chat"},
                        events=[event("gen_ai.user.message",
                                      content='[{"text": "' + _CLOCK + '\\n<presence>"}]')]),
    ]

    class _Exporter:
        def get_finished_spans(self):
            return spans

    assert _tool_inputs(_Exporter()) == (
        ("generate_image", '{"prompt": "p"}'),
        ("send_reply", '[{"text": "hi"}]'),
    )
    assert _shown_now(_Exporter()) == _CLOCK
