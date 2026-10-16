"""R-23: a turn that ran out of budget is never reported as a turn that
chose not to speak.

This is the end-to-end half of the rule. The unit halves are
`tests/unit/test_engagement_deliver.py` (the relabelling itself) and
`tests/unit/test_engagement_invoke.py` (the `stop_reason` counter); this file
asserts the thing an operator actually reads — that the two turns produce
DIFFERENT metrics — through the real pipeline, the real guards and the real
`deliver.check_outcome`.

What is pinned: Strands ends a turn that hits one of R-8's caps by yielding an
`EventLoopStopEvent` with `stop_reason="limit_total_tokens"` and RETURNING. No
exception, no log line, no warning anywhere in the SDK. Unless `stop_reason` is
read, such a turn looks byte-identical to the model simply declining to call
`send_reply` (`ok=True`, ⚠️, `nora_silent_failures_total{reason="no_send_call"}`),
so the most likely cause of a silent turn would be indistinguishable from the
most benign one.

Note what the budget-tripping script below does NOT do: it does not tell the
double which `stop_reason` to return. It spends tokens, and `FakeAgentRunner`
checks that spend against the `limits` dict the pipeline really passed, in
Strands' own priority order (`nora.testing.scripted_agent.limit_stop_reason`).
Raise `[llm.limits] total_tokens` past `_OVER_BUDGET` and this scenario stops
tripping on its own, which is the property that makes it a measurement rather
than a mock.
"""

from nora.engagement.pipeline import _EXHAUSTED_FALLBACK
from nora.testing import ScriptedTurn, group_event

# Comfortably past `[llm.limits] total_tokens` (1,024,000 in config.example.toml).
#
# THIS CONSTANT MUST BE RAISED WITH THE CAP. The docstring above says so as a
# property of the design: if the cap rises past this value the scenario stops
# tripping and the tests that use it go green while measuring nothing.
_OVER_BUDGET = 1_200_000

# `[llm.limits] total_tokens` // `turns` — the spend that lands a 20-cycle
# script on BOTH caps at the same cycle. See the ORDER test below.
_PER_CYCLE = 1_024_000 // 20


def _spent_the_budget_without_replying():
    """A cycle that burns the whole token budget on tool calls and never
    reaches `send_reply`.

    It has to CALL something: Strands checks the caps at the top of a cycle,
    and the loop only reaches another cycle when the previous one ended in
    `tool_use`, so a one-shot answer can never trip a limit. That is also the
    real shape of this failure — the turn ran out of budget while working, not
    while sitting still. The second turn is never reached, which is the SDK's
    behaviour too.
    """
    return [
        ScriptedTurn(tool_calls=[("now", {})], total_tokens=_OVER_BUDGET),
        ScriptedTurn(tool_calls=[("send_reply", {"text": "never reached"})],
                     final_text="never reached"),
    ]


def _chose_not_to_reply():
    """The benign turn: the model finished, cheaply, and said nothing."""
    return [ScriptedTurn(final_text="")]


