"""Build the independent doctrine check used by graph reflection."""

from collections.abc import Callable

from openai import AsyncOpenAI

from nora.app.composition_helpers import _reasoning_effort
from nora.app.raw_clients import _RelevanceGateModelClient
from nora.app.startup import StartupError
from nora.config import Config
from nora.doctrine.loader import DoctrineStore
from nora.instructions.dreamer import DreamerInstructions
from nora.language.doctrine_check import CHECK_OBJECTIVE, DoctrineCheck
from nora.obs.metrics import Metrics


def independent_judge_model(cfg: Config) -> str:
    model = cfg.endpoint("lesson_check", cfg.lessons.check_model)[2]
    if model == cfg.endpoint("dreamer", cfg.dreamer_model())[2]:
        raise StartupError("Graph lesson check requires an independent model from the wake")
    return model


def build_graph_lesson_check(
    cfg: Config, doctrine: DoctrineStore, instructions: DreamerInstructions,
    model_client_for: Callable[[str], AsyncOpenAI], metrics: Metrics,
) -> DoctrineCheck | None:
    model = independent_judge_model(cfg)
    if not instructions.objective_body(CHECK_OBJECTIVE).strip():
        raise StartupError(f"the {CHECK_OBJECTIVE} objective is empty")
    return DoctrineCheck(
        client=_RelevanceGateModelClient(
            client=model_client_for("lesson_check"), model=model,
            max_tokens=cfg.lessons.check_max_tokens, temperature=0.0,
            timeout_seconds=cfg.lessons.check_timeout_seconds + 1.0,
            reasoning_effort=_reasoning_effort(
                cfg.lessons.check_reasoning_effort or "low",
                source="[lessons] check_reasoning_effort",
            ),
        ),
        law=doctrine.law_rules,
        instruction=lambda: instructions.objective_body(CHECK_OBJECTIVE),
        timeout_seconds=cfg.lessons.check_timeout_seconds,
        metrics=metrics,
    )
