"""DEC-32: which verbs END A TURN is a property of the TOOL, not of the guard.

Two layers, because the property spans both:

* the unit half — `ReplyGuard` cancels the model call once a tool it was told
  is terminal has run, and does not otherwise;
* the SDK half — that cancellation actually stops the real Strands loop, which
  is the only reason a turn ever ends at all (`tool_choice="required"` means
  the model cannot stop by declining to call something — DEC-21).

The probe this file exists to be: `test_a_second_verb_declared_terminal_ends_
the_turn` drives the REAL loop with `now` marked terminal and asserts the model
is called ONCE. Revert `_before_model` to asking `deliverer.delivered` and it
goes red — the deliverer in that test has delivered nothing, and `now` has no
side effect for it to observe. That is the whole content of the refactor: a
second terminal verb (`send_gif`, or a distinct feed step on the tag path) is
a table edit, not a guard edit.
"""

from __future__ import annotations

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

# The terminal verb's budget IS the message cap (DEC-42) — the turn ends when no
# terminal verb can be called AGAIN, not when one has been called once. `1`
# here mirrors `send_reply`'s own budget; `BUDGETS_TWICE` is the same
# declaration with the cap raised, which is what multi-message does.
BUDGETS = {"now": 1}
BUDGETS_TWICE = {"now": 2}


# ---- the shapes the SDK really passes -------------------------------------
#
# `_after_tool` reads three fields and the difference between two of them is
# load-bearing, so the doubles below carry all three exactly as
# `strands.tools.executors._executor` builds them: a call that RAN gets a
# success `ToolResult` and `cancel_message=None`; a call `_before_tool`
# cancelled gets an ERROR result, `cancel_message` set — and `exception` still
# None, which is why `exception` alone cannot tell them apart.


class _AfterToolCall:
    def __init__(self, name: str, *, cancel_message: str | None = None,
                 exception: Exception | None = None) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": "t1"}
        self.exception = exception
        self.cancel_message = cancel_message
        self.result = (
            {"toolUseId": "t1", "status": "error", "content": [{"text": cancel_message}]}
            if cancel_message is not None
            else {"toolUseId": "t1", "status": "success", "content": [{"text": "True"}]}
        )


class _BeforeModelCall:
    """`cancel` starts False and is a `bool | str`, exactly as the SDK declares."""

    def __init__(self) -> None:
        self.cancel: bool | str = False


class _NeverLatches:
    """A `Deliverer` that delivered nothing — the control for the probe.

    Not a stand-in for the real class: `delivered`/`attempted`/`exhausted` are
    the properties `ReplyGuard` reads, and all answer False here because
    nothing was ever sent and nothing was therefore spent. A test whose
    deliverer claimed delivery would make the reverted implementation pass and
    the probe worthless.
    """

    delivered = False
    attempted = False
    exhausted = False


def _guard(*, terminal, deliverer=None, metrics=None, budgets=None) -> ReplyGuard:
    return ReplyGuard(
        conversation_id="g1", budgets=budgets or BUDGETS, terminal=terminal,
        deliverer=deliverer, metrics=metrics or Metrics(),
    )


# ---- the unit half ---------------------------------------------------------


def test_the_turn_is_not_over_before_anything_has_run():
    event = _BeforeModelCall()
    _guard(terminal={"now"})._before_model(event)
    assert event.cancel is False


def test_a_terminal_tool_that_ran_ends_the_turn():
    guard = _guard(terminal={"now"})
    guard._after_tool(_AfterToolCall("now"))
    event = _BeforeModelCall()
    guard._before_model(event)
    assert event.cancel == "Reply already sent — the turn is finished."


def test_a_tool_that_is_not_terminal_leaves_the_turn_running():
    """The same call, the same guard, one different declaration."""
    guard = _guard(terminal={"send_reply"})
    guard._after_tool(_AfterToolCall("now"))
    event = _BeforeModelCall()
    guard._before_model(event)
    assert event.cancel is False


