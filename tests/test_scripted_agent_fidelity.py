"""DEC-33: `nora.testing.FakeAgentRunner` must dispatch the hooks the SDK does.

The double replays a scripted tool sequence against the REAL tools and the
REAL guard, which is the only reason a scripted scenario proves anything about
`ReplyGuard`. It used to call `evaluate`/`record` by hand instead of going
through the guard's hook callbacks, and the hand-written version diverged in
one place: on a veto it skipped the after-tool notification entirely. So the
budget a vetoed call costs was decided by the FAKE in tests and by
`ReplyGuard._after_tool` in production, and those two disagreed for the whole
life of the deployment. No scripted scenario could reproduce the live R-4 →
R-2 lockout, and a regression test written against the old double would have
been decorative (CLAUDE.md, "make the fake behave like the real server").

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
                          when="the stub turn needs a tool", budget=1))


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
    await LiveAgentRunner(model=_AsksTwice(), retry=RETRY).invoke(
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
    which is exactly how the live budget bug survived."""
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
                          when="the stub turn needs a tool", budget=1))
    await FakeAgentRunner([ScriptedTurn(tool_calls=[("now", {}), ("now", {})])]).invoke(
        tools=[tool], guard=_guard(), limits=None,
    )
    assert calls == ["now"]
