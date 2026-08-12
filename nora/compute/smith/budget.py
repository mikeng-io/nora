"""The per-job spend guardrail (Nora Compute spec §5) Phase 2's own
self-review flagged as missing: a ceiling on how many tool calls one job may
make, independent of its wall-clock timeout (`LocalDockerBackend`'s own
guardrail) — bounds a job that's technically still running but thrashing
(retrying, looping on errors) rather than doing useful work.

Deliberately a hand-rolled counter, not a Strands-native budget mechanism --
and this file's own docstring above told a prior implementer to check
whether the installed Strands Agents SDK has its own max-iterations/
max-tokens concept before relying solely on this. It does: `Agent.__call__`
takes `limits={"turns": ..., "total_tokens": ..., "output_tokens": ...}`,
enforced natively inside `strands/event_loop/event_loop.py`. That was never
wired until this comment was written -- see `agent.py`'s `main()`, which is
now the PRIMARY, actually-effective ceiling.

This counter is still real and still load-bearing, but for a narrower job
than it originally claimed: verified empirically against the installed
`strands-agents==1.50.2` that `DecoratedFunctionTool.stream()` (a `@tool`-
wrapped function's real call path) catches every exception except
`InterruptException` and converts it into an ordinary `status: "error"`
ToolResult -- so `BudgetExceededError` raised from inside a guarded tool
body NEVER reaches `agent.py`'s own `agent(task)` call; the model just sees
a tool error like any other and may retry. That makes a bare
`except BudgetExceededError` around `agent(task)` dead code. `exceeded`
below exists so `main()` can detect that the counter tripped by reading
its own state AFTER the call returns, rather than trusting the (unreachable)
exception to propagate.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class BudgetExceededError(Exception):
    """Raised when a job's tool-call ceiling is spent. Strands' own
    `DecoratedFunctionTool.stream()` catches this like any other tool
    exception (it is not `InterruptException`, the one type Strands
    re-raises) and turns it into an ordinary error `ToolResult` -- it does
    NOT propagate out of `agent(task)` in agent.py's `main()`. `main()`
    instead checks `ToolCallBudget.exceeded` after the call returns; this
    exception's job is to stop the guarded call's own body from running
    (see `guarded` below) and to make each individual trip legible in the
    container's logs, not to unwind the agent loop itself."""


class ToolCallBudget:
    def __init__(self, *, max_calls: int) -> None:
        self._max_calls = max_calls
        self._spent = 0
        self._exceeded = False

    def spend(self) -> None:
        if self._spent >= self._max_calls:
            self._exceeded = True
            raise BudgetExceededError(
                f"tool-call budget exhausted ({self._max_calls} calls)"
            )
        self._spent += 1

    @property
    def remaining(self) -> int:
        return self._max_calls - self._spent

    @property
    def exceeded(self) -> bool:
        """True once `spend()` has actually REJECTED a call.

        Deliberately distinct from `remaining == 0`, which is also true
        after a job spent its whole budget on exactly `max_calls`
        *successful* calls and then finished normally -- that job did not
        thrash, it just used every unit it was given, and should not be
        reported as a budget failure. Only a call that was actually turned
        away sets this."""
        return self._exceeded


def guarded(budget: ToolCallBudget) -> Callable[[F], F]:
    """Wrap a tool function so every call spends one unit of `budget` BEFORE
    the function body runs — a call that would exceed the budget never
    executes its side effects at all."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            budget.spend()
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
