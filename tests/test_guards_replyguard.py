from pathlib import Path

from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()
#: Read, never written — see the same note in `test_guards_hooks.py`.
_CAP = BUDGETS["send_reply"]


def _guard(metrics=None) -> ReplyGuard:
    return ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                      metrics=metrics or Metrics())


def _send(text="hi", **extra) -> dict:
    """A `send_reply` call as the model emits it.

    `send_reply(text)` is the whole schema (DEC-24), so the well-formed call is just
    `{"text": ...}`. `extra` is how a test spells "the model also sent something that is
    not in the schema" — the only form a scope probe can take, and the one
    `nora_tool_scope_violations_total` exists to see.
    """
    return {"text": text, **extra}


def test_a_first_send_reply_is_allowed():
    assert _guard().evaluate("send_reply", _send()) is None


def test_send_reply_past_the_cap_is_cancelled():
    """The cap is `send_reply`'s budget (DEC-42, multi-message active). Every call up to the
    cap is allowed; the next is refused — by the R-7 budget, reported as R-2 (both
    branches share the model-facing sentence, DEC-33). Derived from BUDGETS so a budget
    change cannot silently un-test the cap."""
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
    """R-3 holds BY CONSTRUCTION, not by veto — and the argument is out-of-schema (DEC-24).

    `send_reply` never reads this argument: it calls `deliverer.deliver(text)`, and
    `Deliverer` holds its target bound from construction. Vetoing on it would protect
    nothing and would make a model that cannot echo a base64 group id verbatim
    structurally unable to reply. The parameter is not declared, so a model can only
    supply one by inventing it.

    An invented one still reaches this hook (Strands validates the tool input AFTER
    `BeforeToolCallEvent` and its pydantic model ignores undeclared properties), so it is
    still counted and still not vetoed.
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
    """Same construction argument as send_reply: `load_transcript` reads the closure's bound
    conversation and, since DEC-24, does not declare one at all. The rows it returns are
    scoped whatever the model asks for, so the probe is counted rather than vetoed."""
    m = Metrics()
    assert _guard(m).evaluate("load_transcript", {"conversation_id": "g2", "limit": 40}) is None
    assert m.get("nora_tool_scope_violations_total", tool="load_transcript") == 1
    assert m.get("nora_guard_vetoes_total", rule="R-3") == 0


def test_the_scope_counter_fires_for_any_tool_not_just_the_two_scoped_ones():
    """No tool declares a conversation, so "the model named a
    conversation" is a signal about the MODEL, not about which tool it aimed
    at. Counting only `send_reply`/`load_transcript` would silently miss it on
    `now` or on any tool added later."""
    m = Metrics()
    assert _guard(m).evaluate("now", {"conversation_id": "g2"}) is None
    assert m.get("nora_tool_scope_violations_total", tool="now") == 1


def test_a_well_formed_call_never_touches_the_scope_counter():
    """The load-bearing half of DEC-24's counter decision (R-17's lesson in the
    other direction). Absence of `conversation_id` is the NORMAL case: if it
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
    """Omitting `conversation_id` is simply the shape of a correct call — there is no such
    parameter to omit. A model that speaks with `{"text": ...}` alone must be allowed
    through and must not be flagged for it."""
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
    """The properties `ReplyGuard` reads off a `Deliverer` — with the real relationship
    between them, not three independent booleans.

    `exhausted` is DERIVED from `attempts` against `message_cap`, exactly as
    `nora.agent.guards.Deliverer` derives it, so a fake that was handed 1 of 2 messages
    answers `attempted=True, exhausted=False` the way the real object does. Spelling the
    three out independently would let a test assert a state the real class cannot be in —
    and this section's whole subject is which of two mechanisms refuses a call, so a fake
    that could disagree with itself would be asserting the test's own arrangement back at
    it.
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
    """THE TRAP: the R-7 budget and the R-2 latch can disagree silently.

    `evaluate` checks the R-7 per-tool budget BEFORE it checks the deliverer, and
    `NATIVE_TOOL_BUDGETS["send_reply"]` is 1. So a change that relaxes R-2 — the latch,
    the thing every document calls "the one-reply rule" — and touches nothing else leaves
    multi-message SILENTLY DEAD: the second `send_reply` is refused by a number nobody
    edited, and the veto it emits says `rule="R-2"`, which is the rule that was just
    relaxed.

    The deliverer here is NOT exhausted (its cap is 1 but it has spent nothing), so the
    latch below cannot be what refuses this call.
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
    """The other half of the ordering. The deliverer's cap is a property of one number, and
    the number can only disagree with the budget if somebody wires it by hand:
    `deliver.build` reads `message_cap` out of the same `budgets` mapping it hands the
    guard. This is what that wiring fault would look like — R-7 lets the second message
    through and R-2 refuses it, so multi-message ships dead with the refusal filed under
    the rule that was relaxed to enable it.
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


def test_a_parallel_batch_cannot_exceed_the_budget():
    """R-7's ceiling must hold WITHIN one model turn, not only between turns.

    Every other budget test in this file loops `evaluate` -> `record` -> `evaluate`, which
    is the SEQUENTIAL shape. A model emitting its tool calls as a parallel batch produces
    a different one: the guard is asked about every call in the batch before any of them
    completes, so with `record` firing only in `AfterToolCallEvent` they would all see the
    pre-batch count and all pass. That carries a turn past its token ceiling and ends it
    with an empty message, so the group hears nothing — R-23's shape, and the worst way
    for a budget to fail.

    PROBE: delete the `_in_flight` term from `evaluate`'s ceiling check and this goes red
    while every sequential budget test above stays green.
    """
    g = _guard()

    admitted = 0
    for _ in range(9):                       # one batch, nothing completes yet
        if g.evaluate("place_info", {"name": "x"}) is None:
            admitted += 1
            g.reserve("place_info")

    assert admitted == BUDGETS["place_info"], (
        f"a parallel batch admitted {admitted} calls against a budget of "
        f"{BUDGETS['place_info']}"
    )


def test_an_admitted_call_that_never_completes_gives_its_budget_back():
    """DEC-33's rule, which the reservation must not break: budget is spent by
    a call that RAN, not by one that was admitted. A veto, an exception, or a
    cancelled call releases its hold — otherwise a failed first attempt would
    silently cost the model a retry it is entitled to.
    """
    g = _guard()

    g.evaluate("place_info", {"name": "x"})
    g.reserve("place_info")
    g.release("place_info")                  # it never ran

    admitted = 0
    for _ in range(BUDGETS["place_info"]):
        assert g.evaluate("place_info", {"name": "x"}) is None
        g.reserve("place_info")
        admitted += 1
    assert admitted == BUDGETS["place_info"], "the whole budget must still be there"


def test_the_veto_names_the_number_it_is_counting():
    """A batch's fifth call vetoed with «you already called it 0 time(s)» is a
    sentence that contradicts itself, and a model reads that as a reason to
    retry. The number quoted is finished PLUS in-flight."""
    g = _guard()
    for _ in range(BUDGETS["place_info"]):
        g.evaluate("place_info", {"name": "x"})
        g.reserve("place_info")

    message = g.evaluate("place_info", {"name": "x"})

    assert message is not None
    assert f"{BUDGETS['place_info']} time(s)" in message


def test_a_write_tools_veto_does_not_tell_her_to_answer_from_what_it_did():
    """R-7's generic sentence says «you have that context, answer from what you already
    have». For a tool that WRITES, every earlier call may have failed: if every
    `computer_run` call failed to parse a spec, the veto would tell her to answer, and she
    would tell the room the document was made. The sentence for a write tool says only an
    `ok` call did anything.

    Reverting the `read_only` branch in `ReplyGuard.evaluate` turns this red."""
    g = _guard()
    for _ in range(BUDGETS["computer_run"]):
        g.evaluate("computer_run", {"command": "ls"})
        g.reserve("computer_run")

    message = g.evaluate("computer_run", {"command": "ls"})

    assert message is not None
    assert f"{BUDGETS['computer_run']} time(s)" in message
    assert "have that context" not in message and "from what you already have" not in message
    assert "Only a call that returned ok did anything" in message


def test_the_skills_veto_does_not_claim_she_holds_a_skill_she_never_loaded():
    """R-7's generic sentence — «you already called X N time(s) and have that context, answer
    from what you already have» — is true of every tool on the table whose repeat call
    returns the identical answer. `skills` is not one of them: the argument picks which
    body loads.

    The defect does not depend on the directory being larger than the budget. **A repeat
    call is not vetoed here** — the two `skills("navigation")` calls below both return
    None — so the budget can always be spent without loading everything, whatever the
    counts are. Telling her she has a body she never loaded is telling her to describe
    something she was never given.

    Reverting the `skills` branch in `ReplyGuard.evaluate` turns this red.
    """
    g = _guard()
    for _ in range(BUDGETS["skills"]):
        assert g.evaluate("skills", {"skill_name": "navigation"}) is None
        g.record("skills")

    message = g.evaluate("skills", {"skill_name": "personalization"})

    assert message is not None
    assert f"{BUDGETS['skills']} skill(s)" in message
    # The false half, gone: it must not tell her she holds what she did not load.
    assert "have that context" not in message
    assert "from what you already have" not in message
    # And the true half, said: what did not come back is not in front of her, and
    # the honest move is to say so rather than fill the gap.
    assert "not in front of you" in message
    assert "cannot check" in message
    # "called", not "activated": `spent` is `record`'s count, and `record` fires for any
    # completed call this guard did not veto — including a `skills(<unknown>)` that
    # resolved nothing. A call is what was spent; an activation is not what every one of
    # them bought.
    assert "already called" in message
    assert "activated" not in message


def test_the_skills_budget_can_be_spent_with_a_skill_still_unloaded():
    """The positive control for the test above: it is only measuring something while the
    budget CAN be exhausted with a skill still unloaded.

    That is NOT the same claim as `directory size > budget`: a repeat call is not vetoed,
    so spending the whole budget on ONE skill leaves every other one unloaded at any
    directory size.

    So this asserts the defect is REACHABLE, by the same route the test above takes: spend
    the budget on one skill, and name a different one that ships and was never loaded."""
    skills_dir = Path(__file__).resolve().parents[1] / "nora" / "skills"
    shipped = sorted(p.name for p in skills_dir.iterdir()
                     if (p / "SKILL.md").is_file())
    assert len(shipped) >= 2, (
        "fewer than two skills ship, so no call can name an unloaded one and "
        "the veto's false half cannot occur; re-read nora/agent/guards.py")

    g = _guard()
    for _ in range(BUDGETS["skills"]):
        assert g.evaluate("skills", {"skill_name": shipped[0]}) is None, (
            "a repeat `skills` call is vetoed now; the whole budget can no "
            "longer be spent on one skill and the test above measures nothing")
        g.record("skills")
    assert g.evaluate("skills", {"skill_name": shipped[1]}) is not None, (
        "the budget no longer vetoes a call naming an unloaded skill; the "
        "test above measures nothing — re-read nora/agent/guards.py's skills "
        "branch if this fires")
