"""The deterministic evaluators — mechanical facts, no judge, no model.

Split out of `tests/test_evals_gate.py` when the old eval CLI was deleted
(`design/evaluation/` Step 5). That file also drove `evals.run`, `evals.sink`
and `evals.proactive`, which went; these assertions need only the evaluators
themselves and `Trajectory`.

**It carries an ACCEPTANCE RULE.** `test_no_redundant_read_fails_when_prefill_
was_sufficient` is R-15's only named test (`tests/test_acceptance_rules.py`),
and the delete would otherwise have taken a rule with it — which is how the
first attempt at Step 5 was found unsafe. A grader that dies with its driver
takes whatever it enforced along with it.
"""
from __future__ import annotations

from evals.evaluators import (
    HonchoFedOncePerReply,
    NoRedundantRead,
    NoSendWithoutTrigger,
    RepliesAtLeastOnce,
    ToolBudget,
)
from evals.harness import Trajectory


def _t(tool_calls=(), sent=(), reply=None, feeds=0, prefill_sufficient=True) -> Trajectory:
    return Trajectory(tool_calls=list(tool_calls), sent=list(sent), reply=reply,
                      honcho_feeds=feeds, prefill_sufficient=prefill_sufficient)


def test_replies_at_least_once_passes_on_a_single_send():
    assert RepliesAtLeastOnce().evaluate(_t(["send_reply"], ["hi"], "hi")).passed


def test_replies_at_least_once_fails_on_zero_sends():
    assert not RepliesAtLeastOnce().evaluate(_t([], [], None)).passed


def test_replies_at_least_once_passes_on_two_sends():
    """Multi-message (DEC-42): two replies are valid, so this PASSES where the
    cap-of-1 `ExactlyOnceSendReply` failed. The upper bound (never past the cap)
    moved to `ToolBudget`, asserted in test_tool_budget_enforces_the_r7_limits."""
    assert RepliesAtLeastOnce().evaluate(_t(["send_reply", "send_reply"],
                                            ["a", "b"], "b")).passed


def test_no_send_without_trigger_passes_on_silence():
    assert NoSendWithoutTrigger().evaluate(_t()).passed


def test_no_send_without_trigger_fails_on_any_send():
    assert not NoSendWithoutTrigger().evaluate(_t(["send_reply"], ["oops"])).passed


def test_tool_budget_enforces_the_r7_limits():
    # Derived from the live budgets (doubled per operator 2026-08-08), so a
    # budget change cannot silently un-test the R-7 ceiling: exactly the budget
    # is allowed, one more is refused.
    from nora.agent.tools import NATIVE_TOOL_BUDGETS as B
    ce, gm, sr = B["load_transcript"], B["group_members"], B["send_reply"]
    assert ToolBudget().evaluate(_t(["load_transcript"] * ce + ["send_reply"])).passed
    assert not ToolBudget().evaluate(_t(["load_transcript"] * (ce + 1) + ["send_reply"])).passed
    assert not ToolBudget().evaluate(_t(["group_members"] * (gm + 1))).passed
    # send_reply's own upper bound (the cap) is ToolBudget's job now that
    # RepliesAtLeastOnce owns only the lower bound (DEC-42).
    assert ToolBudget().evaluate(_t(["send_reply"] * sr)).passed
    assert not ToolBudget().evaluate(_t(["send_reply"] * (sr + 1))).passed


def test_no_redundant_read_fails_when_prefill_was_sufficient():
    assert not NoRedundantRead().evaluate(
        _t(["load_transcript", "send_reply"], prefill_sufficient=True)).passed


def test_no_redundant_read_allows_a_read_when_prefill_was_thin():
    assert NoRedundantRead().evaluate(
        _t(["load_transcript", "send_reply"], prefill_sufficient=False)).passed


def test_honcho_fed_once_per_delivered_reply():
    assert HonchoFedOncePerReply().evaluate(_t(["send_reply"], ["hi"], "hi", feeds=1)).passed
    assert not HonchoFedOncePerReply().evaluate(
        _t(["send_reply"], ["hi"], "hi", feeds=2)).passed
    assert HonchoFedOncePerReply().evaluate(_t([], [], None, feeds=0)).passed


def test_deterministic_evaluators_are_hard_gates():
    """No confidence weighting — these are mechanical facts (spec E.4)."""
    result = RepliesAtLeastOnce().evaluate(_t([], [], None))
    assert not hasattr(result, "confidence")
