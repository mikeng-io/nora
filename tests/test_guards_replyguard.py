from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics
from tests.test_guards_tool_call_log import _wrapped as _wrap_result

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()
#: Read, never written — see the same note in `test_guards_hooks.py`.
_CAP = BUDGETS["send_reply"]


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


def test_send_reply_past_the_cap_is_cancelled():
    """The cap is `send_reply`'s budget (DEC-42, multi-message active; doubled
    per operator 2026-08-08). Every call up to the cap is allowed; the next is
    refused — by the R-7 budget, reported as R-2 (both branches share the
    model-facing sentence, DEC-33). Derived from BUDGETS so a budget change
    cannot silently un-test the cap."""
    m = Metrics()
    g = _guard(m)
    for _ in range(BUDGETS["send_reply"]):
        assert g.evaluate("send_reply", _send()) is None
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


def test_tool_call_budget_enforced_for_load_transcript():
    m = Metrics()
    g = _guard(m)
    for _ in range(BUDGETS["load_transcript"]):
        assert g.evaluate("load_transcript", {"limit": 40}) is None
        g.record("load_transcript")
    msg = g.evaluate("load_transcript", {"limit": 40})
    assert msg is not None and "load_transcript" in msg
    assert m.get("nora_guard_vetoes_total", rule="R-7") == 1


def test_tool_call_budget_enforced_for_group_members():
    g = _guard()
    for _ in range(BUDGETS["group_members"]):
        assert g.evaluate("group_members", {}) is None
        g.record("group_members")
    assert g.evaluate("group_members", {}) is not None


def test_the_correction_names_what_the_model_already_has():
    g = _guard()
    for _ in range(BUDGETS["load_transcript"]):
        g.record("load_transcript")
    msg = g.evaluate("load_transcript", {"limit": 40})
    assert "already" in msg


def test_an_unknown_tool_is_cancelled():
    assert _guard().evaluate("rm_rf", {}) is not None


def test_load_transcript_to_another_conversation_is_counted_not_cancelled():
    """Same construction argument as send_reply: `load_transcript` reads the
    closure's bound conversation (Phase 8 Task 1, which closed a real
    cross-group READ leak) and, since DEC-24, does not declare one at all. The
    rows it returns are scoped whatever the model asks for, so the probe is
    counted rather than vetoed."""
    m = Metrics()
    assert _guard(m).evaluate("load_transcript", {"conversation_id": "g2", "limit": 40}) is None
    assert m.get("nora_tool_scope_violations_total", tool="load_transcript") == 1
    assert m.get("nora_guard_vetoes_total", rule="R-3") == 0


def test_the_scope_counter_fires_for_any_tool_not_just_the_two_scoped_ones():
    """No tool declares a conversation any more, so "the model named a
    conversation" is a signal about the MODEL, not about which tool it aimed
    at. Counting only `send_reply`/`load_transcript` would silently miss it on
    `now` or on any tool added later."""
    m = Metrics()
    assert _guard(m).evaluate("now", {"conversation_id": "g2"}) is None
    assert m.get("nora_tool_scope_violations_total", tool="now") == 1


def test_a_well_formed_call_never_touches_the_scope_counter():
    """The load-bearing half of DEC-24's counter decision (R-17's lesson in the
    other direction). Absence of `conversation_id` is now the NORMAL case: if it
    were treated as a mismatch, this counter would increment on every single
    tool call and be exactly as uninformative as one pinned at zero."""
    m = Metrics()
    g = _guard(m)
    g.evaluate("send_reply", _send())
    g.evaluate("load_transcript", {"limit": 40})
    g.evaluate("now", {})
    for tool in ("send_reply", "load_transcript", "now"):
        assert m.get("nora_tool_scope_violations_total", tool=tool) == 0


def test_counters_are_per_guard_instance():
    """R-2: constructed fresh per tag, so counters reset by construction."""
    a, b = _guard(), _guard()
    for _ in range(_CAP):                   # spend a's whole cap
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
    g.evaluate("load_transcript", {"limit": 40})
    g.evaluate("load_transcript", {"limit": 40})
    g.evaluate("load_transcript", {"limit": 40})
    assert g.counts["load_transcript"] == 0     # only record() counts


# ---- the message cap lives in the budget, not in R-2's latch (DEC-32) ------


