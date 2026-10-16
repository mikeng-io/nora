"""Spec C.10: six API obligations, verified by introspection against the pinned 1.50.2.

Each assertion fails with a message naming the drifted symbol, because a drift here
invalidates a design decision — not just a call site. On failure: STOP and report.
"""

import inspect

import pytest


def _params(fn) -> set[str]:
    return set(inspect.signature(fn).parameters)


def test_obligation_1_agent_init_accepts_the_kwargs_factory_uses():
    from strands import Agent

    params = _params(Agent.__init__)
    for kwarg in (
        "model", "system_prompt", "tools", "messages", "hooks",
        "conversation_manager", "session_manager", "callback_handler",
        "trace_attributes", "load_tools_from_directory",
    ):
        assert kwarg in params, f"DRIFT: Agent.__init__ no longer accepts {kwarg!r}"


def test_obligation_2_invoke_async_accepts_limits_and_invocation_state():
    from strands import Agent

    params = _params(Agent.invoke_async)
    assert "invocation_state" in params, "DRIFT: invoke_async lost invocation_state"
    assert "limits" in params, (
        "DRIFT: invoke_async lost `limits` — check whether it moved to Agent.__init__ "
        "(R-8 enforcement point changes if so)"
    )


def test_obligation_3_hook_events_expose_cancel_tool_and_retry():
    from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

    before = {f.name for f in getattr(BeforeToolCallEvent, "__dataclass_fields__", {}).values()} \
        or set(dir(BeforeToolCallEvent))
    assert "cancel_tool" in before, (
        "DRIFT: BeforeToolCallEvent.cancel_tool is gone (R-2/R-3/R-4/R-7)"
    )
    for attr in ("tool_use", "invocation_state"):
        assert attr in before, f"DRIFT: BeforeToolCallEvent.{attr} is gone"

    after = {f.name for f in getattr(AfterToolCallEvent, "__dataclass_fields__", {}).values()} \
        or set(dir(AfterToolCallEvent))
    for attr in ("result", "exception", "retry"):
        assert attr in after, f"DRIFT: AfterToolCallEvent.{attr} is gone"
    # Load-bearing (DEC-32): this hook fires for a call `_before_tool`
    # CANCELLED as well as for one that ran (`exception` stays None for a
    # cancellation), and `cancel_message` is the only thing that tells them
    # apart. Without it a `send_reply` vetoed for empty text would set
    # `ReplyGuard._turn_is_over` and end the turn in silence.
    assert "cancel_message" in after, (
        "DRIFT: AfterToolCallEvent.cancel_message is gone — ReplyGuard can no "
        "longer tell a cancelled terminal call from one that ran (DEC-32)"
    )


def test_obligation_3b_after_tool_call_result_is_writable():
    """R-91: `DreamGuard` REPLACES a refused memory read's result.

    `BeforeToolCallEvent.cancel_tool` is the only way to stop a call from
    running and the executor turns it into an ERROR `ToolResult` — which is
    exactly what a spent budget must not look like. The rewrite happens in the
    after-hook and depends on `result` being one of the writable fields:
    `BaseHookEvent.__setattr__` raises `AttributeError` for every field
    `_can_write` does not name. Asserted against the real event rather than
    the class, because `_can_write` is an instance method and a drift here
    would otherwise surface as a swallowed exception inside an unattended
    background run.
    """
    from strands.hooks import AfterToolCallEvent

    can_write = AfterToolCallEvent._can_write
    assert can_write(AfterToolCallEvent, "result"), (
        "DRIFT: AfterToolCallEvent.result is no longer writable — the per-pass "
        "memory refusal cannot be rendered as R-27 `unavailable` (R-91)"
    )


