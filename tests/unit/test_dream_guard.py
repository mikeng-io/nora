"""`DreamGuard`: R-7 default-deny and a call ceiling for a loop with no terminal verb.

Two of these tests are STRUCTURAL rather than behavioural, and deliberately so.
The property that matters most about this guard is a NEGATIVE one — it never
ends a turn — and a negative cannot be observed by driving it: a guard that
simply happened not to cancel today would pass any behavioural probe while
still holding the power to. So the absence is asserted where it actually lives:
in what `register_hooks` subscribes to, and in the object's own attribute set.
"""

from __future__ import annotations

import inspect
import json

from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
)

from nora.agent.dream_guard import DreamGuard
from nora.obs.metrics import Metrics

#: One tool with room for a batch, one with a single call — enough to tell a
#: ceiling from a default-deny without a second fixture.
BUDGETS = {"place_info": 3, "now": 1}


def _guard(
    metrics: Metrics | None = None, budgets: dict[str, int] | None = None,
    param_names: dict[str, frozenset[str]] | None = None,
) -> DreamGuard:
    return DreamGuard(
        adaptor="room_language",
        budgets=BUDGETS if budgets is None else budgets,
        metrics=metrics or Metrics(),
        param_names=param_names,
    )


class _FakeRegistry:
    """Records what a `HookProvider` subscribed to, and nothing else.

    `HookRegistry.add_callback` is the whole surface `register_hooks` touches,
    so a double that captures the event types IS the observation — no strands
    loop needed to see which powers a guard took.
    """

    def __init__(self) -> None:
        self.registered: list[type] = []

    def add_callback(self, event_type: type, callback: object) -> None:
        self.registered.append(event_type)


class _BeforeToolCall:
    def __init__(self, name: str, tool_input: dict | None = None) -> None:
        self.tool_use = {"name": name, "input": tool_input or {}, "toolUseId": "t1"}
        self.cancel_tool: str | None = None


class _AfterToolCall:
    """The shape Strands really passes.

    `exception` is None for a call that RAN and for one `_before_tool`
    cancelled alike — `cancel_tool` produces an error `ToolResult`, not a
    raised exception (`strands.tools.executors._executor`, verified against
    1.50.2; `tests/test_strands_api_contract.py` pins it against the real SDK).
    `cancel_message` is the only field that tells them apart, which is why this
    double carries all three and defaults to "the call ran".
    """

    def __init__(self, name: str, *, cancel_message: str | None = None,
                 exception: Exception | None = None) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": "t1"}
        self.exception = exception
        self.cancel_message = cancel_message
        self.result = (
            {"toolUseId": "t1", "status": "error", "content": [{"text": cancel_message}]}
            if cancel_message is not None
            else {"toolUseId": "t1", "status": "success", "content": [{"text": "True"}]}
        )


# ---- R-7: default-deny, and the ceiling ------------------------------------


def test_a_tool_outside_the_budgets_is_vetoed():
    """R-7's default-deny half — the one that stops the model inventing a tool.

    The adaptor grants a tool subset; anything else was never granted, and in a
    background loop there is no human to notice the difference between a tool
    that failed and a tool that never existed.
    """
    m = Metrics()
    message = _guard(m).evaluate("write_fact", {})
    assert message is not None
    assert "write_fact" in message
    assert "does not exist" in message
    assert m.get("nora_dream_guard_vetoes_total", rule="R-7") == 1


def test_the_unknown_tool_message_names_the_tools_that_do_exist():
    message = _guard().evaluate("write_fact", {})
    assert "now or place_info" in message


def test_a_tool_at_its_ceiling_is_vetoed():
    g = _guard()
    for _ in range(BUDGETS["place_info"]):
        assert g.evaluate("place_info", {"name": "x"}) is None
        g._ledger.record("place_info")
    message = g.evaluate("place_info", {"name": "x"})
    assert message is not None
    assert "already called place_info" in message
    assert f"{BUDGETS['place_info']} time(s)" in message


