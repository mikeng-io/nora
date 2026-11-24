"""Fixtures and guard-level contracts for the Dreamer tool guards."""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, Sequence
from typing import Any

from strands.models.model import Model

from nora.agent.dream_guard import (
    GUARD_RULES,
    MEMORY_BUDGET_REASON,
    TOOL_BUDGET_REASON,
    DreamGuard,
)
from nora.agent.ports import ToolSpec, adapt
from nora.obs.metrics import Metrics

ENVELOPE = '{"observations": [{"term": "bath tonight, place and hour open"}]}'


class _Tools:
    """Real `ToolSpec`s under the real memory NAMES, with callables that only
    write down that they ran.

    The argument schema and tool names let the tests drive tool calls through Strands.
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
        self, *, key: str, names: frozenset[str],
        adaptor: str = "", grant: Any = None,
    ) -> list[Any]:
        return [self._spec(n) for n in self._names if n in names]


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
        # (`contentBlockDelta.delta.toolUse.input`). `None` keeps every turn's
        # the same hardcoded `person`, which is all the memory-budget property
        # needs; a caller measuring an ARGUMENT itself
        # (`test_dream_guard_wiring.py`) supplies its own, one string per
        # script entry that calls a tool.
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


_MEMORY_TOOLS = frozenset({"lookup_person", "memory_search", "memory_read"})


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
        budgets=dict.fromkeys(_MEMORY_TOOLS, 4) | {"note_read": 4},
        metrics=Metrics(),
        memory_tools=_MEMORY_TOOLS,
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
    a check that read only completed calls would admit the lot.
    """
    guard = _guard()
    first, second, third = (_Before(n, f"t{i}") for i, n in enumerate(
        ["lookup_person", "memory_search", "memory_read"]))
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
    failed = _Before("lookup_person", "t1")
    guard._before_tool(failed)
    assert failed.cancel_tool is None
    guard._after_tool(_After("lookup_person", "t1", exception=RuntimeError("honcho is down")))

    assert _run(guard, "lookup_person", "t2").cancel_tool is None
    assert _run(guard, "memory_search", "t3").cancel_tool is None, (
        "the outage must not have cost a read"
    )
    assert _run(guard, "lookup_person", "t4").cancel_tool is not None, (
        "and the ration is two, not three"
    )


def test_a_refused_memory_read_leaves_the_per_tool_budget_intact():
    """The two ceilings do not charge each other. `lookup_person` was never called, so
    R-7 must still count it at zero — otherwise a widened pass ration would run
    into a per-tool budget the refusals themselves had spent.
    """
    guard = _guard()
    _run(guard, "lookup_person", "t1")
    _run(guard, "memory_search", "t2")
    _run(guard, "memory_read", "t3")

    assert guard._ledger.spent("memory_read") == 0
    assert guard._ledger.spent("lookup_person") == 1


def test_a_refused_read_is_rewritten_into_an_unavailable_result():
    guard = _guard()
    _run(guard, "lookup_person", "t1")
    _run(guard, "memory_search", "t2")

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
    """The sibling-release defect at the PASS grain, worse here than at the
    per-tool one.

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
    for ref, name in (("t1", "lookup_person"), ("t2", "memory_search")):
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
    _run(guard, "lookup_person", "t1")
    _run(guard, "memory_search", "t2")

    first, second = _Before("lookup_person", ""), _Before("memory_search", "")
    guard._before_tool(first)
    guard._before_tool(second)
    assert MEMORY_BUDGET_REASON in (first.cancel_tool or "")
    assert MEMORY_BUDGET_REASON in (second.cancel_tool or "")
    assert guard._budget_refusals == {}, "nothing is stashed under an empty id"


def test_the_guard_rule_labels_come_from_one_vocabulary():
    """`nora_dream_guard_vetoes_total{rule}` has a vocabulary: a typo in a
    label starts a new series instead of raising, so the label set is DERIVED
    off a `Literal` (docs/code-standards.md) and all emitters are pinned to it — R-7 for a
    tool never granted, tool-budget for one tool's own per-pass ceiling spent,
    memory-budget for the pass's memory ration spent. They are different
    findings that must not collapse: `tool-budget` is not carried on `R-7`, or
    «is a ceiling binding» would be unanswerable from this series — see
    `GuardRule`'s own note and `nora/obs/metric_specs.py`.

    **`R-99` is the fourth**: an argument the tool's own signature does not
    declare, from the SAME `nora.agent.guards.unknown_arguments_veto`
    `ReplyGuard` calls — see that function's docstring rather than a second
    copy of the reasoning here.
    """
    assert set(GUARD_RULES) == {
        "R-7", "memory-budget", "tool-budget", "R-99", "pass-budget",
    }

    m = Metrics()
    guard = DreamGuard(
        adaptor="proactive", budgets={"lookup_person": 1}, metrics=m,
        memory_tools=_MEMORY_TOOLS, memory_budget=1,
        param_names={"lookup_person": frozenset({"query"})},
        pass_budget=1,
    )
    assert guard.evaluate("write_fact", {}) is not None       # R-7
    assert guard.evaluate("lookup_person", {"bogus": "x"}) is not None  # R-99
    _run(guard, "lookup_person", "t1")
    assert guard.evaluate("lookup_person", {}) is not None            # tool-budget
    assert guard.memory_refusal("lookup_person") is not None          # memory-budget
    assert guard.pass_refusal("lookup_person") is not None            # pass-budget

    emitted = {
        dict(labels)["rule"]
        for (name, labels) in m.snapshot()
        if name == "nora_dream_guard_vetoes_total"
    }
    assert emitted == GUARD_RULES, "a rule the guard emits is not in the vocabulary"


def test_a_guard_with_no_memory_budget_refuses_nothing():
    """The pre-ration posture, kept reachable: `memory_budget=None` is no ceiling
    at all. It is what every adaptor holding no memory verb gets, and what a
    deployment gets if this is ever unwired — so it must be a behaviour rather
    than an accident.
    """
    guard = _guard(budget=None)
    for i, name in enumerate(sorted(_MEMORY_TOOLS)):
        assert _run(guard, name, f"t{i}").cancel_tool is None


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
