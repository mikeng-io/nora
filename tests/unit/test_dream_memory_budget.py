"""R-91 / issue #160: ONE memory ration for the whole Dreamer pass.

The per-tool budgets bound each verb on its own, which is exactly what a pass
spends by asking four different verbs the same question. The live decide turn (deleted
2026-10-03, DEC-277)
this file exists for (Langfuse `340373cd`, 2026-09-10 18:03 HKT) held two notes
whose two handles its own `<people>` block already carried and still made six
held-state reads — `note_read` three times, then `about`, then
`search_memory_workspace`, then `ask_memory`. Every per-tool budget was inside
its ceiling the whole time.

**The headline test runs the REAL Strands loop**, not a hand-driven pair of hook
calls, because the property under test is what the MODEL receives back: a
refusal that arrives as an error `ToolResult` is a correction, and a model
corrects by retrying. `_after_tool` rewriting the SDK's cancellation error into
R-27's `unavailable` is the whole fix, and nothing below the SDK can observe
whether it happened. The guard-level tests underneath it are the arithmetic:
across verbs, within a parallel batch, and never charged for a call that did
not run.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, Sequence
from dataclasses import replace
from typing import Any, ClassVar

from strands.models.model import Model

from nora.agent import factory
from nora.agent.dream_guard import (
    GUARD_RULES,
    MEMORY_BUDGET_REASON,
    TOOL_BUDGET_REASON,
    DreamGuard,
)
from nora.agent.dreamer import (
    MEMORY_TOOLS,
    AdaptorBudget,
    AdaptorGrant,
    DreamerRuntime,
)
from nora.agent.ports import ToolSpec, adapt
from nora.obs.metrics import Metrics
from tests.unit.test_dreamer_runtime import _Adaptor as _RuntimeAdaptor
from tests.unit.test_dreamer_runtime import _Sink


def _REMIND_GRANT() -> AdaptorGrant:
    """An adaptor that may hold `remind` past the read-only ceiling, in a real room."""
    return AdaptorGrant(home_room=True)

#: Two reads per pass — the shipped default, and `d11`'s grading budget.
BUDGET = AdaptorBudget(
    turns=8, total_tokens=100_000, output_tokens=4_000, timeout_seconds=10.0,
    max_memory_calls=2,
)

#: Three memory verbs asked in sequence, the shape the live trace took. The
#: envelope is what the fourth turn says instead of calling anything, so the
#: pass has somewhere to finish.
ENVELOPE = '{"observations": [{"term": "bath tonight, place and hour open"}]}'


# ---------------------------------------------------------------------------
# doubles — the tools, the adaptor, the scripted model
# ---------------------------------------------------------------------------


class _Tools:
    """Real `ToolSpec`s under the real memory NAMES, with callables that only
    write down that they ran.

    The names matter and the bodies do not: `_tool_budgets` reads each built
    tool's per-tool ceiling off `NATIVE_TOOLS`, and the guard reads the pass
    ceiling off the same names — so a double named `about` is measured by
    exactly the code production runs. Building the real `about` would drag a
    `Room`, a roster and a Honcho client in to observe a counter.
    """

    def __init__(self, names: Sequence[str]) -> None:
        self.ran: list[str] = []
        self._names = tuple(names)

    def read_only(self) -> frozenset[str]:
        return frozenset(self._names)

    def tool_param_names(self) -> dict[str, frozenset[str]]:
        """Every fake tool this source can build declares one real parameter
        (`person`, `_spec` below) — the accepted-name table an R-99 check
        against one of these would actually see."""
        return {name: frozenset({"person"}) for name in self._names}

    def _spec(self, name: str) -> Any:
        async def call(person: str = "") -> dict:
            self.ran.append(name)
            return {"status": "ok", "answer": f"{name} says {person or 'nothing'}"}

        call.__name__ = name
        call.__doc__ = f"{name}.\n\nArgs:\n    person: who to ask about.\n"
        return adapt(ToolSpec(
            name=name, description=f"{name}", schema={}, call=call, budget=4,
        ))

    def build(
        self, *, key: str, names: frozenset[str], unit: str | None = None,
        adaptor: str = "", grant: Any = None,
    ) -> list[Any]:
        return [self._spec(n) for n in self._names if n in names]


class _Adaptor(_RuntimeAdaptor):
    """The Dreamer-runtime suite's own adaptor double, keyed like the proactive
    one and rendering a prompt worth reading.

    EXTENDED rather than rewritten: `tests/architecture/
    test_port_fakes_live_in_nora_testing.py` pins one inline `DreamAdaptor`
    stand-in per suite and its list only shrinks, so a second copy here would be
    a new entry on an inventory that exists to be burned down. Only `inputs`
    differs — the envelope parse, the screen and the sink path are the parent's,
    which is what makes them the same code both files measure.
    """

    name = "proactive"
    key_kind = "unit"
    objective = "proactive"
    uses_watermark = False

    def __init__(self, tools: frozenset[str]) -> None:
        super().__init__(sink=_Sink(), tools=tools)

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        return "two notes, two handles, both already in <people>"


class _Instructions:
    class _R:
        text = "decide"
        sha256 = "d" * 64
        versions: ClassVar[dict[str, int]] = {}

    def for_objective(self, name: str) -> Any:
        return self._R()


class _ScriptedModel(Model):
    """One scripted assistant turn per entry: a tool name to call, or None to
    stop and emit `ENVELOPE`.

    `seen` keeps every `messages` list the loop hands back, which is the only
    place a tool RESULT is observable from outside the SDK — and the result is
    the thing this file is about.
    """

    def __init__(
        self, script: Sequence[str | None], *, inputs: Sequence[str] | None = None,
    ) -> None:
        self._script = list(script)
        # Per-turn tool-call input, as the raw JSON string Strands streams
        # (`contentBlockDelta.delta.toolUse.input`). `None` (every caller
        # before R-99) keeps every turn's the same hardcoded `person`, which
        # is all the memory-budget property needs; a caller measuring an
        # ARGUMENT itself (`test_dream_guard_wiring.py`) supplies its own,
        # one string per script entry that calls a tool.
        self._inputs = list(inputs) if inputs is not None else None
        self.turn = 0
        self.seen: list[list[dict]] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None,
                                **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        messages = kwargs.get("messages") or (args[0] if args else [])
        self.seen.append([dict(m) for m in messages])
        call = self._script[self.turn] if self.turn < len(self._script) else None
        self.turn += 1
        yield {"messageStart": {"role": "assistant"}}
        if call is None:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": ENVELOPE}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            call_input = (
                self._inputs[self.turn - 1] if self._inputs is not None
                else '{"person": "熱咖啡"}'
            )
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": call, "toolUseId": f"t{self.turn}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {
                "input": call_input}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }}


class _Models:
    def __init__(self, model: Model) -> None:
        self._model = model

    def create(self) -> Any:
        return self._model


def _runtime(model: Model, tools: _Tools, *, budget: AdaptorBudget = BUDGET) -> tuple[
    DreamerRuntime, Metrics
]:
    metrics = Metrics()
    return DreamerRuntime(
        models=_Models(model),
        instructions=_Instructions(),
        tools=tools,
        budgets={"proactive": budget},
        default_budget=budget,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=metrics,
        model_name="scripted",
        # The production grant (39 D3). Without it a unit-keyed adaptor
        # may hold no room-scoped verb, which is three of the four this file
        # counts — so the ceiling would be measured on a tool set the decide
        # turn does not have.
        grants={"proactive": _REMIND_GRANT()},
        store_timeout_seconds=10.0,
    ), metrics


def _tool_results(model: _ScriptedModel) -> list[dict]:
    """Every `toolResult` block the loop fed back to the model, in order."""
    out: list[dict] = []
    seen: set[str] = set()
    for messages in model.seen:
        for message in messages:
            for block in message.get("content") or ():
                result = block.get("toolResult") if isinstance(block, dict) else None
                if result is None:
                    continue
                ref = str(result.get("toolUseId"))
                if ref in seen:
                    continue
                seen.add(ref)
                out.append(result)
    return out


# ---------------------------------------------------------------------------
# THE HEADLINE: the third memory read is answered, and the pass finishes
# ---------------------------------------------------------------------------


async def test_a_third_memory_read_is_answered_unavailable_and_the_pass_completes():
    """The live trace's shape, through the real loop: three memory verbs in
    sequence against a pass ration of two.

    Four assertions, and each is a separate way the fix could be absent:

    1. the third tool's body NEVER RAN — a ceiling that let the read happen and
       discarded the answer would cost exactly what it was built to save;
    2. what came back is R-27's `unavailable` carrying the reason — not an
       error, not an empty result, and not `nothing_found` (which would assert
       memory holds nothing about a person the guard never asked about);
    3. the `ToolResult` status is `success`, which is what makes it read as an
       ANSWER rather than a failure to correct — a memory verb's own
       `unavailable` reaches the model in exactly this shape;
    4. the pass still finishes `ok` and commits, because a spent ration is a
       narrower pass and not a broken one.
    """
    model = _ScriptedModel(["about", "ask_memory", "search_memory_workspace", None])
    tools = _Tools(["about", "ask_memory", "search_memory_workspace"])
    runtime, _ = _runtime(model, tools)
    adaptor = _Adaptor(frozenset(tools.read_only()))

    outcome = await runtime.run(adaptor=adaptor, key="friends", batch=())

    assert tools.ran == ["about", "ask_memory"], (
        "the third memory read must never reach the store"
    )
    third = _tool_results(model)[2]
    body = json.loads(third["content"][0]["text"])
    assert body["status"] == "unavailable"
    assert MEMORY_BUDGET_REASON in body["note"]
    assert third["status"] == "success", (
        "an error result is a correction, and a model corrects by retrying"
    )
    assert outcome.status == "ok"
    assert outcome.written == 1
    assert adaptor.sink.commits, "the pass still commits what it decided"


async def test_the_ceiling_counts_across_verbs_rather_than_per_verb():
    """The control that names the defect. Every one of these three calls is the
    FIRST call of its own verb, so every per-tool budget is untouched — this is
    the arrangement the old code admitted in full and the live trace made six
    times over.
    """
    model = _ScriptedModel(["about", "search_memory_workspace", "ask_memory", None])
    tools = _Tools(["about", "ask_memory", "search_memory_workspace"])
    runtime, metrics = _runtime(model, tools)

    await runtime.run(adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=())

    assert tools.ran == ["about", "search_memory_workspace"]
    assert metrics.get(
        "nora_dream_guard_vetoes_total", adaptor="proactive", rule="memory-budget",
    ) == 1


async def test_a_pass_inside_its_ration_is_untouched():
    """The positive control. Same loop, same verbs, one fewer call: nothing is
    refused, nothing is rewritten, and a `== 0` on the refusal counter means
    something because this run genuinely RAN (AGENTS.md: pair every
    zero-assertion with a positive control).
    """
    model = _ScriptedModel(["about", "ask_memory", None])
    tools = _Tools(["about", "ask_memory", "search_memory_workspace"])
    runtime, metrics = _runtime(model, tools)

    outcome = await runtime.run(
        adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=(),
    )

    assert tools.ran == ["about", "ask_memory"]
    assert outcome.status == "ok"
    assert metrics.get(
        "nora_dream_guard_vetoes_total", adaptor="proactive", rule="memory-budget",
    ) == 0


async def test_a_non_memory_verb_is_never_charged_to_the_memory_ration():
    """`note_read` is deliberately OUT of `MEMORY_TOOLS` (39 D9 — the index
    sends her to it for a body), so a pass that reads two notes still holds its
    whole memory ration. The live trace made three `note_read` calls; a ceiling
    that swept them in would have refused the one read the objective REQUIRES
    before any reminder.
    """
    model = _ScriptedModel(["note_read", "note_read", "about", "ask_memory", None])
    tools = _Tools(["note_read", "about", "ask_memory"])
    runtime, _ = _runtime(model, tools)

    await runtime.run(adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=())

    assert tools.ran == ["note_read", "note_read", "about", "ask_memory"]


async def test_the_ration_resets_for_the_next_pass():
    """Per PASS, never per adaptor lifetime.

    ONE runtime, ONE model, TWO `run` calls on different units — and the
    single object is the whole test. A ledger built at `DreamerRuntime.__init__`
    or hung off the runtime would leave the second pass with nothing to spend
    and nothing saying so, and a test that built a second runtime for the second
    pass could not tell that apart from a correct one. (It was written that way
    first; it passed against every mutation, which is what a decorative test
    does.)
    """
    tools = _Tools(["about", "ask_memory"])
    model = _ScriptedModel(["about", "ask_memory", None, "about", "ask_memory", None])
    runtime, metrics = _runtime(model, tools)

    await runtime.run(adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=())
    assert tools.ran == ["about", "ask_memory"]

    outcome = await runtime.run(
        adaptor=_Adaptor(frozenset(tools.read_only())), key="g", batch=(),
    )

    assert tools.ran == ["about", "ask_memory", "about", "ask_memory"], (
        "the second pass gets its own ration, on the same runtime"
    )
    assert outcome.status == "ok"
    assert metrics.get(
        "nora_dream_guard_vetoes_total", adaptor="proactive", rule="memory-budget",
    ) == 0


async def test_a_pass_that_keeps_asking_ends_visibly_rather_than_silently():
    """THE PATHOLOGICAL ARM, pinned because it is reachable and because nothing
    else in this file drives a model that does not stop.

    The refusal charges neither ledger — a call that never ran spends no budget
    (DEC-33) — so R-7's per-tool ceiling can never bind on a memory verb once
    the ration is spent, and the only thing left bounding a model that keeps
    asking is `AdaptorBudget.turns`. A pass that spends all of them on refusals
    returns no envelope at all.

    That is the honest cost of answering instead of vetoing, and the assertion
    here is that it is LOUD: the run is `unavailable` (never `nothing_found`, so
    no watermark advances and no quiet day is claimed), `stop_reason` names the
    cap, and the refusal counter carries the count. An operator reading
    `nora_dream_guard_vetoes_total{rule="memory-budget"}` climbing beside
    `nora_dream_stop_reason_total{reason="limit_turns"}` is reading this exact
    shape. Issue #205 is the live measurement of how often it happens.
    """
    turns = 5
    tools = _Tools(["about", "ask_memory"])
    model = _ScriptedModel(["about"] * (turns + 2))
    runtime, metrics = _runtime(
        model, tools,
        budget=AdaptorBudget(
            turns=turns, total_tokens=100_000, output_tokens=4_000,
            timeout_seconds=10.0, max_memory_calls=2,
        ),
    )

    outcome = await runtime.run(
        adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=(),
    )

    assert tools.ran == ["about", "about"], "the ration still bound at two"
    assert outcome.status == "unavailable", (
        "no envelope was ever returned, so nobody read this batch"
    )
    assert outcome.stop_reason == "limit_turns", "the cap is named, not inferred"
    assert metrics.get(
        "nora_dream_guard_vetoes_total", adaptor="proactive", rule="memory-budget",
    ) >= 1
    assert metrics.get(
        "nora_dream_run_total", adaptor="proactive", status="nothing_found",
    ) == 0, "a pass that never decided must not read as a quiet day"


async def test_the_configured_ration_is_what_binds():
    """`[dreamer.budgets.<adaptor>] max_memory_calls` reaches the guard. A
    widened ceiling is the operator's answer to a note involving more people
    than the index can settle, and it is worth a test because the field is
    DEFAULTED at every layer — a wiring gap would read as «the default is
    fine» rather than as a break.
    """
    model = _ScriptedModel(["about", "ask_memory", "search_memory_workspace", None])
    tools = _Tools(["about", "ask_memory", "search_memory_workspace"])
    runtime, _ = _runtime(
        model, tools,
        budget=AdaptorBudget(
            turns=8, total_tokens=100_000, output_tokens=4_000,
            timeout_seconds=10.0, max_memory_calls=3,
        ),
    )

    await runtime.run(adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=())

    assert tools.ran == ["about", "ask_memory", "search_memory_workspace"]


# ---------------------------------------------------------------------------
# the arithmetic, at the guard
# ---------------------------------------------------------------------------


class _Before:
    def __init__(self, name: str, ref: str) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": ref}
        self.cancel_tool: str | None = None


class _After:
    def __init__(
        self, name: str, ref: str, *, cancel_message: str | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.tool_use = {"name": name, "input": {}, "toolUseId": ref}
        self.exception = exception
        self.cancel_message = cancel_message
        self.result: dict = (
            {"toolUseId": ref, "status": "error", "content": [{"text": cancel_message}]}
            if cancel_message is not None
            else {"toolUseId": ref, "status": "success", "content": [{"text": "ok"}]}
        )


def _guard(budget: int | None = 2) -> DreamGuard:
    return DreamGuard(
        adaptor="proactive",
        budgets=dict.fromkeys(MEMORY_TOOLS, 4) | {"note_read": 4},
        metrics=Metrics(),
        memory_tools=MEMORY_TOOLS,
        memory_budget=budget,
    )


def _run(guard: DreamGuard, name: str, ref: str) -> _Before:
    before = _Before(name, ref)
    guard._before_tool(before)
    guard._after_tool(_After(name, ref, cancel_message=before.cancel_tool))
    return before


def test_a_parallel_batch_of_memory_verbs_is_measured_against_itself():
    """`CallLedger`'s own lesson at the pass grain: a model emitting three
    memory verbs in ONE batch is asked about all three before any completes, so
    a check that read only completed calls would admit the lot. This is the
    2026-08-13 nine-`place_info` trace's shape one level up.
    """
    guard = _guard()
    first, second, third = (_Before(n, f"t{i}") for i, n in enumerate(
        ["about", "ask_memory", "search_memory_workspace"]))
    for event in (first, second, third):
        guard._before_tool(event)

    assert first.cancel_tool is None
    assert second.cancel_tool is None
    assert third.cancel_tool is not None
    assert MEMORY_BUDGET_REASON in third.cancel_tool


def test_a_memory_read_that_failed_does_not_consume_the_pass_ration():
    """DEC-33 at the pass grain, and the assertion has to be that BOTH reads
    are still spendable afterwards.

    A failed call leaves two things behind if they are not cleaned up: a charge
    and a RESERVATION, and only the second is subtle. The reservation is what
    holds the ceiling while a parallel batch runs, so a guard that never
    released it would read «one in flight» for the rest of the pass — the
    ration would look intact one read later and be gone by the second, which is
    exactly the shape that survives a test asserting only the first.
    """
    guard = _guard()
    failed = _Before("about", "t1")
    guard._before_tool(failed)
    assert failed.cancel_tool is None
    guard._after_tool(_After("about", "t1", exception=RuntimeError("honcho is down")))

    assert _run(guard, "about", "t2").cancel_tool is None
    assert _run(guard, "ask_memory", "t3").cancel_tool is None, (
        "the outage must not have cost a read"
    )
    assert _run(guard, "lookup_person", "t4").cancel_tool is not None, (
        "and the ration is two, not three"
    )


def test_a_refused_memory_read_leaves_the_per_tool_budget_intact():
    """The two ceilings do not charge each other. `about` was never called, so
    R-7 must still count it at zero — otherwise a widened pass ration would run
    into a per-tool budget the refusals themselves had spent.
    """
    guard = _guard()
    _run(guard, "about", "t1")
    _run(guard, "ask_memory", "t2")
    _run(guard, "search_memory_workspace", "t3")

    assert guard._ledger.spent("search_memory_workspace") == 0
    assert guard._ledger.spent("about") == 1


def test_a_refused_read_is_rewritten_into_an_unavailable_result():
    guard = _guard()
    _run(guard, "about", "t1")
    _run(guard, "ask_memory", "t2")

    before = _Before("lookup_person", "t3")
    guard._before_tool(before)
    after = _After("lookup_person", "t3", cancel_message=before.cancel_tool)
    guard._after_tool(after)

    assert after.result["status"] == "success"
    body = json.loads(after.result["content"][0]["text"])
    assert body == {
        "status": "unavailable",
        "note": json.loads(before.cancel_tool or "{}")["note"],
    }


def test_an_id_less_call_cannot_hand_back_a_memory_reservation():
    """The sibling-release defect at the PASS grain, and it is worse here than
    at the per-tool one.

    `CallLedger.release` only takes its id-keyed path when there IS an id;
    given an empty one it falls through to the NAME-keyed path. The pass
    ledger's name is a single synthetic key shared by every tool in the run, so
    an id-less after-hook for ANY verb — `note_read`, `now`, `weather` — would
    decrement a memory read's reservation and reopen the ceiling by one. At the
    per-tool grain the name at least told two calls apart; here it cannot.

    Strands always supplies a `toolUseId`, so this is latent — but the guard
    defends against the empty case explicitly, and a defence that is wrong is
    worse than none.
    """
    guard = _guard()
    for ref, name in (("t1", "about"), ("t2", "ask_memory")):
        before = _Before(name, ref)
        guard._before_tool(before)
        assert before.cancel_tool is None
    # Both reads are in flight; the ration is held.
    guard._after_tool(_After("note_read", ""))

    third = _Before("lookup_person", "t3")
    guard._before_tool(third)
    assert third.cancel_tool is not None, (
        "an id-less call to an unrelated verb must not lend the ration back"
    )


def test_an_id_less_refusal_never_answers_another_call():
    """`_budget_refusals[""]` would be one slot for every id-less refusal in a
    batch: the second would take the first's body, and the first would reach
    the model as the SDK's raw ERROR result — the one shape this refusal exists
    not to be. Nothing is stashed without an id, so both get the body on the
    cancel message instead and neither is answered with the other's."""
    guard = _guard()
    _run(guard, "about", "t1")
    _run(guard, "ask_memory", "t2")

    first, second = _Before("about", ""), _Before("ask_memory", "")
    guard._before_tool(first)
    guard._before_tool(second)
    assert MEMORY_BUDGET_REASON in (first.cancel_tool or "")
    assert MEMORY_BUDGET_REASON in (second.cancel_tool or "")
    assert guard._budget_refusals == {}, "nothing is stashed under an empty id"


