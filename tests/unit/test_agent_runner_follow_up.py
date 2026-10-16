"""`LiveAgentRunner.invoke`'s `follow_up`, against the REAL Strands loop.

The summons occasion's tests (`tests/unit/test_summons_occasion.py`) run on
`FakeAgentRunner`, which models the follow-up. These tests cover what only the
real SDK can answer: that the continuation is the SAME conversation (the model's
second pass sees its own first-pass prose, then the follow-up as the next
user turn), that the guard's per-tool budgets carry into it rather than being
re-granted, and that it runs under what the first pass LEFT of the loop caps,
not a fresh set (Strands' caps are per-invocation, `factory.remaining_limits`).
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from types import SimpleNamespace
from typing import Any

import pytest
from strands.models.model import Model

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.app.agent_runner import LiveAgentRunner
from nora.doctrine.loader import Doctrine
from nora.domain.models import NormalizedMessage, TagContext
from nora.obs.metrics import Metrics
from tests.fakes import FixedModelSource

RETRY = factory.build_retry_policy(timeout_seconds=30.0)
FOLLOW_UP = "You wrote a reply but did not send it."
LIMITS = {"turns": 8, "output_tokens": 12_000, "total_tokens": 64_000}


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    async def now(self) -> str:
        """The current time.

        Returns: a fixed string.
        """
        self.calls += 1
        return "Thursday"


class _ScriptedModel(Model):
    """Answers each model call from `script` in order: `("tool", name)` emits
    a toolUse, `("text", words)` ends the turn with prose. Records a copy of
    the messages every call was sent, which is the instrument here."""

    def __init__(self, script: list[tuple[str, str]], *, tokens_per_call: int = 10) -> None:
        self._script = list(script)
        self._tokens = tokens_per_call
        self.seen: list[list[dict[str, Any]]] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        self.seen.append([dict(m) for m in messages])
        kind, value = self._script.pop(0) if self._script else ("text", "done")
        yield {"messageStart": {"role": "assistant"}}
        if kind == "tool":
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": value, "toolUseId": f"t{len(self.seen)}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": value}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if kind == "tool" else "end_turn"}}
        yield {"metadata": {
            "usage": {"inputTokens": self._tokens // 2, "outputTokens": self._tokens // 2,
                      "totalTokens": self._tokens},
            "metrics": {"latencyMs": 1},
        }}


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>", doctrine_text="be helpful",
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


def _ctx() -> TagContext:
    msg = NormalizedMessage(
        platform="instagram", conversation_id="summons:1", conversation_type="public",
        sender_id="", sender_name=None, timestamp=1, body="q", raw={},
    )
    return TagContext(
        tag_id="s-1", message=msg, tagger=None, transcript=(), peer_card=(), started_at=0.0,
    )


async def _invoke(
    model: _ScriptedModel, *, follow_up: Any, limits: dict | None = None,
    clock: _Clock | None = None,
) -> tuple[str | None, str]:
    stop_reason, text, _trace = await _invoke_traced(
        model, follow_up=follow_up, limits=limits, clock=clock,
    )
    return stop_reason, text


async def _invoke_traced(
    model: _ScriptedModel, *, follow_up: Any, limits: dict | None = None,
    clock: _Clock | None = None,
) -> tuple[str | None, str, Any]:
    clock = clock or _Clock()
    runner = LiveAgentRunner(models=FixedModelSource(model), retry=RETRY)
    return await runner.invoke(
        doctrine=_doctrine(),
        tools=[adapt(ToolSpec(name="now", description="", schema={}, call=clock.now, budget=1))],
        pre_fill=[],
        guard=ReplyGuard(conversation_id="summons:1", budgets={"now": 1, "send_reply": 1},
                         terminal=frozenset({"send_reply"}), metrics=Metrics()),
        tag_message="<summons/>", limits=dict(limits or LIMITS), ctx=_ctx(),
        trace_attributes={}, follow_up=follow_up,
    )


def _texts(message: dict[str, Any]) -> list[str]:
    return [b["text"] for b in message.get("content", []) if "text" in b]


async def test_the_follow_up_continues_the_same_conversation_once():
    asked: list[str] = []

    def follow_up(text: str) -> str | None:
        asked.append(text)
        return FOLLOW_UP

    model = _ScriptedModel([("text", "It is a heron."), ("text", "fine, silent")])
    stop_reason, text = await _invoke(model, follow_up=follow_up)
    assert asked == ["It is a heron."]
    assert len(model.seen) == 2
    second = model.seen[1]
    # Her own first-pass prose, then the follow-up as the next user turn.
    assert second[-2]["role"] == "assistant" and _texts(second[-2]) == ["It is a heron."]
    assert second[-1]["role"] == "user" and _texts(second[-1]) == [FOLLOW_UP]
    assert _texts(second[0]) == ["<summons/>"]
    assert (stop_reason, text) == ("end_turn", "fine, silent")


async def test_no_message_back_means_no_continuation():
    model = _ScriptedModel([("text", "It is a heron.")])
    stop_reason, text = await _invoke(model, follow_up=lambda _text: None)
    assert len(model.seen) == 1
    assert (stop_reason, text) == ("end_turn", "It is a heron.")


async def test_the_guards_budgets_carry_into_the_follow_up():
    """`now` has a budget of 1: spent in the first pass, the follow-up's call
    is vetoed by the SAME guard, so the tool runs once in the whole turn."""
    clock = _Clock()
    model = _ScriptedModel([
        ("tool", "now"), ("text", "It is Thursday."),
        ("tool", "now"), ("text", "done"),
    ])
    await _invoke(model, follow_up=lambda _text: FOLLOW_UP, clock=clock)
    assert len(model.seen) == 4
    assert clock.calls == 1


async def test_the_follow_up_runs_under_what_the_first_pass_left_of_the_caps():
    """turns=3, the first pass spends two cycles, so the follow-up has ONE:
    its tool call is cut at the top of its second cycle. A fresh budget would
    have let it run to its prose (a fourth model call)."""
    model = _ScriptedModel([
        ("tool", "now"), ("text", "It is Thursday."),
        ("tool", "now"), ("text", "done"),
    ])
    stop_reason, _text = await _invoke(
        model, follow_up=lambda _text: FOLLOW_UP,
        limits={"turns": 3, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    assert stop_reason == "limit_turns"
    assert len(model.seen) == 3


async def test_a_loop_cut_off_by_a_cap_is_never_followed_up():
    asked: list[str] = []

    def follow_up(text: str) -> str | None:
        asked.append(text)
        return FOLLOW_UP

    model = _ScriptedModel([("tool", "now"), ("tool", "now")], tokens_per_call=5_000)
    stop_reason, _text = await _invoke(
        model, follow_up=follow_up,
        limits={"turns": 8, "output_tokens": 12_000, "total_tokens": 4_000},
    )
    assert stop_reason == "limit_total_tokens"
    assert asked == []


def _all_text(messages: Any) -> str:
    return repr(messages)


async def test_the_trace_carries_the_follow_up_and_its_reply():
    """One turn, one trace: the follow-up user turn and what she answered to
    it are in the extracted `TagTrace`, after the first pass's prose."""
    model = _ScriptedModel([("text", "It is a heron."), ("text", "fine, silent")])
    _stop, _text, trace = await _invoke_traced(model, follow_up=lambda _t: FOLLOW_UP)
    flat = _all_text(trace.messages)
    assert "It is a heron." in flat and FOLLOW_UP in flat and "fine, silent" in flat
    assert flat.index("It is a heron.") < flat.index(FOLLOW_UP) < flat.index("fine, silent")


