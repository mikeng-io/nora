"""DEC-42: a `send_reply` ends the turn unless it carried `more=true`, so the
relaxed loop does not spend one more FULL model call after the first message
just so she could send a second.

Every probe here drives the REAL Strands loop with the REAL `ReplyGuard` and
the registry's real budgets and terminal set, and counts model calls, which is
the cost being bounded. The model is scripted: call N emits the N-th
`send_reply` input, then plain text once the script runs out (what `auto`
allows after she has spoken).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

from strands.models.model import Model

from nora.agent import factory
from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.agent.registry import ToolRegistry
from nora.app.agent_runner import LiveAgentRunner
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget, NormalizedMessage, TagContext
from nora.obs.metrics import Metrics
from tests.fakes import FixedModelSource, SinkSender

RETRY = factory.build_retry_policy(timeout_seconds=30.0)
TARGET = ConversationTarget("signal", "g1", "group")


class _Store:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _Scripted(Model):
    def __init__(self, script: list[dict]) -> None:
        self.script = script
        self.calls = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        i = self.calls
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if i < len(self.script):
            step = dict(self.script[i])
            name = step.pop("__tool__", "send_reply")
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": name, "toolUseId": f"t{i}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {
                "input": json.dumps(step)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 1}}}


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>x</nora_doctrine>", doctrine_text="x",
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


def _ctx() -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )
    return TagContext(
        tag_id="tag-1", message=msg, tagger=None, transcript=(), peer_card=(), started_at=0.0,
    )


async def _drive(script: list[dict], statuses: list[str] | None = None):
    """Returns (model calls, bodies the tool was asked to send, guard)."""
    registry = ToolRegistry()
    budgets = registry.budgets()
    model = _Scripted(script)
    sent: list[str] = []
    later: list[str] = []
    answers = list(statuses or [])
    deliverer = Deliverer(
        store=_Store(), adapter=SinkSender(), target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="t1", nora_sender_id="nora", message_cap=budgets["send_reply"],
        audience="tag", metrics=Metrics(),
    )

    async def send_reply(text: str, reply_to: int | None = None,
                         more: bool | None = False, heads_up: bool | None = False) -> str:
        """Send.

        Args:
            text: the text.
            reply_to: optional.
            more: optional.
            heads_up: optional.
        """
        sent.append(text)
        return answers.pop(0) if answers else "sent"

    async def now() -> str:
        """Time.

        Returns: a string.
        """
        later.append("ran")
        return "Thursday"

    tool = adapt(ToolSpec(name="send_reply", description="", schema={}, call=send_reply,
                          budget=budgets["send_reply"]))
    now_tool = adapt(ToolSpec(name="now", description="", schema={}, call=now,
                              budget=budgets["now"]))
    guard = ReplyGuard(
        conversation_id="g1", budgets=budgets, terminal=registry.terminal_tools(),
        deliverer=deliverer, metrics=Metrics(),
    )
    await LiveAgentRunner(models=FixedModelSource(model), retry=RETRY).invoke(
        doctrine=_doctrine(), tools=[tool, now_tool], pre_fill=[], guard=guard,
        tag_message="<tag_message/>", ctx=_ctx(), trace_attributes={},
        limits={"turns": 8, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    guard.later = later  # type: ignore[attr-defined]
    return model.calls, sent, guard


async def test_more_false_ends_the_turn_with_no_further_model_call():
    calls, sent, guard = await _drive([{"text": "a"}])
    assert (calls, sent) == (1, ["a"])
    assert guard.cancelled_model_call is True   # DEC-93: the cancel text is the guard's


async def test_more_false_stated_explicitly_is_the_same():
    calls, sent, _ = await _drive([{"text": "a", "more": False}])
    assert (calls, sent) == (1, ["a"])


async def test_more_true_allows_a_second_send_then_it_ends():
    calls, sent, _ = await _drive([{"text": "a", "more": True}, {"text": "b"}])
    assert (calls, sent) == (2, ["a", "b"])


async def test_more_true_then_declining_is_a_normal_stop():
    calls, sent, guard = await _drive([{"text": "a", "more": True}])
    assert (calls, sent) == (2, ["a"])
    assert guard.cancelled_model_call is False   # she stopped, nothing was cancelled


async def test_the_budget_still_caps_at_three():
    cap = ToolRegistry().budgets()["send_reply"]
    assert cap == 3
    script = [{"text": f"m{i}", "more": True} for i in range(5)]
    calls, sent, _ = await _drive(script)
    assert sent == ["m0", "m1", "m2"]
    assert calls == 3        # the fourth model call is cancelled, not paid for


async def test_a_refused_send_keeps_the_turn_open_for_a_retry():
    """R-6 / R-18 / send failure: `refused` is not the last word."""
    calls, sent, _ = await _drive(
        [{"text": "leaky"}, {"text": "clean"}], statuses=["refused", "sent"],
    )
    assert (calls, sent) == (2, ["leaky", "clean"])


async def test_a_held_send_ends_the_turn():
    calls, sent, _ = await _drive([{"text": "a"}], statuses=["held_for_review"])
    assert (calls, sent) == (1, ["a"])


async def test_work_after_the_reply_runs_when_more_is_true():
    """The relaxed call is also where a note or a publish happens."""
    _calls, sent, guard = await _drive([{"text": "a", "more": True}, {"__tool__": "now"}])
    assert sent == ["a"]
    assert guard.later == ["ran"]


async def test_work_after_the_reply_is_dropped_without_more():
    calls, sent, guard = await _drive([{"text": "a"}, {"__tool__": "now"}])
    assert (calls, sent, guard.later) == (1, ["a"], [])


async def test_the_guard_reads_more_as_the_tool_does():
    """Strands coerces "true" to True for the tool; the guard must agree, or the
    turn ends while the tool saw «more»."""
    calls, sent, _ = await _drive([{"text": "a", "more": "true"}, {"text": "b"}])
    assert (calls, sent) == (2, ["a", "b"])


async def test_a_null_more_is_false_and_the_send_still_goes():
    calls, sent, _ = await _drive([{"text": "a", "more": None}])
    assert (calls, sent) == (1, ["a"])


# --- the heads-up's answer is owed until it is sent ---------------------------


async def test_a_heads_up_keeps_the_turn_open_and_owes_its_answer():
    """The pipeline's recovery reads this: without it a delivered heads-up
    would disable DEC-93's recovery and the prose answer would be dropped."""
    _calls, sent, guard = await _drive([{"text": "let me check", "heads_up": True},
                                        {"__tool__": "now"}])
    assert sent == ["let me check"] and guard.later == ["ran"]
    assert guard.reply_still_owed is True


async def test_an_answer_sent_after_the_heads_up_settles_it():
    _calls, sent, guard = await _drive([{"text": "let me check", "heads_up": True},
                                        {"__tool__": "now"}, {"text": "it is Thursday"}])
    assert sent == ["let me check", "it is Thursday"]
    assert guard.reply_still_owed is False


async def test_an_answer_with_more_then_a_note_owes_nothing():
    """Inferring the heads-up from «a `more=true` send, then work» would read
    this as owed, and closing prose after the note would reach the room."""
    _calls, sent, guard = await _drive([{"text": "it is Thursday", "more": True},
                                        {"__tool__": "now"}])
    assert sent == ["it is Thursday"] and guard.later == ["ran"]
    assert guard.reply_still_owed is False


async def test_a_plain_reply_owes_nothing():
    _calls, _sent, guard = await _drive([{"text": "a"}])
    assert guard.reply_still_owed is False
