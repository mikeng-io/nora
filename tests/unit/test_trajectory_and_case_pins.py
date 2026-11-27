"""The wake trajectory's terminal response, and a version-2 case's declared turns."""

import pytest

from nora.subject.eval_evidence import EvidenceError
from scripts.cognition_loop.cases import Case
from scripts.cognition_loop.trajectory import wake_trajectory


def _terminal_attempt(text: str) -> list[dict]:
    identity = {"invocation_id": "primary", "attempt_id": "primary:0:1"}
    return [
        {**identity, "kind": "invocation_started", "parent_invocation_id": None},
        {**identity, "kind": "call_started"},
        {**identity, "kind": "transport_started"},
        {**identity, "kind": "transport_response", "response": {"choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}},
        {**identity, "kind": "transport_finished"},
        {**identity, "kind": "call_finished", "retry": False},
        {**identity, "kind": "invocation_finished"},
    ]


def test_a_completion_model_started_after_the_terminal_cannot_replace_the_main_response():
    events = [
        *_terminal_attempt("the main answer"),
        {"name": "nora.session.terminal", "kind": "event", "attributes": {}},
        {"kind": "invocation_started", "invocation_id": "completion"},
    ]
    trajectory = wake_trajectory(events, wake_id=1, session_id=None, tokens=None)
    assert trajectory["final_response"]["status"] == "observed"
    assert trajectory["final_response"]["text"] == "the main answer"


def test_a_version_2_turn_is_generated_text_or_a_declared_envelope():
    with pytest.raises(EvidenceError, match="version-2 turn"):
        Case("c", "unit", (), turns=(42,), version=2)
    assert Case("c", "unit", (), turns=("say hi",), version=2).turns == ("say hi",)
