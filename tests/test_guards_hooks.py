import types

from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()
#: Read, never written: DEC-10 keeps the number with the tool and DEC-32 allows
#: exactly one copy of it. A literal here is a second copy that silently rots
#: the next time an operator moves the budget (3→6, DEC-95).
_CAP = BUDGETS["send_reply"]


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
    for _ in range(_CAP):                   # spend the cap (DEC-42), whatever it is
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
    """DEC-33: a vetoed call never ran, so it can have consumed nothing — but
    Strands fires this hook for it anyway with `exception=None`, so an
    `exception`-only check would record it. With
    `NATIVE_TOOLS["send_reply"].budget == 1` that would make a `send_reply("")`
    refused by R-4 spend the only reply the tag had: the model's own
    correction would then meet R-7's budget check and be told "you already sent
    your reply", and the group would hear nothing at all.
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
    from strands.hooks import (
        AfterModelCallEvent,
        AfterToolCallEvent,
        BeforeModelCallEvent,
        BeforeToolCallEvent,
    )

    registered = []

    registry = types.SimpleNamespace(
        add_callback=lambda event_type, cb: registered.append(event_type)
    )
    _guard().register_hooks(registry)
    assert BeforeToolCallEvent in registered
    assert AfterToolCallEvent in registered
    assert BeforeModelCallEvent in registered
    # Without the AFTER half the model-call counter has nothing to count
    # completions with, and falls back to counting attempts.
    assert AfterModelCallEvent in registered


def test_the_guard_satisfies_the_hook_provider_protocol():
    from strands.hooks import HookProvider

    assert isinstance(_guard(), HookProvider)


def test_a_hook_failure_never_escapes_to_the_agent_loop():
    """A guard bug must not crash the tag — worst case it fails open, logged."""
    g = _guard()
    event = types.SimpleNamespace(tool_use=None, invocation_state={}, cancel_tool=None)
    g._before_tool(event)     # must not raise
    assert event.cancel_tool is None or isinstance(event.cancel_tool, str)


def test_a_call_that_raised_gives_its_reservation_back():
    """DEC-33's reservation is NOT budget — it is «this call is still running»
    — so it comes back on every path, including the one where the tool raised.

    **An early return on `exception` above `release` would let a raised call
    hold its reservation for the rest of the tag.** `counts` — the thing
    `test_after_tool_does_not_count_a_failed_call` above asserts, and the only
    half any test read — would stay correct throughout. Only `spent` would be
    wrong, and `spent` is what R-7's ceiling reads. The symptom is a model
    refused a call it was entitled to, on exactly the turns that had already
    gone wrong once.

    Asserted through `evaluate`'s veto rather than through the ledger, because
    the veto is where the leak actually costs the model something."""
    g = _guard()
    before = FakeBeforeEvent("load_transcript", {})
    failed = FakeAfterEvent("load_transcript")
    failed.exception = RuntimeError("boom")

    for _ in range(BUDGETS["load_transcript"]):
        g._before_tool(before)
        g._after_tool(failed)

    # Every one of those calls raised, so none of them spent anything and the
    # next is still admitted. With the leak, the ceiling was already reached.
    assert g.counts["load_transcript"] == 0
    assert g.evaluate("load_transcript", {}) is None


def test_a_cancelled_call_also_gives_its_reservation_back():
    """The sibling path, pinned so the release above cannot be undone by
    restoring an early return for one branch."""
    g = _guard()

    for _ in range(BUDGETS["load_transcript"]):
        g._before_tool(FakeBeforeEvent("load_transcript", {}))
        g._after_tool(FakeAfterEvent("load_transcript", cancel_message="no"))

    assert g.evaluate("load_transcript", {}) is None


def test_a_vetoed_call_does_not_release_a_siblings_reservation():
    """The ceiling has to hold WITHIN one parallel batch, and a veto in that
    batch must not reopen it.

    `reserve` runs only on the ADMITTED path, but the after-tool hook fires for
    a VETOED call too. Releasing by NAME would therefore let the vetoed call
    decrement a sibling's reservation:

        A admitted, B admitted     -> in_flight = 2, ceiling closed
        C vetoed (never reserved)
        C's after-hook releases    -> in_flight = 1
        D admitted                 -> a third call runs against a budget of 2

    which is the over-budget parallel batch `call_ledger` exists to prevent,
    needing only one veto in the batch to reopen. Asserted through `evaluate`
    rather than through the ledger, because the veto is where the leak costs
    the model something."""
    budget = BUDGETS["load_transcript"]
    g = _guard()

    # Fill the ceiling with admitted, still-running calls — a parallel batch,
    # which is the condition the reservation exists for.
    for i in range(budget):
        g._before_tool(_before("load_transcript", ref=f"a{i}"))

    assert g.evaluate("load_transcript", {}) is not None, "ceiling should be closed"

    # One more call in the same batch is VETOED, and its after-hook fires.
    vetoed = FakeAfterEvent("load_transcript", cancel_message="over budget")
    vetoed.tool_use["toolUseId"] = "vetoed-1"
    g._after_tool(vetoed)

    assert g.evaluate("load_transcript", {}) is not None, (
        "a vetoed call gave back a reservation it never made, reopening the "
        "ceiling for a sibling in the same batch"
    )


def _before(name: str, *, ref: str) -> FakeBeforeEvent:
    event = FakeBeforeEvent(name, {})
    event.tool_use["toolUseId"] = ref
    return event
