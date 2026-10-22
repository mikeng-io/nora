"""`DreamGuard` — R-7 default-deny for an agent loop with NO terminal verb.

`ReplyGuard` is the tag path's guard, and every one of its extra powers is
about a turn that must end in `send_reply`: it holds a `Deliverer`, it relaxes
`tool_choice` to `auto` once a terminal verb has run, it cancels the model call
once the terminal budget is spent, and its `terminal` parameter has no default
because a guard that ends no turn under `tool_choice="required"` is the ~25s
runaway DEC-21 fixed. The Dreamer has none of that: nothing is delivered, no
verb is terminal, and the loop ends when the model stops calling tools.

So this is a SEPARATE guard rather than a weakened `ReplyGuard`. What it keeps:

* **R-7 default-deny** — a tool absent from `budgets` is vetoed. This is the
  half that stops the model inventing a tool name, and in a background loop
  with no human watching it is also the tell that an objective and its granted
  tool subset disagree about what the job needs.
* **the per-tool call ceiling**, through the same `CallLedger` `ReplyGuard`
  holds — reused as code, so a parallel tool batch is measured against itself
  and not merely between model turns (`nora.agent.call_ledger`).

What it ADDS, and the tag path has no equivalent:

* **a per-PASS ceiling on MEMORY reads**. The per-tool budgets bound each verb
  on its own, which a pass can spend by asking several different memory verbs
  the same question. Rationing that in the objective's prose does not hold, so
  it is enforced: N reads across the whole pass, whichever verbs they are, and
  the (N+1)th is ANSWERED rather than vetoed — R-27's `unavailable` with a reason, in the
  same shape a memory verb's own wiring gap produces, because a corrective
  sentence invites the retry and a state does not.

What it drops, and each is absent BY CONSTRUCTION rather than merely unused —
there is no `Deliverer` to hold, no `terminal` parameter, no turn-ending latch,
no `tool_choice` relaxation, no forced-first-tool pin, no DEC-93 recovery flag:

**this guard never ends a turn, and structurally cannot.** It registers
`BeforeToolCallEvent` and `AfterToolCallEvent` only. `BeforeModelCallEvent` is
the hook whose `cancel` is how `ReplyGuard` ends a turn, so not registering it
is the mechanism, not a convention — the stopping condition and the wall clock
belong to the Dreamer's runtime, which can see the whole run, rather than to a
per-tool veto that can only see one call.

`evaluate` is pure and SDK-free, so every rule is testable without strands; the
hook methods are the thin translation layer (RK-2).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, Literal, get_args

from strands.hooks import (
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from ..obs.metrics import Metrics
from .call_ledger import CallLedger
from .guards import _list_tool_names, unknown_arguments_veto

log = logging.getLogger("nora.dream_guard")

__all__ = [
    "GUARD_RULES", "MEMORY_BUDGET_REASON", "TOOL_BUDGET_REASON", "DreamGuard",
    "GuardRule",
]

#: The pass-wide ledger's ONE key. It is not a tool name and cannot collide
#: with one: the memory ledger is a second `CallLedger` INSTANCE whose only
#: entry this is, so the per-tool ledger never sees it and it never sees a
#: per-tool count.
_PASS_MEMORY = "<memory reads this pass>"  # noqa: S105 — a ledger key, not a secret
#: The pass-wide TOOL ledger's one key: every
#: tool call of one pass, whatever the verb, on a third `CallLedger` instance.
_PASS_TOOLS = "<tool calls this pass>"  # noqa: S105 — a ledger key, not a secret

#: The `rule` label vocabulary on `nora_dream_guard_vetoes_total`, DERIVED off
#: a `Literal` rather than written as a bare string at each call site
#: (AGENTS.md: «Label vocabularies are DERIVED from the code, never
#: hand-copied»). A typo in a bare string would silently start a new series
#: instead of an error.
#:
#: The labels are different FINDINGS and must not collapse into one:
#: `R-7` says the objective asked for something it was never granted — a
#: config fault worth chasing — while `memory-budget` says it asked for MORE
#: of something it WAS granted than a pass is worth, which is ordinary model
#: behaviour the ceiling is there to absorb.
#:
#: **`tool-budget` is the third**, kept apart from `R-7` so «this tool does not
#: exist» and «this tool is at its ceiling» are different series, and the
#: second can answer the question worth asking of it: is a ceiling BINDING. Its
#: own rate is the instrument for `[dreamer.budgets.<adaptor>] tool_calls`: a
#: sustained non-zero on a read verb says the number is too low for the work,
#: which is exactly what `memory-budget` says for the memory ration.
#:
#: **`R-99` is the fourth**: an argument a tool's own signature does not
#: declare, refused before Strands' generated pydantic model can silently drop
#: it. It shares the exact name `ReplyGuard` uses for the identical rule on the
#: tag/participation path — the two counters differ (`nora_dream_guard_vetoes_
#: total` carries `adaptor`, `nora_guard_vetoes_total` does not), but the RULE
#: is the same decision-index row, and `unknown_arguments_veto` (`nora.agent.
#: guards`) is the one function both guards call, so the two cannot drift.
#:
#: **`pass-budget` is the fifth**: the pass
#: made as many tool calls, over every verb together, as one pass may. It is
#: `memory-budget`'s shape over the whole grant rather than the memory cluster,
#: and it is its own label because «this pass researched as much as it may» is
#: the reading that sizes `[dreamer.budgets.<adaptor>] max_tool_calls`.
GuardRule = Literal["R-7", "memory-budget", "tool-budget", "R-99", "pass-budget"]
GUARD_RULES: frozenset[str] = frozenset(get_args(GuardRule))

_R7_RULE: GuardRule = "R-7"
_MEMORY_RULE: GuardRule = "memory-budget"
_TOOL_BUDGET_RULE: GuardRule = "tool-budget"
_BAD_ARGUMENTS_RULE: GuardRule = "R-99"
_PASS_RULE: GuardRule = "pass-budget"  # noqa: S105 — a label, not a secret

#: What a memory verb refused by the per-pass ceiling says, verbatim. A
#: CONSTANT because two readers depend on the exact words — the model, which
#: must be able to tell this from an outage, and
#: `tests/unit/test_dream_memory_budget.py`, which is the only thing that can
#: say the sentence still reaches the model at all.
MEMORY_BUDGET_REASON = "memory budget for this pass is spent"

#: What ANY tool refused by the per-pass tool ceiling says, verbatim, for
#: `MEMORY_BUDGET_REASON`'s reason.
TOOL_BUDGET_REASON = "tool budget for this pass is spent"

#: Strands' `ToolResult.status` vocabulary — `"success" | "error"` — which is
#: the TRANSPORT's word for «this call completed», and is a different axis from
#: R-27's `ok` / `nothing_found` / `unavailable`, which lives INSIDE the
#: result's text. A tool answering `unavailable` still rides a `success`
#: `ToolResult` (`strands.tools.decorator._wrap_tool_result` JSON-encodes the
#: returned dict onto one), and that is the shape `_answer_unavailable`
#: reproduces.
#:
#: NAMED rather than written inline because
#: `tests/test_output_contracts.py::test_no_agent_module_returns_a_status_
#: outside_the_vocabulary` scans `nora.agent` for `{"status": <literal>}` and
#: reads every hit as an R-27 status a tool can hand the model — which is the
#: right reading for a tool's own return, and the wrong one for this envelope.
#: A constant keeps the scan honest instead of teaching it an exception.
_SDK_RESULT_OK = "success"


class DreamGuard(HookProvider):
    """Model-facing veto logic for one Dreamer run. Constructed fresh per run,
    like `ReplyGuard` is per tag, so counters reset by construction.

    `adaptor` is the run's identity and labels every veto —
    `nora_dream_guard_vetoes_total{adaptor, rule}`. It is required and has no
    default for the reason `Deliverer.audience` has none: an unlabelled series
    from a loop nobody is watching answers "which objective is asking for a
    tool it was never granted?" with silence.
    """

    def __init__(
        self, *, adaptor: str, budgets: Mapping[str, int], metrics: Metrics,
        result_caps: Mapping[str, int] | None = None,
        memory_tools: frozenset[str] = frozenset(),
        memory_budget: int | None = None,
        param_names: Mapping[str, frozenset[str]] | None = None,
        pass_budget: int | None = None,
        per_tool_ceilings: bool = True,
    ) -> None:
        self._adaptor = adaptor
        # `False` is the Cognition session's shape (DEC-247): the tool names
        # stay the membership set (R-7's unknown-tool veto and R-99 still
        # run) but no count vetoes a call, so a session that needs fifty
        # reads is not told «you already called X». Every other adaptor
        # keeps the default; the session's bound is rounds, tokens and time.
        self._per_tool_ceilings = per_tool_ceilings
        self._metrics = metrics
        self._ledger = CallLedger(budgets)
        # R-99: each
        # tool's accepted argument names, from `DreamToolSource.tool_param_
        # names()` (`RegistryToolSource` delegates to the same `ToolRegistry.
        # tool_param_names()` `ReplyGuard` reads — one source, two guards).
        # `None` is the rule OFF: a guard built with no tool source in scope is
        # veto-free on arguments rather than forced to invent a table.
        self._param_names = param_names
        # For the unknown-tool sentence only. The ledger is the authority on
        # membership (`knows`); this is the same set spelled for a human, held
        # once rather than re-derived on every veto.
        self._tool_names = frozenset(budgets)
        # Per-tool result size caps, enforced in `_after_tool`. The tag path's
        # results are bounded upstream by their callers; the Dreamer's are not,
        # and one unbounded page fetch could spend the run's whole token budget
        # on context. A run with no caps clamps nothing.
        self._result_caps = result_caps or {}
        # THE PER-PASS MEMORY CEILING. A second `CallLedger` with
        # one synthetic key, rather than a bare integer, and the reuse is the
        # argument: the ledger already separates «finished» from «admitted and
        # still running», which is the half that makes a ceiling hold WITHIN a
        # parallel batch rather than only between model turns, and it already
        # releases a reservation on every never-ran path (DEC-33). A hand-rolled
        # counter here would be a second copy of both rules, and the copy that
        # gets fixed is never both.
        #
        # `None` (or an empty verb set) means no ceiling: nothing counted,
        # nothing refused. An adaptor holding no
        # memory verb is unaffected either way.
        self._memory_tools = frozenset(memory_tools)
        self._memory: CallLedger | None = (
            CallLedger({_PASS_MEMORY: memory_budget})
            if memory_budget is not None and self._memory_tools
            else None
        )
        # `toolUseId` -> the R-27 body `_after_tool` must put in its place.
        # Held between the two hooks because the SDK's own cancel path builds
        # an ERROR `ToolResult`, and the whole point of this refusal is that it
        # is not an error. Keyed on the id so a parallel batch's refusals do not
        # answer each other's calls — `CallLedger.release`'s own lesson.
        #
        # An entry is dropped by `_after_tool`, which Strands fires for a
        # cancelled call too; one that never fires leaves its entry behind. The
        # guard lives ONE sample, so the worst case is bounded by that sample's
        # `turns` x batch size — a few dozen short strings — which is why this
        # is a dict rather than a cache with an eviction policy.
        # Every tool call of the pass, whatever the verb.
        # `None` is no pass-wide ceiling, which is every adaptor that does
        # not set `max_tool_calls`: the per-tool and memory ceilings alone.
        self._pass: CallLedger | None = (
            CallLedger({_PASS_TOOLS: pass_budget})
            if pass_budget is not None else None
        )
        self._budget_refusals: dict[str, str] = {}

    def _veto(self, rule: GuardRule, message: str) -> str:
        self._metrics.inc(
            "nora_dream_guard_vetoes_total", adaptor=self._adaptor, rule=rule,
        )
        log.warning("dream guard veto %s (adaptor=%s): %s", rule, self._adaptor, message)
        return message

    def evaluate(self, tool_name: str, tool_input: dict) -> str | None:
        """The corrective sentence to cancel this call with, or None to admit it.

        Pure and SDK-free. `tool_input` is the raw input the model emitted; R-99
        is the rule that reads it.
        """
        if not self._ledger.knows(tool_name):
            return self._veto(
                _R7_RULE, f"The tool {tool_name!r} does not exist. "
                       f"Use {_list_tool_names(self._tool_names)}.",
            )
        # R-99: the
        # SAME check `ReplyGuard.evaluate` runs, from the SAME function
        # (`nora.agent.guards.unknown_arguments_veto`) — see that function's
        # own docstring for the exemption and the fail-closed-on-no-data
        # reasoning, which apply here unchanged. A Dreamer pass runs native
        # tools built by the identical `build_tool_specs`, so an unknown
        # argument is as reachable from a background pass as from a tag.
        bad_arguments = unknown_arguments_veto(tool_name, tool_input, self._param_names)
        if bad_arguments is not None:
            return self._veto(_BAD_ARGUMENTS_RULE, bad_arguments)
        # Finished PLUS admitted-and-still-running, so a parallel batch is
        # measured against itself. The number quoted is the same one, because
        # «you already called it 0 time(s)» while refusing the fifth call of a
        # batch reads to a model as a reason to retry.
        if self._per_tool_ceilings and self._ledger.at_ceiling(tool_name):
            return self._veto(
                _TOOL_BUDGET_RULE, f"You already called {tool_name} "
                       f"{self._ledger.spent(tool_name)} time(s) and have that context. "
                       "Answer from what you already have.",
            )
        return None

    def memory_refusal(self, tool_name: str) -> dict[str, str] | None:
        """The R-27 body this memory verb gets because the PASS's memory budget
        is spent, or None to let the call through.

        Pure and SDK-free like `evaluate`, and a SEPARATE method rather than a
        branch inside it because the two produce different things: `evaluate`
        returns a corrective SENTENCE for a call that should not have been made,
        this returns a tool RESULT for a call that was reasonable and has no
        answer left to give. Folding them would force one return type on both
        and the fold would be the veto's.

        **The note does NOT offer her a way out.** Told a hard cap, the model
        declines the work rather than spending less, so a refusal ending «say so
        and decide nothing» would grant that permission mid-pass, at the moment
        she is least sure. It names what to do instead and tells her to finish.

        **`unavailable`, never `nothing_found` and never an error.** A spent
        budget is «I could not check», exactly the state R-27 reserves for a
        wiring gap; `nothing_found` would assert that memory holds nothing about
        the person, which this cannot know, and an error invites the retry the
        ceiling exists to stop. The note says what to do INSTEAD, because a
        refusal a model cannot act on is a refusal it argues with.
        """
        if self._memory is None or tool_name not in self._memory_tools:
            return None
        if not self._memory.at_ceiling(_PASS_MEMORY):
            return None
        spent = self._memory.spent(_PASS_MEMORY)
        self._metrics.inc(
            "nora_dream_guard_vetoes_total",
            adaptor=self._adaptor, rule=_MEMORY_RULE,
        )
        log.info(
            "dream memory budget spent (adaptor=%s tool=%s reads=%d): "
            "answering unavailable", self._adaptor, tool_name, spent,
        )
        return {
            "status": "unavailable",
            "note": (
                f"{MEMORY_BUDGET_REASON} — {spent} memory read(s) is the whole "
                "pass's ration and you have made them. Nothing is broken and "
                "there is nothing to retry: every further memory call will "
                "answer exactly this. Finish the decision from what your "
                "prompt already carries — the people index, the note bodies "
                "and each room's standing — and return your envelope now."
            ),
        }

    def pass_refusal(self, tool_name: str) -> dict[str, str] | None:
        """The R-27 body ANY call gets because the pass's tool ceiling is
        spent, or None to let it through. `memory_refusal`'s shape and its
        reasons (`unavailable`, a note naming what to do instead, no way out
        offered), over every verb rather than the memory cluster."""
        if self._pass is None or not self._pass.at_ceiling(_PASS_TOOLS):
            return None
        spent = self._pass.spent(_PASS_TOOLS)
        self._metrics.inc(
            "nora_dream_guard_vetoes_total",
            adaptor=self._adaptor, rule=_PASS_RULE,
        )
        log.info(
            "dream tool budget spent (adaptor=%s tool=%s calls=%d): "
            "answering unavailable", self._adaptor, tool_name, spent,
        )
        return {
            "status": "unavailable",
            "note": (
                f"{TOOL_BUDGET_REASON} — {spent} tool call(s) is the whole "
                "pass's ration and you have made them. Nothing is broken and "
                "there is nothing to retry: every further tool call will "
                "answer exactly this. Finish from what your prompt and the "
                "reads you already made carry, and return your answer now."
            ),
        }

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Tool hooks ONLY — see the module docstring. Registering
        `BeforeModelCallEvent` here would hand this guard the power to end a
        turn, which is exactly what it must not have.
        """
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_tool(self, event: Any) -> None:
        try:
            tool_use = getattr(event, "tool_use", None) or {}
            name = tool_use.get("name", "")
            tool_input = tool_use.get("input") or {}
            if not name:
                return
            ref = str(tool_use.get("toolUseId") or "")
            message = self.evaluate(name, tool_input)
            if message:
                event.cancel_tool = message
                return
            refusal = self.pass_refusal(name) or self.memory_refusal(name)
            if refusal is not None:
                # REFUSED, not vetoed. `cancel_tool` is the only way to stop a
                # call from running, and the SDK turns it into an ERROR
                # `ToolResult` — so the body is stashed here and `_after_tool`
                # puts it in place of that error, which is the one hook that
                # can (`AfterToolCallEvent._can_write("result")`, pinned in
                # `tests/test_strands_api_contract.py`). The cancel message
                # carries the same body so that a run where the rewrite somehow
                # does not happen still reads as a spent budget rather than as
                # a crash — which is also what an id-less call gets, below.
                #
                # **Stashed only when there IS an id.** `_budget_refusals[""]`
                # would be one slot shared by every id-less refusal in a batch,
                # so the second one would take the first's body and the first
                # would reach the model as the SDK's raw error — the exact
                # shape this refusal must never have. Strands always supplies a
                # `toolUseId` (`strands.tools.executors._executor`), so this is
                # the degraded path: the call is still refused, and the body
                # still reaches the model, on an error result rather than a
                # rewritten one. Refusing correctly matters more than rendering
                # prettily.
                body = json.dumps(refusal, ensure_ascii=False)
                if ref:
                    self._budget_refusals[ref] = body
                event.cancel_tool = body
                return
            # ADMITTED. Held against the ceiling from here until `_after_tool`
            # releases it, so the rest of a parallel batch is measured against
            # this call too. The mutation lives HERE and not in `evaluate` on
            # purpose: `evaluate` is pure so every rule stays testable without
            # strands, and this hook is the layer that owns lifecycle.
            self._ledger.reserve(name, ref=ref)
            if self._pass is not None:
                # The pass ceiling is held the same way, on the same id, so a
                # parallel batch larger than `max_tool_calls` is refused in
                # this hook rather than admitted in full: `pass_refusal` above
                # reads `spent` (finished plus in flight), and only this line
                # makes an admitted call count while it runs.
                self._pass.reserve(_PASS_TOOLS, ref=ref)
            if self._memory is not None and name in self._memory_tools:
                # The pass ledger holds its reservation on the SAME id, in its
                # own dictionaries — so a batch of three memory verbs emitted
                # together is measured against itself, which is the failure
                # `call_ledger`'s docstring records at the per-tool grain.
                #
                # An id-less call reserves here and is never released (see
                # `_after_tool`), so it holds the ration for the rest of the
                # pass. That is the FAIL-CLOSED direction and it is chosen: a
                # ceiling that leaks upward on a missing id is a ceiling that
                # is not one.
                self._memory.reserve(_PASS_MEMORY, ref=ref)
        except Exception:
            # A guard bug must not kill an unattended run; worst case it fails
            # open, logged. Same posture as `ReplyGuard._before_tool`.
            log.exception("DreamGuard._before_tool raised; allowing the call")

    def _after_tool(self, event: Any) -> None:
        """Release the reservation, then charge the budget only if the call RAN.

        Two early returns, both inherited from `ReplyGuard._after_tool` and both
        non-obvious:

        1. `exception is not None` — a FAILED call consumes no budget (DEC-33).
           The model's own retry is the thing a charge here would refuse.
        2. `cancel_message is not None` — Strands fires this hook for a call
           `_before_tool` VETOED as well: `cancel_tool` produces an error
           `ToolResult` with `exception=None`
           (`strands.tools.executors._executor`, verified against 1.50.2), so
           `cancel_message` is the ONLY field separating the two.
           `tests/test_strands_api_contract.py` pins that against the real SDK
           because it is load-bearing rather than incidental. A background loop
           that double-charges itself for the calls it refused would shrink its
           own budget every time it corrected the model, in silence.

        The reservation goes back on EVERY path — vetoed, failed, or done — and
        therefore BEFORE both returns. It is not budget; it is "this call is
        still running".

        **Keyed on the SDK's `toolUseId`**, so it drops THIS call's reservation
        rather than whichever one is outstanding. A vetoed call reaches this
        hook and never reserved, so a name-keyed release would decrement a
        SIBLING's reservation in the same parallel batch — reopening the
        ceiling by one, and with it the trace `call_ledger` exists to prevent.
        """
        try:
            tool_use = getattr(event, "tool_use", None) or {}
            name = tool_use.get("name", "")
            ref = str(tool_use.get("toolUseId") or "")
            self._ledger.release(name, ref=ref)
            if self._memory is not None and ref:
                # An unknown `ref` releases nothing, so this is a no-op for
                # every non-memory call and for a refused one — no branch on
                # the tool name is needed or wanted here.
                #
                # **`and ref` is load-bearing.**
                # `CallLedger.release` only takes the id-keyed path `if ref`;
                # with an empty one it falls through to the NAME-keyed path and
                # decrements `_in_flight[_PASS_MEMORY]` — and this ledger's
                # single key is the same for every tool, so an id-less call to
                # ANY verb would hand a memory read's reservation back and
                # reopen the ceiling by one. That is exactly the sibling-release
                # defect `call_ledger.release` documents at the per-tool grain,
                # and it is worse here because there the name at least told the
                # two calls apart.
                self._memory.release(_PASS_MEMORY, ref=ref)
            if self._pass is not None and ref:
                # `and ref` for the memory ledger's reason just above.
                self._pass.release(_PASS_TOOLS, ref=ref)
            body = self._budget_refusals.pop(ref, None) if ref else None
            if body is not None:
                # OUR refusal. Replace the SDK's error result with R-27's third
                # state, in the shape a memory verb's own `unavailable` reaches
                # the model in (`strands.tools.decorator._wrap_tool_result`
                # JSON-encodes a returned dict into ONE text block on a
                # `success` result) — so the model cannot tell this refusal
                # apart from the tool answering «I could not check», which is
                # exactly what it is. No budget is charged: the call never ran.
                self._answer_unavailable(event, ref, body)
                return
            if getattr(event, "exception", None) is not None:
                return                       # a failed call does not consume budget
            if getattr(event, "cancel_message", None) is not None:
                return                       # a vetoed call never ran
            self._ledger.record(name)
            if self._memory is not None and name in self._memory_tools:
                self._memory.record(_PASS_MEMORY)
            if self._pass is not None:
                self._pass.record(_PASS_TOOLS)
            cap = self._result_caps.get(name)
            if cap is not None:
                self._clamp_result(event, name, cap)
        except Exception:
            log.exception("DreamGuard._after_tool raised; ignoring")

    def _answer_unavailable(self, event: Any, ref: str, body: str) -> None:
        """Put R-27's `unavailable` where the SDK put its cancellation error.

        `AfterToolCallEvent.result` is one of exactly two writable fields on a
        hook event (`_can_write`, verified against the installed SDK and pinned
        in `tests/test_strands_api_contract.py`); everything else raises
        `AttributeError`. A fresh dict rather than an in-place edit, because the
        thing being replaced is the WHOLE result — status included — and a
        half-rewritten error result is the worst of both.
        """
        event.result = {
            "toolUseId": ref,
            "status": _SDK_RESULT_OK,
            "content": [{"text": body}],
        }

    def _clamp_result(self, event: Any, name: str, cap: int) -> None:
        """Cut a capped tool's result text down to its cap, IN PLACE.

        The marker says the cap exists: a truncated result reads as truncated,
        not as the whole answer, which is what stops the model summarising half a page as if it were
        the page. Only `text` content blocks are clamped: the dream tools
        return plain dicts, which Strands wraps as one `text` block
        (`strands.tools.decorator._wrap_tool_result`, verified against the
        installed SDK), and a block of any other shape is left exactly as it
        arrived rather than guessed at.
        """
        result = getattr(event, "result", None)
        if not isinstance(result, dict):
            return
        content = result.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and len(text) > cap:
                block["text"] = (
                    text[:cap]
                    + f"\n... [truncated: this {name} result exceeded the "
                      f"{cap}-character cap; the rest was cut before it "
                      f"reached you]"
                )
