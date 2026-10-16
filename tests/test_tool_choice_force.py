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

    def __init__(self, tool_choice: str = "required") -> None:
        self.config: dict[str, Any] = {
            "model_id": "stub",
            "params": {"max_tokens": 2000, "temperature": 0.7,
                       "tool_choice": tool_choice},
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


def test_require_releases_to_the_models_initial_value():
    """A participation turn composes its model with `tool_choice="auto"`:
    releasing a `force` pin to the hardcoded `FIRST_CALL` would flip the rest of
    that turn to `required` and destroy the right-to-silence the occasion exists
    for. The release target is the value the model was BUILT with, recorded on
    its first mutation."""
    m = _Model(tool_choice="auto")
    tool_choice.force(m, "place_info")
    assert _tc(m) == FIRST_TOOL
    assert tool_choice.require(m) is True
    assert _tc(m) == "auto"


def test_require_without_a_prior_pin_is_a_no_op():
    """Nothing was ever forced: there is no pin to release, and an untouched
    model's initial value IS its current one — so nothing may change."""
    m = _Model(tool_choice="auto")
    assert tool_choice.require(m) is False
    assert _tc(m) == "auto"


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


def _answered():
    """`AfterModelCallEvent` for a call the model actually answered."""
    return SimpleNamespace(exception=None, retry=False)


def _raised(exc=None):
    """`AfterModelCallEvent` for an attempt that threw — a throttle, say.

    Strands' own retry strategy (`strands/event_loop/_retry.py`) only sets
    `retry` after `is_retryable(event.exception)`, so an exception here is
    exactly the shape that sends the loop back round to `BeforeModelCallEvent`
    without a model answer in hand.
    """
    return SimpleNamespace(exception=exc or RuntimeError("throttled"), retry=True)


def test_guard_forces_on_first_call_then_releases_on_second():
    m = _Model()
    guard = _guard("place_info")

    guard._before_model(_event(m))          # call 1 → force place_info
    assert _tc(m) == FIRST_TOOL
    guard._after_model(_answered())         # ...and the model answered it

    guard._before_model(_event(m))          # call 2 → release to required
    assert _tc(m) == "required"


def test_a_retried_first_call_does_not_release_the_pin():
    """`BeforeModelCallEvent` fires INSIDE the retry loop —
    `strands/event_loop/event_loop.py::_handle_model_execution` invokes it at
    the top of a `while True:` that the exception path `continue`s back to — so
    a throttled first attempt would bump `_model_calls` to 2 and release the
    pin before any successful call had ever used it.

    Revert `_before_model` to `self._model_calls += 1` / `== 1` / `== 2` and
    this goes red: the retry re-enters as «call 2» and hands the model
    `required` on the only call the router meant to pin.
    """
    m = _Model()
    guard = _guard("place_info")

    guard._before_model(_event(m))          # attempt 1 of call 1 → force
    assert _tc(m) == FIRST_TOOL
    guard._after_model(_raised())           # throttled: no answer came back

    guard._before_model(_event(m))          # attempt 2 of THE SAME call
    assert _tc(m) == FIRST_TOOL, "a retry released the pin the first call never used"
    assert guard._model_calls == 0, "an attempt that raised is not a model call"

    guard._after_model(_answered())         # this attempt answered
    assert guard._model_calls == 1

    guard._before_model(_event(m))          # now genuinely call 2 → release
    assert _tc(m) == "required"


def test_a_model_call_this_guard_cancelled_is_not_counted():
    """Strands synthesises a stop response for a cancelled call and still fires
    `AfterModelCallEvent` with `exception=None`, which is indistinguishable
    from a real answer at that event alone — so the count is gated on the
    guard's own record of having admitted the call."""
    m = _Model()
    guard = _guard("place_info")
    guard._turn_is_over = True

    event = _event(m)
    guard._before_model(event)
    assert event.cancel                      # no model call happened
    guard._after_model(_answered())
    assert guard._model_calls == 0


def test_guard_without_a_forced_tool_leaves_the_opening_required():
    m = _Model()
    guard = _guard(None)
    guard._before_model(_event(m))
    assert _tc(m) == "required"             # untouched — abstained