def test_the_guard_rule_labels_come_from_one_vocabulary():
    """`nora_dream_guard_vetoes_total{rule}` had no vocabulary while it had one
    value. The second is where a typo starts a new series instead of raising,
    so the label set is DERIVED off a `Literal` (AGENTS.md) and all three
    emitters are pinned to it — R-7 for a tool never granted, tool-budget for
    one tool's own per-pass ceiling spent, memory-budget for the pass's memory
    ration spent, and the three are different findings that must not collapse.

    `tool-budget` was carried on `R-7` until 2026-09-17, which is what made
    «is a ceiling binding» unanswerable from this series — see
    `GuardRule`'s own note and `nora/obs/metric_specs.py`.

    **`R-99` is the fourth** (design/social-per-network/30-link-owner-registry.md,
    2026-09-25): an argument the tool's own signature does not declare, from
    the SAME `nora.agent.guards.unknown_arguments_veto` `ReplyGuard` calls —
    see that function's docstring rather than a second copy of the reasoning
    here.
    """
    assert set(GUARD_RULES) == {
        "R-7", "memory-budget", "tool-budget", "R-99", "pass-budget",
    }

    m = Metrics()
    guard = DreamGuard(
        adaptor="proactive", budgets={"about": 1}, metrics=m,
        memory_tools=MEMORY_TOOLS, memory_budget=1,
        param_names={"about": frozenset({"query"})},
        pass_budget=1,
    )
    assert guard.evaluate("write_fact", {}) is not None       # R-7
    assert guard.evaluate("about", {"bogus": "x"}) is not None  # R-99
    _run(guard, "about", "t1")
    assert guard.evaluate("about", {}) is not None            # tool-budget
    assert guard.memory_refusal("about") is not None          # memory-budget
    assert guard.pass_refusal("about") is not None            # pass-budget

    emitted = {
        dict(labels)["rule"]
        for (name, labels) in m.snapshot()
        if name == "nora_dream_guard_vetoes_total"
    }
    assert emitted == GUARD_RULES, "a rule the guard emits is not in the vocabulary"


