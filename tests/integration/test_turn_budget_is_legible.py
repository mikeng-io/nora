"""R-23: a turn that ran out of budget is never reported as a turn that
chose not to speak.

This is the end-to-end half of the rule. The unit halves are
`tests/unit/test_engagement_deliver.py` (the relabelling itself) and
`tests/unit/test_engagement_invoke.py` (the `stop_reason` counter); this file
asserts the thing an operator actually reads — that the two turns produce
DIFFERENT metrics — through the real pipeline, the real guards and the real
`deliver.check_outcome`.

The bug being pinned: Strands ends a turn that hits one of R-8's caps by
yielding an `EventLoopStopEvent` with `stop_reason="limit_total_tokens"` and
RETURNING. No exception, no log line, no warning anywhere in the SDK.
`LiveAgentRunner.invoke` then discarded the `AgentResult` outright and nothing
in `nora/` read `stop_reason` at all, so `invoke.run` returned `ok=True`, ⚠️
fired, and `nora_silent_failures_total{reason="no_send_call"}` incremented —
byte-identical to the model simply declining to call `send_reply`. The single
most likely cause of a silent turn was indistinguishable from the most benign
one.

Note what the budget-tripping script below does NOT do: it does not tell the
double which `stop_reason` to return. It spends tokens, and `FakeAgentRunner`
checks that spend against the `limits` dict the pipeline really passed, in
Strands' own priority order (`nora.testing.scripted_agent.limit_stop_reason`).
Raise `[llm.limits] total_tokens` past `_OVER_BUDGET` and this scenario stops
tripping on its own, which is the property that makes it a measurement rather
than a mock.
"""

from nora.testing import ScriptedTurn, group_event

# Comfortably past `[llm.limits] total_tokens` (64,000). A real 6-call turn was
# measured at 32,426-36,748 tokens, so this is a turn roughly twice that long.
_OVER_BUDGET = 70_000


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
    return [ScriptedTurn(final_text="silent")]


async def test_a_budget_stop_is_distinguishable_from_a_silent_model(harness_factory):
    """R-23. The named test for the rule.

    Both turns end with no reply, `ok=True`, and a ⚠️. The ONLY thing that
    separates them is the reason label — so this asserts both directions: the
    budget turn must not say `no_send_call`, and the benign turn must not say
    `limit_total_tokens`. Asserting only the first would pass a change that
    relabelled every silent turn.
    """
    over = await harness_factory(script=_spent_the_budget_without_replying())
    await over.handler.on_event(group_event(body="nora.000 ?"))
    await over.drain()

    assert over.sink.sent == []
    assert over.metrics.get(
        "nora_silent_failures_total", reason="limit_total_tokens") == 1
    assert over.metrics.get("nora_silent_failures_total", reason="no_send_call") == 0
    assert over.metrics.get(
        "nora_llm_stop_reason_total", reason="limit_total_tokens") == 1

    quiet = await harness_factory(script=_chose_not_to_reply())
    await quiet.handler.on_event(group_event(body="nora.000 ?"))
    await quiet.drain()

    assert quiet.sink.sent == []
    assert quiet.metrics.get("nora_silent_failures_total", reason="no_send_call") == 1
    assert quiet.metrics.get(
        "nora_silent_failures_total", reason="limit_total_tokens") == 0
    assert quiet.metrics.get("nora_llm_stop_reason_total", reason="end_turn") == 1


async def test_the_group_still_sees_nothing_when_the_budget_runs_out(harness_factory):
    """R-5 is not weakened by R-23: naming the cause in a METRIC must not put
    it in the room. The turn stays silent and the ⚠️ is the only thing the
    group learns."""
    h = await harness_factory(script=_spent_the_budget_without_replying())
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert h.sink.sent == []
    assert h.sink.texts == []


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

    8 cycles x 8,000 tokens lands exactly on both caps (`turns = 8`,
    `total_tokens = 64000`) at the same cycle, which is the only arrangement
    that tests the ORDER rather than one cap or the other.
    """
    h = await harness_factory(script=[
        ScriptedTurn(tool_calls=[("now", {})], total_tokens=8_000) for _ in range(8)
    ])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()

    assert h.metrics.get("nora_silent_failures_total", reason="limit_turns") == 1
    assert h.metrics.get("nora_silent_failures_total", reason="limit_total_tokens") == 0
