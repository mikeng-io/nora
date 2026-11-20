"""A route override cannot make the wake review its own lessons."""

import pytest

from nora.app.composition_lesson_check import build_graph_lesson_check
from nora.app.startup import StartupError
from nora.config import ModelRoute
from nora.obs.metrics import Metrics


@pytest.mark.parametrize("enabled", [False, True])
def test_graph_wake_refuses_the_same_resolved_model_with_legacy_lessons_on_or_off(
    example_config, enabled,
):
    cfg = example_config.model_copy(update={
        "cognition": example_config.cognition.model_copy(update={"enabled": True}),
        "lessons": example_config.lessons.model_copy(update={
            "enabled": enabled, "check_model": "configured-check",
        }),
        "model_routes": {
            "lesson_check": ModelRoute(model="resolved-model"),
            "dreamer": ModelRoute(model="resolved-model"),
        },
    })

    def unexpected_client(_purpose):
        raise AssertionError("Same-model independence must fail before building a model client")

    with pytest.raises(StartupError, match="Graph lesson check requires an independent model"):
        build_graph_lesson_check(cfg, None, None, unexpected_client, Metrics())