def test_a_guard_with_no_memory_budget_refuses_nothing():
    """The pre-#160 posture, kept reachable: `memory_budget=None` is no ceiling
    at all. It is what every adaptor holding no memory verb gets, and what a
    deployment gets if this is ever unwired — so it must be a behaviour rather
    than an accident.
    """
    guard = _guard(budget=None)
    for i, name in enumerate(sorted(MEMORY_TOOLS)):
        assert _run(guard, name, f"t{i}").cancel_tool is None


# ---------------------------------------------------------------------------
# the wiring: config -> runtime
# ---------------------------------------------------------------------------


def test_the_configured_memory_ceiling_reaches_the_runtime():
    """`[dreamer.budgets.<adaptor>] max_memory_calls` -> `AdaptorBudget`.

    Walked off `DreamBudgetConfig`'s OWN field list rather than asserting the
    five names by hand, because the failure this guards is a field that exists
    on both sides and is carried by neither construction site — and a
    hand-written list cannot answer «was anything forgotten» about itself.
    `max_memory_calls` is the first field DEFAULTED on both types, so unlike
    every field before it a dropped conversion raises nothing: the runtime just
    gets 2 while the operator reads 5 in their config.
    """
    from nora.app.composition import _adaptor_budget
    from nora.config import DreamBudgetConfig

    configured = DreamBudgetConfig(
        turns=9, total_tokens=11_000, output_tokens=1_200,
        timeout_seconds=13.5, max_memory_calls=5,
        # EVERY field has to be set to something other than its default, or
        # the walk below compares two defaults and passes on a conversion
        # that was never written. `tool_calls` was added 2026-09-17 and is
        # the second defaulted field on both sides, so it inherits exactly
        # `max_memory_calls`' silence.
        tool_calls={"note_read": 8},
        # The third (design 43 §4), silent the same way.
        max_tool_calls=4,
    )
    budget = _adaptor_budget(configured)

    for field in DreamBudgetConfig.model_fields:
        assert getattr(budget, field) == getattr(configured, field), (
            f"{field} is configured and never reaches the runtime"
        )
        assert getattr(configured, field) != getattr(
            DreamBudgetConfig(), field
        ), (
            f"{field} is set to its own default in this fixture, so the "
            f"assertion above would pass even if nothing carried it"
        )