class _Deliverer:
    """The properties `ReplyGuard` reads off a `Deliverer` — with the real
    relationship between them, not three independent booleans.

    `exhausted` is DERIVED from `attempts` against `message_cap`, exactly as
    `nora.agent.guards.Deliverer` derives it, so a fake that was handed 1 of 2
    messages answers `attempted=True, exhausted=False` the way the real object
    does. Spelling the three out independently would let a test assert a state
    the real class cannot be in — and this section's whole subject is which of
    two mechanisms refuses a call, so a fake that could disagree with itself
    would be asserting the test's own arrangement back at it.
    """

    def __init__(self, *, attempts: int, message_cap: int) -> None:
        self.attempts = attempts
        self.delivered = attempts > 0
        self.attempted = attempts > 0
        self.exhausted = attempts >= message_cap


def _guard_with(*, attempts: int, send_reply_budget: int,
                deliverer_cap: int | None = None, metrics=None) -> ReplyGuard:
    """A guard whose deliverer may DISAGREE with its budget.

    `deliverer_cap` defaults to the budget, which is what
    `nora.engagement.stages.deliver.build` guarantees in production by reading
    both from the same mapping. Passing a different one is how this file spells
    the wiring fault that guarantee exists to prevent.
    """
    return ReplyGuard(
        conversation_id="g1",
        budgets={**BUDGETS, "send_reply": send_reply_budget},
        terminal=TERMINAL,
        deliverer=_Deliverer(
            attempts=attempts,
            message_cap=send_reply_budget if deliverer_cap is None else deliverer_cap,
        ),
        metrics=metrics or Metrics(),
    )


def test_relaxing_r_2_alone_does_not_buy_a_second_message():
    """THE TRAP, still visible after DEC-40 walked past it.

    `evaluate` checks the R-7 per-tool budget BEFORE it checks the deliverer,
    and `NATIVE_TOOL_BUDGETS["send_reply"]` is 1. So a change that relaxes R-2
    — the latch, the thing every document calls "the one-reply rule" — and
    touches nothing else leaves multi-message SILENTLY DEAD: the second
    `send_reply` is refused by a number nobody edited, and the veto it emits
    says `rule="R-2"`, which is the rule that was just relaxed.

    The deliverer here is NOT exhausted (its cap is 1 but it has spent
    nothing), so the latch below cannot be what refuses this call.

    `design/proposed/16-multi-message-replies.md` §4 caught this shape one line
    lower down, in `Deliverer._attempted`, and missed it here.
    """
    m = Metrics()
    g = _guard_with(attempts=0, send_reply_budget=1, metrics=m)
    g.record("send_reply")
    assert g.evaluate("send_reply", _send("and one more thing")) is not None
    # Reported as R-2 although the BUDGET is what refused it. Asserted, not
    # endorsed: this is why the trap is invisible to an operator reading
    # `nora_guard_vetoes_total`.
    assert m.get("nora_guard_vetoes_total", rule="R-2") == 1


def test_the_budget_is_the_number_that_refused_it():
    """The attribution. Same untouched latch, same second call, budget of 2 —
    and it is allowed. So the refusal above came from the budget and from
    nothing else, and THIS is the number an operator raises."""
    g = _guard_with(attempts=1, send_reply_budget=2)
    g.record("send_reply")
    assert g.evaluate("send_reply", _send("and one more thing")) is None


def test_a_deliverer_capped_below_the_budget_refuses_what_the_budget_allowed():
    """The other half of the ordering, restated for DEC-40.

    Before DEC-40 this said "today's deliverer latches on the first attempt",
    which was a property of the code. It is now a property of one number, and
    the number can only disagree with the budget if somebody wires it by hand:
    `deliver.build` reads `message_cap` out of the same `budgets` mapping it
    hands the guard. This is what that wiring fault would look like — R-7 lets
    the second message through and R-2 refuses it, so multi-message ships dead
    with the refusal filed under the rule that was relaxed to enable it.
    """
    m = Metrics()
    g = _guard_with(attempts=1, send_reply_budget=2, deliverer_cap=1, metrics=m)
    g.record("send_reply")
    assert g.evaluate("send_reply", _send("and one more thing")) is not None
    assert m.get("nora_guard_vetoes_total", rule="R-2") == 1


