from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()


def _guard(metrics=None) -> ReplyGuard:
    return ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                      metrics=metrics or Metrics())


def _send(text="hi", **extra) -> dict:
    """A `send_reply` call as the model emits it.

    `send_reply(text)` is the whole schema since DEC-24, so the well-formed call
    is just `{"text": ...}`. `extra` is how a test spells "the model also sent
    something that is not in the schema" — the only form a scope probe can still
    take, and the one `nora_tool_scope_violations_total` exists to see.
    """
    return {"text": text, **extra}


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


def test_send_to_another_conversation_cannot_escape_scope_and_is_counted():
    """R-3 holds BY CONSTRUCTION, not by veto — and the argument is now
    out-of-schema (DEC-24).

    `send_reply` never read this argument: it calls `deliverer.deliver(text)`,
    and `Deliverer` holds its target bound from construction. Vetoing on it
    protected nothing and made a model that cannot echo a base64 group id
    verbatim structurally unable to reply — on the first real tag three
    `send_reply` calls were cancelled this way and the turn ended
    `no_send_call`. DEC-24 then deleted the parameter, so a model can only
    supply one by inventing it.

    An invented one still reaches this hook (Strands validates the tool input
    AFTER `BeforeToolCallEvent` and its pydantic model ignores undeclared
    properties), so it is still counted and still not vetoed.
    """
    m = Metrics()
    assert _guard(m).evaluate("send_reply", _send(conversation_id="g2")) is None
    assert m.get("nora_tool_scope_violations_total", tool="send_reply") == 1
    assert m.get("nora_guard_vetoes_total", rule="R-3") == 0


def test_empty_reply_is_cancelled_before_the_tool_runs():
    m = Metrics()
    msg = _guard(m).evaluate("send_reply", _send(text="   "))
    assert msg is not None and "empty" in msg.lower()
    assert m.get("nora_guard_vetoes_total", rule="R-4") == 1


def test_tool_call_budget_enforced_for_read_buffer():
    m = Metrics()
    g = _guard(m)
    for _ in range(2):
        assert g.evaluate("read_buffer", {"limit": 40}) is None
        g.record("read_buffer")
    msg = g.evaluate("read_buffer", {"limit": 40})
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
    msg = g.evaluate("read_buffer", {"limit": 40})
    assert "already" in msg


def test_an_unknown_tool_is_cancelled():
    assert _guard().evaluate("rm_rf", {}) is not None


def test_read_buffer_to_another_conversation_is_counted_not_cancelled():
    """Same construction argument as send_reply: `read_buffer` reads the
    closure's bound conversation (Phase 8 Task 1, which closed a real
    cross-group READ leak) and, since DEC-24, does not declare one at all. The
    rows it returns are scoped whatever the model asks for, so the probe is
    counted rather than vetoed."""
    m = Metrics()
    assert _guard(m).evaluate("read_buffer", {"conversation_id": "g2", "limit": 40}) is None
    assert m.get("nora_tool_scope_violations_total", tool="read_buffer") == 1
    assert m.get("nora_guard_vetoes_total", rule="R-3") == 0


def test_the_scope_counter_fires_for_any_tool_not_just_the_two_scoped_ones():
    """No tool declares a conversation any more, so "the model named a
    conversation" is a signal about the MODEL, not about which tool it aimed
    at. Counting only `send_reply`/`read_buffer` would silently miss it on
    `now` or on any tool added later."""
    m = Metrics()
    assert _guard(m).evaluate("read_peer_card", {"sender_id": "+1",
                                                 "conversation_id": "g2"}) is None
    assert m.get("nora_tool_scope_violations_total", tool="read_peer_card") == 1


def test_a_well_formed_call_never_touches_the_scope_counter():
    """The load-bearing half of DEC-24's counter decision (R-17's lesson in the
    other direction). Absence of `conversation_id` is now the NORMAL case: if it
    were treated as a mismatch, this counter would increment on every single
    tool call and be exactly as uninformative as one pinned at zero."""
    m = Metrics()
    g = _guard(m)
    g.evaluate("send_reply", _send())
    g.evaluate("read_buffer", {"limit": 40})
    g.evaluate("now", {})
    for tool in ("send_reply", "read_buffer", "now"):
        assert m.get("nora_tool_scope_violations_total", tool=tool) == 0