def test_the_shipped_default_is_the_number_d11_grades_at():
    """Two memory reads per pass, the shipped default (#206)."""
    from nora.config import DreamBudgetConfig

    assert DreamBudgetConfig().max_memory_calls == 2
    assert AdaptorBudget(
        turns=1, total_tokens=1, output_tokens=1, timeout_seconds=1.0,
    ).max_memory_calls == 2


# ---------------------------------------------------------------------------
# the set itself
# ---------------------------------------------------------------------------


def test_the_memory_verbs_are_these_five():
    """The verbs the memory ceiling counts: every memory verb of both memory
    surfaces (DEC-316), so the ration binds whichever one is registered."""
    assert set(MEMORY_TOOLS) == {
        "about", "ask_memory", "search_memory_workspace", "lookup_person",
        "memory_search",
    }


def test_note_read_is_not_a_memory_verb():
    """Stated as its own assertion because it is the judgement call in the set,
    and the live trace made three of them: `note_read` reads a note the prompt
    already INDEXED, which is the depth the index deliberately withholds.
    """
    assert "note_read" not in MEMORY_TOOLS


# ---------------------------------------------------------------------------
# design/dreamer-agent-loop/43 §4: the pass-wide tool ceiling
# ---------------------------------------------------------------------------


def test_the_pass_tool_ceiling_binds_over_every_verb_together():
    """Three DIFFERENT verbs, each well under its own per-tool ceiling and none
    of them a memory verb: only a ceiling over the whole pass can refuse the
    third. The refusal is R-27 `unavailable` with the constant reason, and the
    call is not charged."""
    m = Metrics()
    guard = DreamGuard(
        adaptor="note-candidates",
        budgets={"load_transcript": 4, "note_read": 3, "web_search": 4},
        metrics=m, pass_budget=2,
    )
    assert _run(guard, "load_transcript", "t1").cancel_tool is None
    assert _run(guard, "note_read", "t2").cancel_tool is None
    third = _run(guard, "web_search", "t3")
    assert third.cancel_tool is not None
    assert TOOL_BUDGET_REASON in third.cancel_tool
    assert json.loads(third.cancel_tool)["status"] == "unavailable"
    assert guard.evaluate("web_search", {}) is None, (
        "the per-tool ceiling did not bind: the pass ceiling did"
    )


