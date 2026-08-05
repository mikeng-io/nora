import types

from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics

BUDGETS = ToolRegistry().budgets()


class FakeBeforeEvent:
    def __init__(self, name, tool_input):
        self.tool_use = {"name": name, "input": tool_input}
        self.invocation_state = {"conversation_id": "g1"}
        self.cancel_tool = None


class FakeAfterEvent:
    def __init__(self, name):
        self.tool_use = {"name": name, "input": {}}
        self.exception = None


def _guard(metrics=None):
    return ReplyGuard(conversation_id="g1", budgets=BUDGETS, metrics=metrics or Metrics())


def test_before_tool_allows_a_valid_call():
    g = _guard()
    event = FakeBeforeEvent("send_reply", {"conversation_id": "g1", "text": "hi"})
    g._before_tool(event)
    assert event.cancel_tool is None


def test_before_tool_sets_cancel_tool_with_the_corrective_message():
    g = _guard()
    g.record("send_reply")
    event = FakeBeforeEvent("send_reply", {"conversation_id": "g1", "text": "hi"})
    g._before_tool(event)
    assert "already sent your reply" in event.cancel_tool


def test_after_tool_records_a_successful_call():
    g = _guard()
    g._after_tool(FakeAfterEvent("read_buffer"))
    assert g.counts["read_buffer"] == 1


def test_after_tool_does_not_count_a_failed_call():
    g = _guard()
    event = FakeAfterEvent("read_buffer")
    event.exception = RuntimeError("boom")
    g._after_tool(event)
    assert g.counts["read_buffer"] == 0


def test_register_hooks_subscribes_to_both_events():
    from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

    registered = []

    registry = types.SimpleNamespace(
        add_callback=lambda event_type, cb: registered.append(event_type)
    )
    _guard().register_hooks(registry)
    assert BeforeToolCallEvent in registered
    assert AfterToolCallEvent in registered


def test_the_guard_satisfies_the_hook_provider_protocol():
    from strands.hooks import HookProvider

    assert isinstance(_guard(), HookProvider)


def test_a_hook_failure_never_escapes_to_the_agent_loop():
    """A guard bug must not crash the tag — worst case it fails open, logged."""
    g = _guard()
    event = types.SimpleNamespace(tool_use=None, invocation_state={}, cancel_tool=None)
    g._before_tool(event)     # must not raise
    assert event.cancel_tool is None or isinstance(event.cancel_tool, str)