def test_counters_are_per_guard_instance():
    """R-2: constructed fresh per tag, so counters reset by construction."""
    a, b = _guard(), _guard()
    a.record("send_reply")
    assert a.evaluate("send_reply", _send()) is not None
    assert b.evaluate("send_reply", _send()) is None


def test_a_missing_conversation_id_no_longer_blocks_the_reply():
    """It used to be a hard veto, then a counted mismatch, and is now simply
    the shape of a correct call — there is no such parameter to omit. A model
    that speaks with `{"text": ...}` alone must be allowed through and must not
    be flagged for it."""
    m = Metrics()
    assert _guard(m).evaluate("send_reply", {"text": "hi"}) is None
    assert m.get("nora_tool_scope_violations_total", tool="send_reply") == 0


def test_evaluate_does_not_mutate_counts():
    g = _guard()
    g.evaluate("read_buffer", {"limit": 40})
    g.evaluate("read_buffer", {"limit": 40})
    g.evaluate("read_buffer", {"limit": 40})
    assert g.counts["read_buffer"] == 0     # only record() counts


# ---- the message cap lives in the budget, not in R-2's latch (DEC-32) ------


class _Deliverer:
    """The two properties `ReplyGuard` reads off a `Deliverer`, and nothing else.

    `attempted` is R-2's latch — the authoritative single-shot mechanism (spec
    S4) — and it is a CONSTRUCTOR ARGUMENT here because these tests need both
    answers from it: `True` is today's deliverer after a send, `False` is what
    a future relaxation of R-2 would make it. A fake that answered the same way
    every time could not express the question this section asks.
    """

    def __init__(self, *, attempted: bool) -> None:
        self.attempted = attempted
        self.delivered = attempted


def _guard_with(*, attempted: bool, send_reply_budget: int, metrics=None) -> ReplyGuard:
    return ReplyGuard(
        conversation_id="g1",
        budgets={**BUDGETS, "send_reply": send_reply_budget},
        terminal=TERMINAL, deliverer=_Deliverer(attempted=attempted),
        metrics=metrics or Metrics(),
    )


def test_relaxing_r_2_alone_does_not_buy_a_second_message():
    """THE TRAP, made visible before step 5 walks into it.

    `evaluate` checks the R-7 per-tool budget BEFORE it checks
    `Deliverer.attempted`, and `NATIVE_TOOL_BUDGETS["send_reply"]` is 1. So a
    change that relaxes R-2 — the latch, the thing every document calls "the
    one-reply rule" — and touches nothing else leaves multi-message SILENTLY
    DEAD: the second `send_reply` is refused by a number nobody edited, and the
    veto it emits says `rule="R-2"`, which is the rule that was just relaxed.

    `design/proposed/16-multi-message-replies.md` §4 caught this shape one line
    lower down, in `Deliverer._attempted`, and missed it here.
    """
    m = Metrics()
    g = _guard_with(attempted=False, send_reply_budget=1, metrics=m)
    g.record("send_reply")
    assert g.evaluate("send_reply", _send("and one more thing")) is not None
    # Reported as R-2 although the BUDGET is what refused it. Asserted, not
    # endorsed: this is why the trap is invisible to an operator reading
    # `nora_guard_vetoes_total`.
    assert m.get("nora_guard_vetoes_total", rule="R-2") == 1


def test_the_budget_is_the_number_that_refused_it():
    """The attribution. Same relaxed latch, same second call, budget of 2 —
    and it is allowed. So the refusal above came from the budget and from
    nothing else; when step 5 lands, THIS is the number that has to move."""
    g = _guard_with(attempted=False, send_reply_budget=2)
    g.record("send_reply")
    assert g.evaluate("send_reply", _send("and one more thing")) is None


def test_with_the_budget_raised_the_latch_is_what_refuses_the_second_message():
    """The other half of the ordering: raise the cap and R-2 becomes reachable.

    Today's deliverer latches on the first attempt, so a budget raised without
    revising `Deliverer` moves the refusal here rather than removing it —
    which is the failure design/16 §4 names, now with a test that fails if
    someone raises the budget and calls multi-message done."""
    m = Metrics()
    g = _guard_with(attempted=True, send_reply_budget=2, metrics=m)
    g.record("send_reply")
    assert g.evaluate("send_reply", _send("and one more thing")) is not None
    assert m.get("nora_guard_vetoes_total", rule="R-2") == 1