async def test_a_budget_stop_is_distinguishable_from_a_silent_model(harness_factory):
    """R-23. The named test for the rule, under the never-silent fallback.

    The two turns are still DISTINGUISHABLE, which is the whole rule: a budget
    cut-off is a turn that ran out mid-work, and it RECOVERS with the fallback
    line (`nora_loop_exhausted_fallbacks_total{reason=limit_total_tokens}`), NOT
    counted a silent failure; a model that merely chose not to speak stays silent
    and IS counted `no_send_call`. Both directions asserted — a change that
    relabelled or dropped either would fail here.
    """
    over = await harness_factory(script=_spent_the_budget_without_replying())
    await over.handler.on_event(group_event(body="nora.000 ?"))
    await over.drain()

    assert len(over.sink.sent) == 1        # recovered with the fallback, not silent
    assert over.metrics.get(
        "nora_loop_exhausted_fallbacks_total", reason="limit_total_tokens") == 1
    # recovered → NOT a silent failure of any reason
    assert over.metrics.get(
        "nora_silent_failures_total", reason="limit_total_tokens") == 0
    assert over.metrics.get("nora_silent_failures_total", reason="no_send_call") == 0
    # the stop cap is still counted for legibility (unchanged, unconditional)
    assert over.metrics.get(
        "nora_llm_stop_reason_total", reason="limit_total_tokens") == 1

    quiet = await harness_factory(script=_chose_not_to_reply())
    await quiet.handler.on_event(group_event(body="nora.000 ?"))
    await quiet.drain()

    assert quiet.sink.sent == []           # a chosen silence stays silent
    assert quiet.metrics.get("nora_silent_failures_total", reason="no_send_call") == 1
    assert quiet.metrics.get("nora_loop_exhausted_fallbacks_total") == 0
    assert quiet.metrics.get("nora_llm_stop_reason_total", reason="end_turn") == 1


async def test_the_budget_fallback_says_nothing_diagnostic_to_the_group(harness_factory):
    """R-5/R-23 spirit through the fallback: the group sees ONE generic line.
    Naming the cause in a METRIC must never put
    it in the room: the fallback the group reads must be the generic apology, and
    must NOT leak the stop reason (`limit_total_tokens`, `budget`, token counts)."""
    h = await harness_factory(script=_spent_the_budget_without_replying())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert len(h.sink.texts) == 1
    line = h.sink.texts[0]
    for diagnostic in ("limit", "token", "budget", "total_tokens"):
        assert diagnostic not in line.lower()


async def test_a_reply_delivered_before_the_cap_is_still_a_success(harness_factory):
    """A turn can hit the cap AFTER speaking — the loop is over budget, but
    the group got its answer. That is not a silent failure of any kind, and
    relabelling must not turn a delivered reply into one."""
    h = await harness_factory(script=[
        ScriptedTurn(tool_calls=[("send_reply", {"text": "here you go"})],
                     final_text="here you go", total_tokens=_OVER_BUDGET),
    ])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert len(h.sink.sent) == 1
    assert h.metrics.get("nora_silent_failures_total", reason="limit_total_tokens") == 0
    assert h.metrics.get("nora_silent_failures_total", reason="no_send_call") == 0
    # The loop still ran out of budget, and that is still worth counting.
    assert h.metrics.get("nora_llm_stop_reason_total", reason="limit_total_tokens") == 1


async def test_the_cap_that_binds_is_the_one_that_gets_the_blame(harness_factory):
    """Strands checks `turns` BEFORE `total_tokens`, so a turn that crosses
    both on the same cycle must be reported as `limit_turns`. A reason label
    that names the wrong cap is DEC-25's mistake again: it aims the next
    investigation at the token budget when the fix is the turn count.

    20 cycles x 51,200 tokens lands exactly on both caps (`turns = 20`,
    `total_tokens = 1024000`) at the same cycle, which is the only arrangement
    that tests the ORDER rather than one cap or the other.

    LIKE `_OVER_BUDGET`, THE PER-CYCLE SPEND IS DERIVED FROM THE CAP and must
    move with it. A test that crosses only ONE of two caps cannot observe their
    ORDER — it goes green while proving nothing. `_PER_CYCLE` is `total_tokens //
    turns` written out, so a raise of the cap makes the arithmetic visibly wrong
    here instead of quietly inert.
    """
    h = await harness_factory(script=[
        ScriptedTurn(tool_calls=[("now", {})], total_tokens=_PER_CYCLE) for _ in range(20)
    ])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    # The blame rides the fallback's reason label (and the stop-reason
    # counter), both naming `limit_turns`, not `limit_total_tokens` (never a
    # silent failure — recovered). The precedence is what's under test.
    assert h.metrics.get("nora_loop_exhausted_fallbacks_total", reason="limit_turns") == 1
    assert h.metrics.get(
        "nora_loop_exhausted_fallbacks_total", reason="limit_total_tokens") == 0
    assert h.metrics.get("nora_llm_stop_reason_total", reason="limit_turns") == 1


