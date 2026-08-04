
from evals.evaluators import (
    ExactlyOnceSendReply,
    HonchoFedOncePerReply,
    NoRedundantRead,
    NoSendWithoutTrigger,
    ToolBudget,
)
from evals.harness import Trajectory


def _t(tool_calls=(), sent=(), reply=None, feeds=0, prefill_sufficient=True) -> Trajectory:
    return Trajectory(tool_calls=list(tool_calls), sent=list(sent), reply=reply,
                      honcho_feeds=feeds, prefill_sufficient=prefill_sufficient)


def test_exactly_once_passes_on_a_single_send():
    assert ExactlyOnceSendReply().evaluate(_t(["send_reply"], ["hi"], "hi")).passed


def test_exactly_once_fails_on_zero_sends():
    assert not ExactlyOnceSendReply().evaluate(_t([], [], None)).passed


def test_exactly_once_fails_on_two_sends():
    assert not ExactlyOnceSendReply().evaluate(_t(["send_reply", "send_reply"],
                                                  ["a", "b"], "b")).passed


def test_no_send_without_trigger_passes_on_silence():
    assert NoSendWithoutTrigger().evaluate(_t()).passed


def test_no_send_without_trigger_fails_on_any_send():
    assert not NoSendWithoutTrigger().evaluate(_t(["send_reply"], ["oops"])).passed


def test_tool_budget_enforces_the_r7_limits():
    assert ToolBudget().evaluate(_t(["read_buffer", "read_buffer", "send_reply"])).passed
    assert not ToolBudget().evaluate(
        _t(["read_buffer", "read_buffer", "read_buffer", "send_reply"])).passed
    assert not ToolBudget().evaluate(_t(["read_peer_card", "read_peer_card"])).passed


def test_no_redundant_read_fails_when_prefill_was_sufficient():
    assert not NoRedundantRead().evaluate(
        _t(["read_buffer", "send_reply"], prefill_sufficient=True)).passed


def test_no_redundant_read_allows_a_read_when_prefill_was_thin():
    assert NoRedundantRead().evaluate(
        _t(["read_buffer", "send_reply"], prefill_sufficient=False)).passed


def test_honcho_fed_once_per_delivered_reply():
    assert HonchoFedOncePerReply().evaluate(_t(["send_reply"], ["hi"], "hi", feeds=1)).passed
    assert not HonchoFedOncePerReply().evaluate(
        _t(["send_reply"], ["hi"], "hi", feeds=2)).passed
    assert HonchoFedOncePerReply().evaluate(_t([], [], None, feeds=0)).passed


def test_deterministic_evaluators_are_hard_gates():
    """No confidence weighting — these are mechanical facts (spec E.4)."""
    result = ExactlyOnceSendReply().evaluate(_t([], [], None))
    assert not hasattr(result, "confidence")