def test_a_tool_below_its_ceiling_is_admitted():
    """The control for the test above: same guard, same tool, one fewer call."""
    g = _guard()
    for _ in range(BUDGETS["place_info"] - 1):
        g._ledger.record("place_info")
    assert g.evaluate("place_info", {"name": "x"}) is None


# ---- DEC-33: budget is spent by a call that RAN ----------------------------


def test_a_vetoed_call_consumes_no_budget():
    """DEC-33 through the hooks the SDK actually calls, in the arrangement
    where getting it wrong actually costs something.

    Strands fires `AfterToolCallEvent` for a call `_before_tool` VETOED as well,
    with `exception=None` — so a guard that recorded on `exception is None`
    alone charges for a call that never ran. One admitted `place_info` against
    a budget of 1, and a second refused by the ceiling the first is holding;
    the admitted one then FAILS. Both calls are over, neither ran, and the
    model's retry must therefore still be allowed. A guard that charged for the
    refused one has silently spent the budget on its own correction.
    """
    g = _guard(budgets={"place_info": 1})

    admitted = _BeforeToolCall("place_info", {"name": "x"})
    g._before_tool(admitted)
    assert admitted.cancel_tool is None
    refused = _BeforeToolCall("place_info", {"name": "y"})
    g._before_tool(refused)
    assert refused.cancel_tool is not None, "the first call holds the ceiling"

    g._after_tool(_AfterToolCall("place_info", exception=RuntimeError("boom")))
    g._after_tool(_AfterToolCall("place_info", cancel_message=refused.cancel_tool))

    assert g.evaluate("place_info", {"name": "x"}) is None, (
        "a vetoed call must leave the budget intact"
    )


def test_a_call_that_raised_consumes_no_budget():
    """The other never-ran path. `now` has exactly one call: if a failed
    attempt spent it, the model's retry would be refused with «you already
    called it», which is the correction being blocked rather than the mistake.
    """
    g = _guard()
    event = _BeforeToolCall("now")
    g._before_tool(event)
    g._after_tool(_AfterToolCall("now", exception=RuntimeError("boom")))
    assert g.evaluate("now", {}) is None, "a failed call must leave the budget intact"


def test_a_call_that_ran_does_consume_its_budget():
    """The control for both tests above — otherwise they would pass against a
    guard that simply never charges anything."""
    g = _guard()
    event = _BeforeToolCall("now")
    g._before_tool(event)
    g._after_tool(_AfterToolCall("now"))
    assert g.evaluate("now", {}) is not None


# ---- the reason the ledger exists: a batch measured against itself ---------


def test_a_parallel_batch_is_measured_against_itself():
    """The ceiling must hold WITHIN one model turn, not only between turns.

    Every sequential test above loops evaluate -> record -> evaluate. A model
    emitting its tool calls as a PARALLEL BATCH produces a different shape: the
    guard is asked about every call before any of them completes, so with the
    count only advancing in `AfterToolCallEvent` all of them see the pre-batch
    number and all pass. That is the failure `nora.agent.call_ledger` exists to prevent.

    PROBE: this is why `evaluate` asks `at_ceiling` (finished PLUS in-flight)
    rather than reading completed counts alone. Two `reserve`s before either
    `record` is the minimum arrangement that can fail; one call at a time
    cannot.
    """
    g = _guard(budgets={"place_info": 2})

    admitted = 0
    for _ in range(6):                       # one batch, nothing completes yet
        event = _BeforeToolCall("place_info", {"name": "x"})
        g._before_tool(event)                # admits AND reserves, or cancels
        if event.cancel_tool is None:
            admitted += 1

    assert admitted == 2, f"a parallel batch admitted {admitted} calls against a budget of 2"


def test_the_second_of_two_reservations_sees_the_ceiling():
    """The same property at its minimum size, without the hook layer: two
    `reserve`s and no `record` at all.

    The message must name the number it is actually counting — spent, not
    completed. «You already called place_info 0 time(s)» while refusing it is a
    sentence that contradicts itself, and a model reads that as a reason to
    retry rather than as a ceiling.
    """
    g = _guard(budgets={"place_info": 1})
    assert g.evaluate("place_info", {"name": "x"}) is None
    g._ledger.reserve("place_info")
    message = g.evaluate("place_info", {"name": "x"})
    assert message is not None
    assert "1 time(s)" in message


