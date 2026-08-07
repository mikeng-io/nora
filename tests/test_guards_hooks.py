import types

from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()


class FakeBeforeEvent:
    def __init__(self, name, tool_input):
        self.tool_use = {"name": name, "input": tool_input}
        self.invocation_state = {"conversation_id": "g1"}
        self.cancel_tool = None


class FakeAfterEvent:
    """The shape Strands really passes — including `cancel_message`.

    `exception` is None for a call that RAN and for one `_before_tool`
    cancelled alike: `cancel_tool` produces an error `ToolResult`, not a raised
    exception (`strands.tools.executors._executor`, verified against 1.50.2).
    `cancel_message` is the only field that tells them apart, which is why this
    double carries it and defaults it to "the call ran".
    """

    def __init__(self, name, cancel_message=None):
        self.tool_use = {"name": name, "input": {}}
        self.exception = None
        self.cancel_message = cancel_message


def _guard(metrics=None):
    return ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                      metrics=metrics or Metrics())


def test_before_tool_allows_a_valid_call():
    g = _guard()
    event = FakeBeforeEvent("send_reply", {"text": "hi"})
    g._before_tool(event)
    assert event.cancel_tool is None


def test_before_tool_sets_cancel_tool_with_the_corrective_message():
    g = _guard()
    for _ in range(3):                      # spend the cap of 3 (DEC-42)
        g.record("send_reply")
    event = FakeBeforeEvent("send_reply", {"text": "hi"})
    g._before_tool(event)
    assert "already sent your reply" in event.cancel_tool


def test_before_tool_counts_an_out_of_schema_conversation_and_still_allows_the_call():
    """DEC-24's scope counter, through the hook the SDK actually calls.

    `BeforeToolCallEvent` carries the model's RAW `tool_use["input"]` — Strands
    validates it afterwards, in `DecoratedFunctionTool.stream`. That ordering is
    what keeps `nora_tool_scope_violations_total` reachable now that no tool
    declares a conversation: the invented argument is visible here and gone by
    the time the tool body runs. The call must not be cancelled — vetoing it is
    the `no_send_call` bug DEC-21 fixed.
    """
    m = Metrics()
    g = _guard(m)
    event = FakeBeforeEvent("send_reply", {"conversation_id": "gEVIL", "text": "hi"})
    g._before_tool(event)
    assert event.cancel_tool is None
    assert m.get("nora_tool_scope_violations_total", tool="send_reply") == 1


def test_after_tool_records_a_successful_call():
    g = _guard()
    g._after_tool(FakeAfterEvent("load_transcript"))
    assert g.counts["load_transcript"] == 1


def test_after_tool_does_not_count_a_failed_call():
    g = _guard()
    event = FakeAfterEvent("load_transcript")
    event.exception = RuntimeError("boom")
    g._after_tool(event)
    assert g.counts["load_transcript"] == 0


def test_after_tool_does_not_count_a_call_the_guard_cancelled():
    """DEC-33, and a LIVE bug until it was written.

    A vetoed call never ran, so it can have consumed nothing — but Strands
    fires this hook for it anyway with `exception=None`, so the pre-DEC-33
    `exception`-only check recorded it. With
    `NATIVE_TOOLS["send_reply"].budget == 1` that made a `send_reply("")`
    refused by R-4 spend the only reply the tag had: the model's own
    correction then met R-7's budget check and was told "you already sent your
    reply", and the group heard nothing at all.
    """
    g = _guard()
    g._after_tool(FakeAfterEvent("load_transcript", cancel_message="You already called it."))
    assert g.counts["load_transcript"] == 0


def test_the_budget_a_vetoed_call_leaves_is_the_one_the_correction_uses():
    """The rule stated as the model experiences it: veto, correct, succeed.

    `send_reply`'s budget is 1. The first call is refused by R-4 and the second
    must still be allowed — the assertion is on `evaluate`, i.e. on what the
    model is told, not on the counter that feeds it.
    """
    g = _guard()
    assert g.evaluate("send_reply", {"text": ""}) is not None          # R-4
    g._after_tool(FakeAfterEvent("send_reply", cancel_message="Your reply text is empty."))
    assert g.evaluate("send_reply", {"text": "今晚七點"}) is None


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