def test_a_terminal_call_the_guard_cancelled_does_not_end_the_turn():
    """A vetoed `send_reply` never ran, so it cannot have ended anything.

    Strands fires `AfterToolCallEvent` for a cancelled call too, with
    `exception=None` — so a terminal latch that only checked `exception` would
    treat an empty-text reply refused by R-4 as a delivered one and cancel the
    loop in silence. `cancel_message` is what separates them.
    """
    guard = _guard(terminal={"now"})
    guard._after_tool(_AfterToolCall("now", cancel_message="Your reply text is empty."))
    event = _BeforeModelCall()
    guard._before_model(event)
    assert event.cancel is False


def test_a_terminal_call_that_raised_does_not_end_the_turn():
    guard = _guard(terminal={"now"})
    guard._after_tool(_AfterToolCall("now", exception=RuntimeError("boom")))
    event = _BeforeModelCall()
    guard._before_model(event)
    assert event.cancel is False


def test_replies_end_the_turn_at_the_cap_not_before():
    """Multi-message (DEC-42): the turn ends when `send_reply`'s BUDGET is spent,
    not on the first reply.

    A reply the deliverer REFUSED still spends one — it was RECORDED — so the
    group sees the same silence either way; but a refused FIRST reply does not
    end the turn, because messages remain. Once all are spent the turn is over
    and the (cap+1)th `send_reply` is already refused by the budget. If the
    budget ever stops refusing that call, this is where the loss surfaces.

    The cap is READ from the registry rather than written here. DEC-10 puts the
    number with the tool and DEC-32 requires exactly ONE of it; a literal in
    this test is a second copy that goes stale the next time an operator moves
    the budget (DEC-95)."""
    cap = ToolRegistry().budgets()["send_reply"]
    assert cap >= 2, "this test needs headroom to distinguish 'not before' from 'at'"
    guard = ReplyGuard(
        conversation_id="g1", budgets=ToolRegistry().budgets(),
        terminal=ToolRegistry().terminal_tools(), deliverer=_NeverLatches(),
        metrics=Metrics(),
    )
    # Every recorded reply BUT the last (delivered nothing) leaves the turn open.
    for _ in range(cap - 1):
        guard._after_tool(_AfterToolCall("send_reply"))
        event = _BeforeModelCall()
        guard._before_model(event)
        assert event.cancel is False
    # The last one spends the final message; the turn is now over.
    guard._after_tool(_AfterToolCall("send_reply"))
    event = _BeforeModelCall()
    guard._before_model(event)
    assert event.cancel == "Reply already sent — the turn is finished."
    assert guard.evaluate("send_reply", {"text": "let me try again"}) is not None


# ---- the table, and what the registry makes of it --------------------------


def test_reply_verbs_are_terminal():
    assert ToolRegistry().terminal_tools() == frozenset({"send_reply", "send_gif"})


def test_every_spec_carries_the_declaration_the_registry_reports():
    """The chain the refactor rests on: table → `ToolSpec` → guard.

    `budgets()` has the same property (`tests/unit/test_tool_registry.py`), and
    for the same reason — DEC-10's "the number enforced and the number declared
    are one object", with a second field travelling the same road.
    """
    registry = ToolRegistry()
    specs = registry.specs(
        store=_Store(), conversation_id="g1", conversation_type="group",
        persons=_Persons(), honcho=None, deliverer=_NeverLatches(),
        metrics=Metrics(), room=None,
    )
    assert {s.name for s in specs if s.terminal} == set(registry.terminal_tools())


class _Store:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _Persons:
    def resolve(self, platform: str, sender_id: str):
        return None


# ---- the SDK half: the cancellation really stops the real loop -------------


async def _now() -> str:
    """The current time.

    Returns: a fixed string.
    """
    return "Thursday"


class _AlwaysCallsNow(Model):
    """A model that asks for the same tool on every pass, and counts its calls.

    This is what `tool_choice="required"` looks like from the loop's side: the
    model never emits a final answer, so nothing except a guard cancellation or
    one of R-8's caps can end the turn. `calls` is therefore a direct reading of
    how many passes the guard allowed.
    """

    def __init__(self) -> None:
        self.calls = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None, **kwargs: Any) -> Any:
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
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>", doctrine_text="be helpful",
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