def test_a_parallel_batch_larger_than_the_pass_ceiling_admits_only_the_ceiling():
    """The pass-wide ceiling (`max_tool_calls`) must hold WITHIN one parallel
    batch, not only between model turns.

    Five calls, spread over two tools, with distinct `toolUseId`s, are all
    admitted by `_before_tool` before any `_after_tool` fires (Strands runs a
    batch's before-hooks ahead of its completions). With a pass budget of 2 only
    the first two may be admitted; the other three must be cancelled. The pass
    counter is advanced only in `_after_tool`, so without a reservation in
    `_before_tool` every call sees the pre-batch count and all five run.
    """
    g = DreamGuard(
        adaptor="room_language", budgets=BUDGETS, metrics=Metrics(), pass_budget=2,
    )
    calls = [("place_info", "b1"), ("now", "b2"), ("place_info", "b3"),
             ("place_info", "b4"), ("now", "b5")]
    events = []
    for name, ref in calls:
        event = _BeforeToolCall(name)
        event.tool_use["toolUseId"] = ref
        g._before_tool(event)
        events.append(event)

    admitted = [e.tool_use["toolUseId"] for e in events if e.cancel_tool is None]
    assert admitted == ["b1", "b2"], (
        f"a parallel batch admitted {admitted} against a pass ceiling of 2"
    )


# ---- the metric --------------------------------------------------------


def test_vetoes_are_counted_with_the_adaptor_label():
    """`nora_dream_guard_vetoes_total{adaptor, rule}`. The adaptor label is the
    point: a veto means an objective asked for a tool its own subset never
    granted, and an unlabelled series cannot say which objective."""
    m = Metrics()
    DreamGuard(adaptor="room_language", budgets=BUDGETS, metrics=m).evaluate("rm_rf", {})
    DreamGuard(adaptor="peer_facts", budgets=BUDGETS, metrics=m).evaluate("rm_rf", {})
    DreamGuard(adaptor="peer_facts", budgets=BUDGETS, metrics=m).evaluate("rm_rf", {})
    assert m.get("nora_dream_guard_vetoes_total", adaptor="room_language") == 1
    assert m.get("nora_dream_guard_vetoes_total", adaptor="peer_facts", rule="R-7") == 2


def test_an_admitted_call_is_not_a_veto():
    m = Metrics()
    _guard(m).evaluate("now", {})
    assert m.get("nora_dream_guard_vetoes_total", adaptor="room_language") == 0


# ---- the critical property: this guard cannot end a turn -------------------


def test_the_guard_registers_no_before_model_callback():
    """THE STRUCTURAL ASSERTION. `BeforeModelCallEvent.cancel` is how
    `ReplyGuard` ends a turn; a guard that never subscribes to that event
    cannot end one, whatever its code says.

    Asserted over what `register_hooks` actually subscribed to — not by reading
    the source, and not by driving a loop and observing that nothing was
    cancelled, which a guard that merely chose not to cancel today would also
    survive.
    """
    registry = _FakeRegistry()
    _guard().register_hooks(registry)
    assert BeforeToolCallEvent in registry.registered
    assert AfterToolCallEvent in registry.registered
    assert BeforeModelCallEvent not in registry.registered
    assert AfterModelCallEvent not in registry.registered
    assert len(registry.registered) == 2, "no other power was taken either"