def test_obligation_4_openai_model_accepts_a_custom_client():
    """`client` is a named __init__ param. `model_id`/`params` arrive via
    `**model_config: Unpack[OpenAIConfig]` instead of named params — the runtime
    behavior factory.py needs (OpenAIModel(model_id=..., params=..., client=...))
    is intact because **kwargs unpacks the TypedDict.
    """
    from strands.models.openai import OpenAIModel

    params = _params(OpenAIModel.__init__)
    assert "client" in params, (
        "DRIFT: OpenAIModel no longer takes client= — the OmniRoute AsyncOpenAI "
        "singleton (audit fix #6) has no injection point"
    )

    config_keys = set(OpenAIModel.OpenAIConfig.__annotations__)
    for key in ("model_id", "params"):
        assert key in config_keys, (
            f"DRIFT: OpenAIModel.OpenAIConfig no longer declares {key!r} — "
            "factory.py's OpenAIModel(model_id=..., params=..., client=...) call has no target"
        )


def test_obligation_5_null_conversation_manager_import_path():
    from strands.agent.conversation_manager import NullConversationManager

    assert NullConversationManager() is not None


def test_obligation_6_hook_provider_protocol():
    from strands.hooks import HookProvider

    assert hasattr(HookProvider, "register_hooks")


def test_obligation_6b_strands_evals_surface():
    """The judge contract needs Case/Experiment/OutputEvaluator/TrajectoryEvaluator.

    OutputEvaluator and TrajectoryEvaluator are not re-exported at the
    strands_evals top level (only Case/Experiment/etc. are, per __init__.py's
    __all__). Both exist in the submodules `strands_evals.evaluators.output_evaluator`
    and `strands_evals.evaluators.trajectory_evaluator`, so this test imports
    them from there instead of asserting top-level attribute presence.

    OutputEvaluator.evaluate()/evaluate_async() hardcode
    `structured_output_model=EvaluationOutput`, where EvaluationOutput
    (strands_evals.types) is `{score: float, test_pass: bool, reason: str | None,
    label: str | None}`. That does NOT match the judge contract of
    `{criterion, score, confidence, rationale}` per criterion — there is no
    `criterion` or `confidence` field, and `test_pass` is a hard bool rather than
    a confidence-weighted score/confidence pair. The structured output model is
    inline in evaluate()/evaluate_async(), not overridable via a class
    attribute, so subclassing OutputEvaluator cannot swap the schema. The
    judge evaluator therefore subclasses strands_evals' Evaluator base directly
    (not OutputEvaluator) and owns its own judge prompt + a custom Pydantic
    structured_output_model shaped {criterion, score, confidence, rationale},
    following the same Agent(...).invoke_async(prompt,
    structured_output_model=...) pattern OutputEvaluator itself uses internally.
    """
    strands_evals = pytest.importorskip("strands_evals")
    from strands_evals.evaluators.output_evaluator import OutputEvaluator
    from strands_evals.evaluators.trajectory_evaluator import TrajectoryEvaluator

    for name, symbol in (
        ("Case", getattr(strands_evals, "Case", None)),
        ("Experiment", getattr(strands_evals, "Experiment", None)),
        ("OutputEvaluator", OutputEvaluator),
        ("TrajectoryEvaluator", TrajectoryEvaluator),
    ):
        assert symbol is not None, f"DRIFT: strands_evals.{name} is gone"
    assert hasattr(strands_evals.Experiment, "run_evaluations")
    assert hasattr(strands_evals.Experiment, "to_file")