async def _drive(*, terminal, budgets=None) -> _AlwaysCallsNow:
    """Four turns' worth of rope, a real `Deliverer` that is never called."""
    model = _AlwaysCallsNow()
    budgets = budgets or BUDGETS
    deliverer = Deliverer(
        store=_Store(), adapter=SinkSender(), target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="t1", nora_sender_id="nora", message_cap=1, audience="tag",
        metrics=Metrics(),
    )
    tool = adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          budget=budgets["now"]))
    await LiveAgentRunner(models=FixedModelSource(model), retry=RETRY).invoke(
        doctrine=_doctrine(), tools=[tool], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=budgets, terminal=terminal,
                         deliverer=deliverer, metrics=Metrics()),
        tag_message="<tag_message/>", ctx=_ctx(), trace_attributes={},
        limits={"turns": 4, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    return model


async def test_a_second_verb_declared_terminal_ends_the_turn():
    """THE PROBE. `now` is terminal here; the loop must stop after one pass.

    Nothing was delivered — the `Deliverer` is real and untouched — so a guard
    that decides "she has replied" by asking it cannot possibly cancel, and the
    model runs until R-8's turn cap. That is what makes this red on revert.
    """
    model = await _drive(terminal={"now"})
    assert model.calls == 1


async def test_the_same_turn_with_nothing_terminal_runs_to_the_cap():
    """The control: identical model, identical tool, one different declaration.

    Without it, `calls == 1` above could mean anything — a fake that stopped on
    its own, a limit misread. It stops because of the declaration, and here the
    declaration is today's real one (`send_reply`), which `now` is not.
    """
    model = await _drive(terminal=ToolRegistry().terminal_tools())
    # The LOOP runs to exactly four — `turns` is 4 and nothing terminal ever
    # stops it, which is the property this control exists to prove. The fifth
    # call is the budget-exhaustion draft: a turn that ends `limit_turns` with
    # nothing said gets one tool-less completion over what it gathered before the
    # pipeline decides what the room reads.
    #
    # Kept as a call count rather than relaxed to `>= 4`: the number IS the
    # assertion here, and it is also the cheapest place the extra cost of that
    # draft would show up if it ever started firing on turns it should not.
    assert model.calls == 5


# ---- DEC-42: the turn ends at the CAP, not at the first terminal call ------


def test_a_terminal_tool_with_budget_left_does_not_end_the_turn():
    """The unit half of the relaxation, and the one line of DEC-42 that makes
    multi-message possible at all.

    Same guard, same terminal declaration, same single call — one different
    number. If the turn ended the instant a terminal verb ran, `send_reply`
    could never produce a second message however high its budget went: the loop
    would be cancelled before the model was asked again.
    """
    guard = _guard(terminal={"now"}, budgets=BUDGETS_TWICE)
    guard._after_tool(_AfterToolCall("now"))
    event = _BeforeModelCall()
    guard._before_model(event)
    assert event.cancel is False


def test_the_turn_ends_once_the_terminal_budget_is_spent():
    guard = _guard(terminal={"now"}, budgets=BUDGETS_TWICE)
    guard._after_tool(_AfterToolCall("now"))
    guard._after_tool(_AfterToolCall("now"))
    event = _BeforeModelCall()
    guard._before_model(event)
    assert event.cancel == "Reply already sent — the turn is finished."


def test_a_guard_told_nothing_is_terminal_does_not_end_the_turn_immediately():
    """`all(())` is True, so the exhaustion test has to check emptiness first.

    Without that check a guard with no terminal verbs would cancel before the
    FIRST model call — every tag silent, and silent is R-5's normal-looking
    state.
    """
    event = _BeforeModelCall()
    _guard(terminal=frozenset())._before_model(event)
    assert event.cancel is False


async def test_a_raised_terminal_budget_buys_a_second_model_call():
    """The SDK half of the same thing: the real loop runs twice, not once.

    `test_a_second_verb_declared_terminal_ends_the_turn` above is this test's
    control at a budget of 1 — identical model, identical tool, identical
    declaration, and it stops after one pass.
    """
    model = await _drive(terminal={"now"}, budgets=BUDGETS_TWICE)
    assert model.calls == 2
