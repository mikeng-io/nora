"""DEC-33: `nora.testing.FakeAgentRunner` must dispatch the hooks the SDK does.

The double replays a scripted tool sequence against the REAL tools and the
REAL guard, which is the only reason a scripted scenario proves anything about
`ReplyGuard`. It must go through the guard's hook callbacks rather than call
`evaluate`/`record` by hand: a hand-written version skips the after-tool
notification on a veto, so the budget a vetoed call costs would be decided by
the FAKE in tests and by `ReplyGuard._after_tool` in production. No scripted
scenario could then reproduce the R-4 → R-2 lockout, and a regression test
written against the double would be decorative (CLAUDE.md, "make the fake
behave like the real server").

Both halves below assert the SAME observable — the sequence of after-tool
notifications a `HookProvider` receives, and specifically whether a CANCELLED
call produces one — first against the real Strands loop, then against the
double. They are written as a pair on purpose: either alone is a claim about
one implementation, and the property that matters is that the two agree.

What the guard then DOES with that notification is `tests/test_guards_hooks.py`
and `tests/integration/test_tagging.py`'s business, not this file's.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

from strands.models.model import Model

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.app.agent_runner import LiveAgentRunner
from nora.doctrine.loader import Doctrine
from nora.domain.models import NormalizedMessage, TagContext
from nora.obs.metrics import Metrics
from nora.testing import FakeAgentRunner, ScriptedTurn
from tests.fakes import FixedModelSource

RETRY = factory.build_retry_policy(timeout_seconds=30.0)

# A budget of 1 with a model that asks twice: the first call runs, the second
# is vetoed by R-7. Nothing else in either half can end a turn, so the veto is
# the only thing being observed.
BUDGETS = {"now": 1}


class _Recording(ReplyGuard):
    """A real guard that also writes down every after-tool event it is handed.

    Subclassed rather than stubbed because the point is the DISPATCH, not the
    decision: `seen` records what arrived, and `super()` still applies the real
    rule to it, so this cannot pass by being a guard that does nothing.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen: list[tuple[str, str | None]] = []

    def _after_tool(self, event: Any) -> None:
        self.seen.append((
            (getattr(event, "tool_use", None) or {}).get("name", ""),
            getattr(event, "cancel_message", None),
        ))
        super()._after_tool(event)


def _guard() -> _Recording:
    return _Recording(conversation_id="g1", budgets=BUDGETS,
                      terminal=frozenset(), metrics=Metrics())


async def _now() -> str:
    """The current time.

    Returns: a fixed string.
    """
    return "Thursday"


def _tool():
    return adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          budget=1))


# ---- the SDK half ----------------------------------------------------------


class _AsksTwice(Model):
    """`tool_choice="required"` from the loop's side: it only ever asks for `now`."""

    def __init__(self) -> None:
        self.calls = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None,
                                **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {
            "name": "now", "toolUseId": f"t{self.calls}"}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 1}}}


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>",
        doctrine_text="be helpful", sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


def _ctx() -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )
    return TagContext(tag_id="tag-1", message=msg, tagger=None, transcript=(),
                      peer_card=(), started_at=0.0)


