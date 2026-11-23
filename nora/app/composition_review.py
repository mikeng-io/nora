"""Compose review with the independently routed Dreamer runner."""

from collections.abc import Callable

from openai import AsyncOpenAI

from nora.agent import factory, tool_choice
from nora.agent.dreamer import TOOL_RESULT_CAPS, DreamerRuntime, DreamToolSource
from nora.app.cognition_agent_runner import DreamerAgentRunner
from nora.app.composition_helpers import _adaptor_budget, _reasoning_effort
from nora.app.composition_lesson_check import independent_judge_model
from nora.app.startup import StartupError
from nora.config import Config
from nora.instructions.dreamer import DreamerInstructions
from nora.instructions.frontmatter import InstructionError
from nora.obs.metrics import Metrics


def build_review_runner(
    cfg: Config, instructions: DreamerInstructions, tools: DreamToolSource,
    model_client_for: Callable[[str], AsyncOpenAI], metrics: Metrics, *, content_checks: bool,
) -> tuple[DreamerAgentRunner, str] | None:
    if not cfg.cognition.enabled:
        return None
    model = independent_judge_model(cfg)
    try:
        instructions.for_objective("review")
    except InstructionError as exc:
        raise StartupError(f"Review objective error: {exc}") from exc
    models = factory.ModelSource(
        client=model_client_for("lesson_check"),
        spec=factory.ModelSpec(
            model=model, max_tokens=cfg.dreamer_max_tokens(), temperature=0.0,
            reasoning_effort=_reasoning_effort(
                cfg.lessons.check_reasoning_effort or "low",
                source="[lessons] check_reasoning_effort",
            ) or "low",
            tool_choice=tool_choice.AFTER_SPEAKING,
        ),
    )
    runtime = DreamerRuntime(
        models=models, instructions=instructions, tools=tools, budgets={},
        default_budget=_adaptor_budget(cfg.dreamer.budget_for("cognition")),
        retry=factory.build_retry_policy(timeout_seconds=cfg.llm.timeout_seconds),
        metrics=metrics, model_name=model, content_checks=content_checks,
    )
    return DreamerAgentRunner(
        runtime=runtime, metrics=metrics, agent_name="review", result_caps=TOOL_RESULT_CAPS,
        bound_tool_arguments=True,
    ), model