def test_the_guard_has_no_terminal_verb_and_nothing_that_could_end_a_turn():
    """The same negative at the object level: the machinery is ABSENT, not
    merely unused.

    `terminal` has no default on `ReplyGuard` on purpose — a guard that ends no
    turn under `tool_choice="required"` is the runaway DEC-21 fixed — and that
    argument does not transfer to a loop that ends when the model stops calling
    tools. The way to be sure this guard is not a weakened `ReplyGuard` is that
    it cannot be handed the parts.

    `result_caps` is on the
    signature deliberately: it shapes a tool RESULT, not a turn — it cannot
    cancel, relax, or end anything, so the negative below stays intact.

    `memory_tools`/`memory_budget` (R-91) join it on the same
    terms, and the list is spelled out rather than loosened so that the next
    parameter has to be argued for here too. A ceiling on how many MEMORY
    reads one pass may make refuses a call and rewrites its result; it holds
    no terminal verb, subscribes to no model-call hook, and a pass whose
    ration is spent still runs until the model stops asking for tools —
    `tests/unit/test_dream_memory_budget.py` asserts exactly that, because «a
    budget that ends the turn» is the failure mode this negative is about.

    `pass_budget` is the same ceiling over
    every verb rather than the memory cluster, argued on the same terms: it
    refuses a call and rewrites its result, and the pass runs on until the
    model stops asking (`test_the_configured_pass_ceiling_binds_through_the_
    real_loop` finishes `ok` past the cap).

    `per_tool_ceilings` (DEC-247) is the opposite direction and is argued the
    same way: it only SWITCHES OFF one veto (a per-tool count), for the
    Cognition session alone. It adds no terminal verb and no model-call hook
    and ends nothing, so a guard with it off is a guard with one rule fewer,
    not a `ReplyGuard` with a turn-ending power
    (`tests/unit/test_cognition_no_call_ceilings.py`).
    """
    params = inspect.signature(DreamGuard.__init__).parameters
    assert set(params) == {
        "self", "adaptor", "budgets", "metrics", "result_caps",
        "memory_tools", "memory_budget", "param_names", "pass_budget", "per_tool_ceilings",
    }
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY
        for name, p in params.items() if name != "self"
    )

    names = set(dir(_guard()))
    for absent in (
        "terminal", "_terminal", "_terminal_budget_spent",   # terminality
        "_spoke", "_turn_is_over",                           # the turn-ending latch
        "deliverer", "_deliverer",                           # nothing is delivered
        "_before_model", "_after_model",                     # the model-call hooks
        "_forced_first_tool", "_model_calls",                # the tool_choice pin
        "cancelled_model_call",                              # DEC-93's recovery
    ):
        assert absent not in names, f"DreamGuard must not have {absent!r}"


def test_the_guard_satisfies_the_hook_provider_protocol():
    assert isinstance(_guard(), HookProvider)


def test_a_hook_failure_never_escapes_the_loop():
    """A guard bug must not kill an unattended run — worst case it fails open,
    logged. Same posture as `ReplyGuard`, and it matters more here: there is no
    human waiting on a reply to notice."""
    g = _guard()

    class _Malformed:
        tool_use = None
        cancel_tool = None

    event = _Malformed()
    g._before_tool(event)                    # must not raise
    g._after_tool(_Malformed())              # must not raise
    assert event.cancel_tool is None


# ---- result caps ----------------


def _capped_guard(cap: int) -> DreamGuard:
    return DreamGuard(
        adaptor="fact-adjudication", budgets=BUDGETS, metrics=Metrics(),
        result_caps={"place_info": cap},
    )


def test_a_capped_tools_result_is_cut_and_says_so():
    """A result past its cap is cut BEFORE it re-enters the loop, and the
    marker names the cap: a truncated result must read as truncated, not as
    the whole answer."""
    g = _capped_guard(10)
    event = _AfterToolCall("place_info")
    event.result["content"] = [{"text": "x" * 50}]

    g._after_tool(event)

    text = event.result["content"][0]["text"]
    assert text.startswith("x" * 10)
    assert "x" * 11 not in text
    assert "truncated" in text and "10" in text


def test_a_result_under_the_cap_passes_through_untouched():
    g = _capped_guard(100)
    event = _AfterToolCall("place_info")

    g._after_tool(event)

    assert event.result["content"] == [{"text": "True"}]


def test_an_uncapped_tool_is_never_touched():
    """Caps are per tool: a tool absent from the mapping keeps its result
    verbatim, however large."""
    g = _capped_guard(1)
    event = _AfterToolCall("now")

    g._after_tool(event)

    assert event.result["content"] == [{"text": "True"}]