async def test_the_real_sdk_fires_after_tool_for_a_call_the_guard_cancelled():
    """The claim the double has to match, taken from the SDK itself.

    Strands' executor turns `cancel_tool` into an ERROR `ToolResult` and fires
    `AfterToolCallEvent` anyway, with `exception` still None — so a hook that
    keys off `exception` alone sees a cancelled call and a successful one as
    the same thing, and `cancel_message` is the only field that separates them.
    """
    guard = _guard()
    await LiveAgentRunner(models=FixedModelSource(_AsksTwice()), retry=RETRY).invoke(
        doctrine=_doctrine(), tools=[_tool()], pre_fill=[], guard=guard,
        tag_message="<tag_message/>", ctx=_ctx(), trace_attributes={},
        limits={"turns": 2, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    ran, cancelled = guard.seen[0], guard.seen[1]
    assert ran == ("now", None)
    assert cancelled[0] == "now"
    assert isinstance(cancelled[1], str) and "already called now" in cancelled[1]


# ---- the double half -------------------------------------------------------


async def test_the_scripted_double_fires_the_same_after_tool_for_a_cancelled_call():
    """THE PROBE. Delete the `_AfterToolCall` dispatch from `FakeAgentRunner`'s
    veto branch and this goes red while every scripted scenario stays green —
    the divergence this file exists to catch."""
    guard = _guard()
    await FakeAgentRunner([ScriptedTurn(tool_calls=[("now", {}), ("now", {})])]).invoke(
        tools=[_tool()], guard=guard, limits=None,
    )
    ran, cancelled = guard.seen[0], guard.seen[1]
    assert ran == ("now", None)
    assert cancelled[0] == "now"
    assert isinstance(cancelled[1], str) and "already called now" in cancelled[1]


async def test_the_double_never_runs_the_tool_it_cancelled():
    """The control for the probe above: an after-tool notification for a
    cancelled call must not mean the call happened. A double that simply ran
    every scripted call and fired both hooks would pass the probe and prove
    nothing."""
    calls: list[str] = []

    async def _counting() -> str:
        """The current time.

        Returns: a fixed string.
        """
        calls.append("now")
        return "Thursday"

    tool = adapt(ToolSpec(name="now", description="", schema={}, call=_counting,
                          budget=1))
    await FakeAgentRunner([ScriptedTurn(tool_calls=[("now", {}), ("now", {})])]).invoke(
        tools=[tool], guard=_guard(), limits=None,
    )
    assert calls == ["now"]


# ---- the before-model half: the agent, the model, and the flip -------------
#
# `ReplyGuard._before_model` has a second job (DEC-42): it relaxes
# `tool_choice` through `event.agent.model`. A double that dispatched a
# before-model event with no `agent` would silently exercise nothing — the
# relaxation would be a no-op in every scripted scenario and real in
# production, which is the divergence this file exists to prevent.


def test_the_double_dispatches_a_before_model_event_carrying_an_agent_and_a_model():
    from nora.testing import ScriptedModel
    from nora.testing.scripted_agent import _Agent, _BeforeModelCall

    event = _BeforeModelCall(_Agent(ScriptedModel()))
    assert event.cancel is False
    assert isinstance(event.agent.model, ScriptedModel)


def test_the_doubles_first_call_tool_choice_agrees_with_the_real_one():
    """The transcribed constant, pinned against the module it was copied from.

    `nora.testing` sits below `nora.agent` in the layers contract and cannot
    import it, so `TOOL_CHOICE_FIRST_CALL` is a copy — and a copy nobody
    compares drifts silently.
    """
    from nora.agent.tool_choice import FIRST_CALL
    from nora.testing import TOOL_CHOICE_FIRST_CALL

    assert TOOL_CHOICE_FIRST_CALL == FIRST_CALL


def test_the_scripted_model_replaces_params_the_way_the_real_one_does():
    """`update_config` is `config.update(...)`, a shallow replace. A double
    that merged would let `relax` drop `max_tokens` here and in production."""
    from nora.testing import ScriptedModel

    model = ScriptedModel({"max_tokens": 7, "tool_choice": "required"})
    model.update_config(params={"tool_choice": "auto"})
    assert model.get_config()["params"] == {"tool_choice": "auto"}


async def test_the_double_relaxes_tool_choice_after_a_terminal_verb_runs():
    """THE PROBE for the before-model half.

    `now` is terminal with a budget of 2, so the guard permits a second model
    call and relaxes it — the `required → auto` relaxation sequence. Drop
    `agent` from `_BeforeModelCall` (or stop passing a model) and this goes red
    while every other scripted scenario stays green.
    """
    guard = ReplyGuard(conversation_id="g1", budgets={"now": 2}, terminal={"now"},
                       metrics=Metrics())
    runner = FakeAgentRunner([
        ScriptedTurn(tool_calls=[("now", {})]),
        ScriptedTurn(tool_calls=[("now", {})]),
    ])
    tool = adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          budget=2, terminal=True))
    await runner.invoke(tools=[tool], guard=guard, limits=None)
    assert runner.tool_choices == ["required", "auto"]


async def test_each_invocation_gets_its_own_scripted_model():
    """Per-tag, mirroring `LiveAgentRunner`'s `ModelSource`. Share one and the
    second tag would start where the first left off — `auto` on its first call,
    which can answer in prose and send nothing."""
    runner = FakeAgentRunner([ScriptedTurn(tool_calls=[("now", {})])])
    tool = adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          budget=2, terminal=True))
    guard_kwargs = {"conversation_id": "g1", "budgets": {"now": 2}, "terminal": {"now"}}
    await runner.invoke(tools=[tool], guard=ReplyGuard(metrics=Metrics(), **guard_kwargs),
                        limits=None)
    await runner.invoke(tools=[tool], guard=ReplyGuard(metrics=Metrics(), **guard_kwargs),
                        limits=None)
    assert len(runner.models) == 2
    assert runner.models[0] is not runner.models[1]
    assert runner.tool_choices == ["required", "required"]