# ── her own words beat the canned line ───────────────────────────────────────


def _spent_the_budget_but_drafted_an_answer():
    """A cut-off turn whose runner came back WITH words.

    In production those words are `LiveAgentRunner`'s drafting call over the
    context the loop had already gathered (`factory.draft_from_exhausted_loop`,
    tested against the real SDK in `tests/test_agent_runner_stop_reason.py`).
    Here the script supplies them, because what this file owns is what the
    PIPELINE does with them.
    """
    return [
        ScriptedTurn(
            tool_calls=[("now", {})], total_tokens=_OVER_BUDGET,
            final_text="Monza, lap 31 of 53. Verstappen leads.",
        ),
        ScriptedTurn(tool_calls=[("send_reply", {"text": "never reached"})],
                     final_text="never reached"),
    ]


async def test_a_cut_off_turn_says_what_it_found_instead_of_apologising(harness_factory):
    """A cut-off turn should use its current information to draft the reply,
    not answer 'Sorry, I cannot handle this' like a bot instead of a friend.

    The room reads the ANSWER. The canned apology must not appear at all — not
    appended, not instead — and the drafted delivery is counted on its own
    series rather than as a fallback.
    """
    h = await harness_factory(script=_spent_the_budget_but_drafted_an_answer())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert h.sink.texts == ["Monza, lap 31 of 53. Verstappen leads."]
    assert _EXHAUSTED_FALLBACK not in h.sink.texts
    assert h.metrics.get(
        "nora_budget_drafts_delivered_total", reason="limit_total_tokens") == 1
    # The fallback series must stay FLAT — the two are read against each other,
    # and a turn counted on both would make that reading meaningless.
    assert h.metrics.get(
        "nora_loop_exhausted_fallbacks_total", reason="limit_total_tokens") == 0
    # Unchanged and unconditional: the operator must still see that the cap
    # bound, whatever got delivered.
    assert h.metrics.get(
        "nora_llm_stop_reason_total", reason="limit_total_tokens") == 1
    assert h.metrics.get("nora_silent_failures_total", reason="no_send_call") == 0


async def test_the_canned_line_survives_for_a_turn_with_genuinely_nothing(harness_factory):
    """The fallback-of-the-fallback, and the reason `_EXHAUSTED_FALLBACK`
    cannot just be deleted. A cap that binds with no words anywhere — the
    drafting call timed out, raised, or returned nothing — still owes the room
    something, because R-23's never-silent guarantee is what this whole branch
    exists for. Improving the line must not cost the guarantee."""
    h = await harness_factory(script=_spent_the_budget_without_replying())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert h.sink.texts == [_EXHAUSTED_FALLBACK]
    assert h.metrics.get(
        "nora_loop_exhausted_fallbacks_total", reason="limit_total_tokens") == 1
    assert h.metrics.get("nora_budget_drafts_delivered_total") == 0


async def test_a_drafted_answer_never_leaks_why_the_turn_was_cut_off(harness_factory):
    """The same screen the canned line already passes, applied to the new path.

    A draft is MODEL-authored text delivered on a failure path, which is
    exactly where "sorry, I ran out of tokens" would appear if the drafting
    prompt ever mentioned budgets. It deliberately does not
    (`factory._DRAFT_INSTRUCTION`), and this is the assertion that keeps it
    that way — naming the cause in a metric must never put it in the room.
    """
    h = await harness_factory(script=_spent_the_budget_but_drafted_an_answer())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert len(h.sink.texts) == 1
    line = h.sink.texts[0].lower()
    for diagnostic in ("limit", "token", "budget", "cut off", "sorry"):
        assert diagnostic not in line