def test_a_guard_with_no_caps_behaves_exactly_as_before():
    event = _AfterToolCall("place_info")
    event.result["content"] = [{"text": "x" * 100_000}]

    _guard()._after_tool(event)

    assert event.result["content"] == [{"text": "x" * 100_000}]


# ---- R-99: an argument the tool does not declare --------------------
#
# The Dreamer runs native tools built by the identical `build_tool_specs` the tag path builds
# them from, so `media_fetch(file=...)`'s silent drop (Strands' generated pydantic model
# ignores an undeclared key) is exactly as reachable from a background pass as from a tag.
# `unknown_arguments_veto` (`nora.agent.guards`) is the ONE function both `ReplyGuard` and
# `DreamGuard` call, so these tests are the Dreamer-side half of the same property
# `tests/unit/test_tool_unknown_arguments.py` proves for the tag path.


def test_an_unknown_argument_is_refused_and_names_what_the_tool_accepts():
    g = _guard(param_names={"place_info": frozenset({"name"})})
    message = g.evaluate("place_info", {"bogus": "x"})
    assert message is not None
    payload = json.loads(message)
    assert payload["status"] == "bad_arguments"
    assert "bogus" in payload["note"]
    assert "name" in payload["note"]


def test_a_well_formed_call_is_not_vetoed_by_r99():
    g = _guard(param_names={"place_info": frozenset({"name"})})
    assert g.evaluate("place_info", {"name": "Yoho Mall"}) is None


def test_conversation_id_is_exempted_here_too():
    """The same DEC-24 tolerance `ReplyGuard`'s own R-3 established, proven
    here because it lives in the SHARED function rather than in either guard
    — a Dreamer pass holds no per-conversation scope to invent one about, but
    the exemption must still hold rather than silently narrowing under a
    stricter rule of the same name."""
    g = _guard(param_names={"place_info": frozenset({"name"})})
    assert g.evaluate(
        "place_info", {"name": "Yoho Mall", "conversation_id": "g2"},
    ) is None


def test_a_tool_with_no_signature_data_is_left_alone():
    """`param_names` present but missing THIS tool's entry — a plugin tool, or
    an adaptor's own tool source that cannot introspect one — means "cannot
    verify", never "reject everything"."""
    g = _guard(param_names={"now": frozenset()})
    assert g.evaluate("place_info", {"bogus": "x"}) is None


def test_r99_is_off_when_no_param_names_are_given():
    """`param_names=None`, `_guard()`'s own default, is the shape every other
    test in this file already exercises without ever meaning to try R-99 —
    pinned explicitly so the default cannot quietly start vetoing."""
    assert _guard().evaluate("place_info", {"bogus": "x"}) is None


def test_a_vetoed_unknown_argument_call_never_ran_and_consumes_no_budget():
    """THE WIRING PROOF, through the hooks the SDK actually calls — the same
    shape `test_a_vetoed_call_consumes_no_budget` above takes for the R-7
    ceiling. `place_info`'s budget is 3; if the vetoed call spent it, the
    model's retry with the correct argument would eventually be refused with
    «you already called it», the correction being blocked rather than the
    mistake.
    """
    g = _guard(param_names={"place_info": frozenset({"name"})})

    event = _BeforeToolCall("place_info", {"bogus": "x"})
    g._before_tool(event)
    assert event.cancel_tool is not None
    payload = json.loads(event.cancel_tool)
    assert payload["status"] == "bad_arguments"

    g._after_tool(_AfterToolCall("place_info", cancel_message=event.cancel_tool))

    assert g.evaluate("place_info", {"name": "Yoho Mall"}) is None, (
        "a call R-99 vetoed must leave the budget intact"
    )


def test_r99_vetoes_are_counted_under_their_own_rule():
    m = Metrics()
    _guard(m, param_names={"place_info": frozenset({"name"})}).evaluate(
        "place_info", {"bogus": "x"},
    )
    assert m.get("nora_dream_guard_vetoes_total", adaptor="room_language", rule="R-99") == 1