@pytest.mark.parametrize(
    "stop_reason",
    ["guardrail_intervened", "stop_sequence", "content_filtered", None,
     "limit_turns", "limit_total_tokens", "limit_output_tokens"],
)
async def test_only_end_turn_is_ever_followed_up(monkeypatch, stop_reason):
    """A guardrail, a stop sequence, a filter, a cap or a stop reason the SDK
    did not give is not a turn she ended in prose: never continued, even with
    prose in hand and a follow-up that would answer."""
    asked: list[str] = []

    def follow_up(text: str) -> str | None:
        asked.append(text)
        return FOLLOW_UP

    real_invoke = factory.invoke

    async def relabelled(agent: Any, message: str, **kw: Any) -> Any:
        # The REAL loop runs (so its metrics exist and caps are left); only
        # the stop reason it reports is swapped.
        result = await real_invoke(agent, message, **kw)
        return SimpleNamespace(stop_reason=stop_reason, message=result.message)

    monkeypatch.setattr(factory, "invoke", relabelled)
    model = _ScriptedModel([("text", "It is a heron."), ("text", "sent")])
    _stop, text = await _invoke(model, follow_up=follow_up)
    assert asked == []
    assert len(model.seen) == 1
    assert text == "It is a heron."


async def test_the_relabelled_control_is_followed_up_on_end_turn(monkeypatch):
    """The control for the test above: the same relabelling harness with
    `end_turn` IS followed up, so the negatives are not an artefact of it."""
    real_invoke = factory.invoke

    async def relabelled(agent: Any, message: str, **kw: Any) -> Any:
        result = await real_invoke(agent, message, **kw)
        return SimpleNamespace(stop_reason="end_turn", message=result.message)

    monkeypatch.setattr(factory, "invoke", relabelled)
    model = _ScriptedModel([("text", "It is a heron."), ("text", "sent")])
    _stop, text = await _invoke(model, follow_up=lambda _t: FOLLOW_UP)
    assert len(model.seen) == 2 and text == "sent"


def _agent_with(invocation: Any) -> Any:
    return SimpleNamespace(event_loop_metrics=SimpleNamespace(latest_agent_invocation=invocation))


@pytest.mark.parametrize("invocation", [
    None,
    SimpleNamespace(cycles=[1], usage=None),
    SimpleNamespace(cycles=None, usage={"outputTokens": 1, "totalTokens": 1}),
    SimpleNamespace(usage={"outputTokens": 1, "totalTokens": 1}),
    SimpleNamespace(cycles=[1]),
    SimpleNamespace(cycles=[1], usage={"totalTokens": 1}),
    SimpleNamespace(cycles=[1], usage={"outputTokens": "1", "totalTokens": 1}),
])
def test_remaining_limits_fails_closed_on_missing_metrics(invocation):
    """What the SDK did not measure is not room left: no remainder, so no
    follow-up, never a fresh budget."""
    assert factory.remaining_limits(_agent_with(invocation), dict(LIMITS)) is None


def test_remaining_limits_is_the_cap_minus_the_spend():
    invocation = SimpleNamespace(cycles=[1, 2], usage={"outputTokens": 100, "totalTokens": 900})
    assert factory.remaining_limits(_agent_with(invocation), dict(LIMITS)) == {
        "turns": 6, "output_tokens": 11_900, "total_tokens": 63_100,
    }
    spent = SimpleNamespace(cycles=[1] * 8, usage={"outputTokens": 0, "totalTokens": 0})
    assert factory.remaining_limits(_agent_with(spent), dict(LIMITS)) is None