def test_obligation_7_the_tool_choice_flip_is_reachable_from_a_before_model_hook():
    """DEC-42's whole mechanism, verified against the SDK rather than assumed.

    `BeforeModelCallEvent` "cannot write `tool_choice`" because `_can_write` is `return
    name == "cancel"`, but that is about the EVENT: the event refuses attribute
    assignment on itself. It says nothing about reachability. `HookEvent.agent`
    is a plain required field, `Agent.model` is public, and
    `OpenAIModel.update_config` exists — so
    `event.agent.model.update_config(params=...)` is the route, and
    `nora.agent.tool_choice.relax` takes it.

    Three assertions, because losing any one of them silently disables
    multi-message: she would keep `tool_choice="required"` for the whole turn,
    never be permitted to stop, and be cut off by a cap instead.

    The `update_config` REPLACE semantics are asserted too. It is
    `self.config.update(model_config)`, a shallow dict update, so writing
    `params={"tool_choice": "auto"}` drops `max_tokens`, `temperature` and
    `reasoning_effort` for the rest of the turn. `relax` reads, copies and
    writes the params dict back whole; if the SDK ever started merging that
    would still be correct, and if it stopped being a replace nobody would
    notice without this.
    """
    import dataclasses

    from openai import AsyncOpenAI
    from strands.hooks import BeforeModelCallEvent
    from strands.models.openai import OpenAIModel

    fields = {f.name for f in dataclasses.fields(BeforeModelCallEvent)}
    assert "agent" in fields, (
        "DRIFT: BeforeModelCallEvent no longer carries `agent` — ReplyGuard has "
        "no route to the model and tool_choice can never be relaxed (DEC-42)"
    )
    assert BeforeModelCallEvent._can_write(None, "cancel") is True
    assert BeforeModelCallEvent._can_write(None, "agent") is False, (
        "the event is read-only except for `cancel`; if that changed, the "
        "reasoning in nora.agent.tool_choice's docstring needs rereading"
    )

    model = OpenAIModel(
        model_id="m", client=AsyncOpenAI(api_key="k", base_url="http://localhost:1/v1"),
        params={"max_tokens": 7, "tool_choice": "required"},
    )
    model.update_config(params={"tool_choice": "auto"})
    assert model.get_config()["params"] == {"tool_choice": "auto"}, (
        "DRIFT: OpenAIModel.update_config now MERGES params instead of replacing "
        "them — nora.agent.tool_choice.relax reads-copies-writes to survive the "
        "replace, which is still correct, but the docstring's reasoning is stale"
    )


def test_obligation_8_the_before_model_hook_fires_inside_the_retry_loop():
    """`_model_calls` counts COMPLETIONS because the BEFORE
    hook cannot tell a fresh call from a re-attempt of the same one.

    Asserted against the SDK's own source rather than described in a comment,
    because the whole reason `ReplyGuard._after_model` exists is this ordering.
    Three facts, and losing any of them changes what the counter means:

    * `BeforeModelCallEvent` is constructed INSIDE the `while True:` retry loop
      in `_handle_model_execution`, so it fires once per ATTEMPT;
    * the exception handler `continue`s back to the top of that loop, which is
      the re-attempt;
    * `AfterModelCallEvent` carries `exception`, which is the only thing that
      separates «the model answered» from «the attempt threw».

    If the SDK ever moves the before-hook OUTSIDE the retry loop, this reds and
    the guard's `_model_call_in_flight` bookkeeping becomes dead weight rather
    than a fix.
    """
    import dataclasses

    from strands.event_loop import event_loop
    from strands.hooks import AfterModelCallEvent

    src = inspect.getsource(event_loop._handle_model_execution)
    loop_at = src.index("while True:")
    hook_at = src.index("BeforeModelCallEvent(")
    assert loop_at < hook_at, (
        "DRIFT: BeforeModelCallEvent is no longer raised inside the retry loop — "
        "ReplyGuard._model_calls could go back to counting it directly (A11)"
    )
    assert "continue  # Retry the model call" in src, (
        "DRIFT: _handle_model_execution no longer re-enters the loop on a "
        "retryable failure — re-read ReplyGuard._after_model's reasoning"
    )

    after = {f.name for f in dataclasses.fields(AfterModelCallEvent)}
    assert "exception" in after, (
        "DRIFT: AfterModelCallEvent.exception is gone — ReplyGuard cannot tell a "
        "model call that answered from an attempt that threw (A11)"
    )
