"""The per-job spend guardrail (Nora Compute spec §5) Phase 2's own
self-review flagged as missing: a ceiling on how many tool calls one job may
make, independent of its wall-clock timeout (`LocalDockerBackend`'s own
guardrail) — bounds a job that's technically still running but thrashing
(retrying, looping on errors) rather than doing useful work.

Deliberately a hand-rolled counter, not a Strands-native budget mechanism —
verify whether the installed Strands Agents SDK version has its own
max-iterations/max-tokens concept before relying solely on this; if it does,
prefer wiring that in ADDITION to this (a tool-call ceiling and a token
ceiling bound different things), never as a replacement for this file's
explicit, testable behavior.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class BudgetExceededError(Exception):
    """Raised when a job's tool-call ceiling is spent. Smith's own agent loop
    (agent.py) lets this propagate — an exhausted budget ends the job as a
    failure, the same way a killed container does at the wall-clock
    boundary, rather than letting it silently keep going."""


class ToolCallBudget:
    def __init__(self, *, max_calls: int) -> None:
        self._max_calls = max_calls
        self._spent = 0

    def spend(self) -> None:
        if self._spent >= self._max_calls:
            raise BudgetExceededError(
                f"tool-call budget exhausted ({self._max_calls} calls)"
            )
        self._spent += 1

    @property
    def remaining(self) -> int:
        return self._max_calls - self._spent


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