def test_a_guard_with_no_pass_budget_refuses_nothing_on_it():
    guard = DreamGuard(
        adaptor="proactive", budgets={"note_read": 9}, metrics=Metrics(),
    )
    for i in range(6):
        assert _run(guard, "note_read", f"t{i}").cancel_tool is None


async def test_the_configured_pass_ceiling_binds_through_the_real_loop():
    """`AdaptorBudget.max_tool_calls` reaches the guard the runtime builds.
    Three NON-memory verbs, each its own first call, so neither the memory
    ration nor any per-tool ceiling can refuse the third: only the pass cap
    of 2 does, and the pass still finishes."""
    model = _ScriptedModel(["note_read", "now", "web_search", None])
    tools = _Tools(["note_read", "now", "web_search"])
    runtime, metrics = _runtime(
        model, tools, budget=replace(BUDGET, max_tool_calls=2),
    )

    outcome = await runtime.run(
        adaptor=_Adaptor(frozenset(tools.read_only())), key="f", batch=(),
    )

    assert tools.ran == ["note_read", "now"]
    body = json.loads(_tool_results(model)[2]["content"][0]["text"])
    assert TOOL_BUDGET_REASON in body["note"]
    assert outcome.status == "ok"
    assert metrics.get(
        "nora_dream_guard_vetoes_total", adaptor="proactive", rule="pass-budget",
    ) == 1