def test_the_latch_still_refuses_the_message_after_the_last_one():
    """R-2b at a raised cap: the deliverer is the authoritative bound.

    Reachable in production only when the guard's own count and the deliverer's
    disagree — a `send_reply` that raised, or one the guard cancelled after the
    audit row was written — which is precisely why the backstop is kept.
    """
    g = _guard_with(attempts=2, send_reply_budget=2)
    assert g.evaluate("send_reply", _send("and one more thing")) is not None


# ---- R-45 (DEC-101): nudge make_artifact toward context, not prose ---------
#
# 2026-08-14: the model called place_info four times, drafted a full prose
# answer from the results, then reused that draft wholesale as `make_artifact`'s
# `task` and left `context` empty — the docstring's own "do not re-derive or
# summarise them into prose first" did not hold against a model that had
# already written the words once. This is a NUDGE, not a refusal: the veto
# fires once, and `make_artifact`'s own budget (1) is not spent by it.


def test_make_artifact_is_nudged_once_when_context_is_empty_after_gathering():
    g = _guard()
    g.record("place_info")
    msg = g.evaluate("make_artifact", {"task": "build a report"})
    assert msg is not None
    assert "place_info" in msg


def test_a_route_call_also_triggers_the_nudge():
    g = _guard()
    g.record("route")
    msg = g.evaluate("make_artifact", {"task": "build a report"})
    assert msg is not None


def test_the_context_nudge_does_not_repeat():
    g = _guard()
    g.record("place_info")
    first = g.evaluate("make_artifact", {"task": "build a report"})
    assert first is not None
    second = g.evaluate("make_artifact", {"task": "build a report"})
    assert second is None


def test_make_artifact_with_context_already_populated_is_not_nudged():
    g = _guard()
    g.record("place_info")
    msg = g.evaluate(
        "make_artifact",
        {"task": "build a report", "context": [{"name": "Yoho Mall", "rating": 4.2}]},
    )
    assert msg is None


def test_make_artifact_with_nothing_gathered_is_not_nudged():
    """No place_info/route this turn: an empty context is legitimate, not a
    defect to correct — `make_artifact`'s own docstring already says to
    leave it unset "for a task with nothing gathered"."""
    g = _guard()
    msg = g.evaluate("make_artifact", {"task": "write a haiku"})
    assert msg is None


def test_the_context_nudge_names_r45_in_the_vetoes_metric():
    m = Metrics()
    g = _guard(m)
    g.record("place_info")
    g.evaluate("make_artifact", {"task": "build a report"})
    assert m.get("nora_guard_vetoes_total", rule="R-45") == 1


def test_the_context_nudge_does_not_spend_make_artifacts_own_budget():
    """The nudge is a correction, not a refusal — the model's retry with
    context populated must still be allowed to actually launch. Mirrors
    production: `_after_tool` never `record()`s a cancelled call (DEC-33's
    own mechanism), so this test does not `record("make_artifact")` for the
    nudged call either — the retry below would hit R-7's OWN budget veto
    instead of running clean if the nudge had silently spent it."""
    g = _guard()
    g.record("place_info")
    nudged = g.evaluate("make_artifact", {"task": "build a report"})
    assert nudged is not None
    allowed = g.evaluate(
        "make_artifact",
        {"task": "build a report", "context": [{"name": "Yoho Mall"}]},
    )
    assert allowed is None


# ---- R-45 extended for recall_lookups (tool-recall-log spec §5) ----


def _wrapped_ok_recall():
    return _wrap_result({"status": "ok", "entries": [{"tool_name": "place_info"}]})


def _wrapped_nothing_found_recall():
    return _wrap_result({"status": "nothing_found"})


class _RecallEvent:
    """A mock AfterToolCallEvent for recall_lookups testing."""

    def __init__(self, result):
        self.tool_use = {"name": "recall_lookups", "input": {}}
        self.result = result
        self.cancel_message = None
        self.exception = None


def test_a_successful_recall_lookups_also_triggers_the_nudge():
    g = _guard()
    g._after_tool(_RecallEvent(_wrapped_ok_recall()))
    msg = g.evaluate("make_artifact", {"task": "build a report"})
    assert msg is not None


def test_a_nothing_found_recall_lookups_does_not_trigger_the_nudge():
    g = _guard()
    g._after_tool(_RecallEvent(_wrapped_nothing_found_recall()))
    msg = g.evaluate("make_artifact", {"task": "build a report"})
    assert msg is None
