"""Phase 2 forcing mechanism: `tool_choice.force`/`require`, and the
`ReplyGuard._before_model` sequence that pins the routed tool on the FIRST model
call and RELEASES it on the second (a pin left in place would make her call the
same tool every pass and never `send_reply`)."""

from types import SimpleNamespace
from typing import Any

from nora.agent import tool_choice
from nora.agent.guards import ReplyGuard
from nora.obs.metrics import Metrics

FIRST_TOOL = {"type": "function", "function": {"name": "place_info"}}


class _Model:
    """Real `OpenAIModel.update_config`/`get_config` semantics (shallow update)."""

    def __init__(self) -> None:
        self.config: dict[str, Any] = {
            "model_id": "stub",
            "params": {"max_tokens": 2000, "temperature": 0.7, "tool_choice": "required"},
        }

    def update_config(self, **cfg: Any) -> None:
        self.config.update(cfg)

    def get_config(self) -> Any:
        return self.config


def _tc(model: _Model):
    return model.config["params"].get("tool_choice")


def test_force_sets_named_choice_and_preserves_other_params():
    m = _Model()
    assert tool_choice.force(m, "place_info") is True
    assert _tc(m) == FIRST_TOOL
    # the other params must survive the whole-dict write-back
    assert m.config["params"]["max_tokens"] == 2000
    assert m.config["params"]["temperature"] == 0.7


def test_require_resets_to_required():
    m = _Model()
    tool_choice.force(m, "place_info")
    assert tool_choice.require(m) is True
    assert _tc(m) == "required"


def test_force_and_require_never_raise_on_a_configless_model():
    assert tool_choice.force(None, "place_info") is False
    assert tool_choice.require(None) is False


def _guard(forced):
    return ReplyGuard(
        conversation_id="c1", budgets={"place_info": 4, "send_reply": 3},
        terminal={"send_reply"}, metrics=Metrics(), forced_first_tool=forced,
    )


def _event(model):
    # _before_model reads event.agent.model and may set event.cancel.
    return SimpleNamespace(agent=SimpleNamespace(model=model), cancel=None)


def test_guard_forces_on_first_call_then_releases_on_second():
    m = _Model()
    guard = _guard("place_info")

    guard._before_model(_event(m))          # call 1 → force place_info
    assert _tc(m) == FIRST_TOOL

    guard._before_model(_event(m))          # call 2 → release to required
    assert _tc(m) == "required"


def test_guard_without_a_forced_tool_leaves_the_opening_required():
    m = _Model()
    guard = _guard(None)
    guard._before_model(_event(m))
    assert _tc(m) == "required"             # untouched — abstained
