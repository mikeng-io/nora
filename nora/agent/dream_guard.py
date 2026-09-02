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

import logging
from collections.abc import Mapping
from typing import Any

from strands.hooks import (
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from ..obs.metrics import Metrics
from .call_ledger import CallLedger
from .guards import _list_tool_names

log = logging.getLogger("nora.dream_guard")

__all__ = ["DreamGuard"]


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
    ) -> None:
        self._adaptor = adaptor
        self._metrics = metrics
        self._ledger = CallLedger(budgets)
        # For the unknown-tool sentence only. The ledger is the authority on
        # membership (`knows`); this is the same set spelled for a human, held
        # once rather than re-derived on every veto.
        self._tool_names = frozenset(budgets)
        # Per-tool result size caps (design/dreamer-agent-loop/32 §3.3 point
        # 3), enforced in `_after_tool`. A run with no caps behaves exactly as
        # before — the tag path's results are bounded upstream by their
        # callers; the Dreamer's were not, and one unbounded page fetch could
        # spend the run's whole token budget on context.
        self._result_caps = result_caps or {}

    def _veto(self, rule: str, message: str) -> str:
        self._metrics.inc(
            "nora_dream_guard_vetoes_total", adaptor=self._adaptor, rule=rule,
        )
        log.warning("dream guard veto %s (adaptor=%s): %s", rule, self._adaptor, message)
        return message

    def evaluate(self, tool_name: str, tool_input: dict) -> str | None:
        """The corrective sentence to cancel this call with, or None to admit it.

        Pure and SDK-free. `tool_input` is accepted and unread: it is the raw
        input the model emitted, and it is part of the signature because a rule
        that looks at arguments (the shape R-45 and R-4 take on the tag path)
        must be addable here without changing every call site and every test.
        """
        if not self._ledger.knows(tool_name):
            return self._veto(
                "R-7", f"The tool {tool_name!r} does not exist. "
                       f"Use {_list_tool_names(self._tool_names)}.",
            )
        # Finished PLUS admitted-and-still-running, so a parallel batch is
        # measured against itself. The number quoted is the same one, because
        # «you already called it 0 time(s)» while refusing the fifth call of a
        # batch reads to a model as a reason to retry.
        if self._ledger.at_ceiling(tool_name):
            return self._veto(
                "R-7", f"You already called {tool_name} "
                       f"{self._ledger.spent(tool_name)} time(s) and have that context. "
                       "Answer from what you already have.",
            )
        return None

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
            message = self.evaluate(name, tool_input)
            if message:
                event.cancel_tool = message
                return
            # ADMITTED. Held against the ceiling from here until `_after_tool`
            # releases it, so the rest of a parallel batch is measured against
            # this call too. The mutation lives HERE and not in `evaluate` on
            # purpose: `evaluate` is pure so every rule stays testable without
            # strands, and this hook is the layer that owns lifecycle.
            self._ledger.reserve(name, ref=str(tool_use.get("toolUseId") or ""))
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
        hook and never reserved, so a name-keyed release let it decrement a
        SIBLING's reservation in the same parallel batch — reopening the
        ceiling by one, and with it the trace `call_ledger` exists to prevent.
        Measured on both guards, 2026-08-21 review.
        """
        try:
            tool_use = getattr(event, "tool_use", None) or {}
            name = tool_use.get("name", "")
            self._ledger.release(name, ref=str(tool_use.get("toolUseId") or ""))
            if getattr(event, "exception", None) is not None:
                return                       # a failed call does not consume budget
            if getattr(event, "cancel_message", None) is not None:
                return                       # a vetoed call never ran
            self._ledger.record(name)
            cap = self._result_caps.get(name)
            if cap is not None:
                self._clamp_result(event, name, cap)
        except Exception:
            log.exception("DreamGuard._after_tool raised; ignoring")

    def _clamp_result(self, event: Any, name: str, cap: int) -> None:
        """Cut a capped tool's result text down to its cap, IN PLACE.

        The marker says the cap exists — design/dreamer-agent-loop/32 §3.3:
        "a truncated result reads as truncated, not as the whole answer",
        which is what stops the model summarising half a page as if it were
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
