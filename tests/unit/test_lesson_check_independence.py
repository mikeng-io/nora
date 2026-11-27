"""A route override cannot make the wake review its own lessons."""

from pathlib import Path

import pytest

from nora.app.composition_lesson_check import build_graph_lesson_check
from nora.app.startup import StartupError
from nora.config import ModelRoute
from nora.doctrine.loader import DoctrineStore
from nora.instructions.dreamer import DreamerInstructions
from nora.obs.metrics import Metrics


@pytest.mark.parametrize("enabled", [False, True])
def test_graph_wake_refuses_the_same_resolved_model_with_legacy_lessons_on_or_off(
    example_config, enabled,
):
    cfg = example_config.model_copy(update={
        "dreamer": example_config.dreamer.model_copy(update={"enabled": True}),
        "lessons": example_config.lessons.model_copy(update={
            "enabled": enabled, "check_model": "configured-check",
        }),
        "model_routes": {
            "lesson_check": ModelRoute(model="resolved-model"),
            "dreamer": ModelRoute(model="resolved-model"),
        },
    })

    metrics = Metrics()
    source = Path(__file__).resolve().parents[2] / "nora"
    doctrine = DoctrineStore(source / "doctrine", metrics=metrics)
    instructions = DreamerInstructions(source / "dreamer", metrics=metrics)

    def client(purpose):
        assert purpose == "lesson_check"
        return object()

    with pytest.raises(StartupError, match="Graph lesson check requires an independent model"):
        build_graph_lesson_check(cfg, doctrine, instructions, client, metrics)

    distinct = cfg.model_copy(update={"model_routes": {
        **cfg.model_routes, "dreamer": ModelRoute(model="other-model"),
    }})
    checker = build_graph_lesson_check(distinct, doctrine, instructions, client, metrics)
    assert checker is not None and checker.model == "resolved-model"
