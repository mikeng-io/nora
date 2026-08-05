from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics

BUDGETS = ToolRegistry().budgets()


def _guard(metrics=None) -> ReplyGuard:
    return ReplyGuard(conversation_id="g1", budgets=BUDGETS, metrics=metrics or Metrics())


def _send(conversation_id="g1", text="hi") -> dict:
    return {"conversation_id": conversation_id, "conversation_type": "group", "text": text}


def test_a_first_send_reply_is_allowed():
    assert _guard().evaluate("send_reply", _send()) is None


def test_second_send_reply_is_cancelled():
    m = Metrics()
    g = _guard(m)
    g.evaluate("send_reply", _send())
    g.record("send_reply")
    msg = g.evaluate("send_reply", _send())
    assert msg is not None
    assert "already sent your reply" in msg
    assert "finish your turn" in msg
    assert m.get("nora_guard_vetoes_total", rule="R-2") == 1


def test_send_to_other_group_is_cancelled():
    m = Metrics()
    msg = _guard(m).evaluate("send_reply", _send(conversation_id="g2"))
    assert msg is not None and "this conversation" in msg
    assert m.get("nora_guard_vetoes_total", rule="R-3") == 1


def test_empty_reply_is_cancelled_before_the_tool_runs():
    m = Metrics()
    msg = _guard(m).evaluate("send_reply", _send(text="   "))
    assert msg is not None and "empty" in msg.lower()
    assert m.get("nora_guard_vetoes_total", rule="R-4") == 1


def test_tool_call_budget_enforced_for_read_buffer():
    m = Metrics()
    g = _guard(m)
    for _ in range(2):
        assert g.evaluate("read_buffer", {"conversation_id": "g1"}) is None
        g.record("read_buffer")
    msg = g.evaluate("read_buffer", {"conversation_id": "g1"})
    assert msg is not None and "read_buffer" in msg
    assert m.get("nora_guard_vetoes_total", rule="R-7") == 1


def test_tool_call_budget_enforced_for_read_peer_card():
    g = _guard()
    assert g.evaluate("read_peer_card", {"sender_id": "+1"}) is None
    g.record("read_peer_card")
    assert g.evaluate("read_peer_card", {"sender_id": "+1"}) is not None


def test_the_correction_names_what_the_model_already_has():
    g = _guard()
    g.record("read_buffer")
    g.record("read_buffer")
    msg = g.evaluate("read_buffer", {"conversation_id": "g1"})
    assert "already" in msg


def test_an_unknown_tool_is_cancelled():
    assert _guard().evaluate("rm_rf", {}) is not None


def test_read_buffer_to_another_conversation_is_cancelled():
    m = Metrics()
    msg = _guard(m).evaluate("read_buffer", {"conversation_id": "g2"})
    assert msg is not None
    assert m.get("nora_guard_vetoes_total", rule="R-3") == 1


def test_counters_are_per_guard_instance():
    """R-2: constructed fresh per tag, so counters reset by construction."""
    a, b = _guard(), _guard()
    a.record("send_reply")
    assert a.evaluate("send_reply", _send()) is not None
    assert b.evaluate("send_reply", _send()) is None


def test_a_missing_conversation_id_is_cancelled():
    assert _guard().evaluate("send_reply", {"text": "hi"}) is not None


def test_evaluate_does_not_mutate_counts():
    g = _guard()
    g.evaluate("read_buffer", {"conversation_id": "g1"})
    g.evaluate("read_buffer", {"conversation_id": "g1"})
    g.evaluate("read_buffer", {"conversation_id": "g1"})
    assert g.counts["read_buffer"] == 0     # only record() counts
