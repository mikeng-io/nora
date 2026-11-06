"""A run-scoped bound on model dispatch, fail-closed by construction.

`nora/agent/factory.create_client` wraps a client's `chat.completions`
calls with the installed `RunBudget` — before every request it
`reserve`s one call plus the declared worst-case output, and after each
answer it `reconcile`s the actual usage. A reserve that would cross any
bound raises `RunBudgetError` BEFORE the HTTP call leaves; a reconcile
that finds the bound crossed marks the budget spent so the NEXT reserve
refuses. The caller (an eval run) installs it through
`active_run_budget`; production never sets it, so it is a no-op there.

`max_tokens` the caller did not declare reserves `output_reserve`
whole — an unbounded request counts as the worst it could write.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class RunBudgetError(RuntimeError):
    """A model dispatch was refused by the run's installed budget."""


#: The installed run budget, or None (production default: ungated).
active_run_budget: ContextVar[RunBudget | None] = ContextVar(
    "active_run_budget", default=None)


@dataclass
class RunBudget:
    """Counts and bounds dispatched model calls for one run.

    Requests are charged at RESERVE time — a call that never leaves the
    process still counted toward the cap the moment it was admitted, so a
    loop of refused calls cannot spin the counter. Output tokens reserve
    the declared `max_tokens` and reconcile down to the measured usage.
    """

    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    deadline: float | None = None
    output_reserve: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _reserved_out: int = field(default=0, repr=False)
    _spent_reason: str | None = field(default=None, repr=False)
    monotonic: Any = time.monotonic

    def _check(self) -> None:
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)
        if self.deadline is not None and self.monotonic() >= self.deadline:
            self._spent_reason = "run budget: active-time cap reached"
            raise RunBudgetError(self._spent_reason)

    def reserve(self, declared_output_tokens: int | None) -> None:
        """Charge one request plus its worst-case output BEFORE dispatch."""
        self._check()
        if self.requests + 1 > self.max_requests:
            self._spent_reason = (
                f"run budget: model requests {self.requests + 1} > "
                f"{self.max_requests}")
            raise RunBudgetError(self._spent_reason)
        out = declared_output_tokens or self.output_reserve
        if self._reserved_out + out > self.max_output_tokens:
            self._spent_reason = (
                f"run budget: reserved output tokens "
                f"{self._reserved_out + out} > {self.max_output_tokens}")
            raise RunBudgetError(self._spent_reason)
        self.requests += 1
        self._reserved_out += out

    def reconcile(self, *, input_tokens: int, output_tokens: int,
                  declared_output_tokens: int | None) -> None:
        """Land the measured usage after the call returns."""
        out = declared_output_tokens or self.output_reserve
        self._reserved_out = max(0, self._reserved_out - out + output_tokens)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if self.input_tokens > self.max_input_tokens:
            self._spent_reason = (
                f"run budget: input tokens {self.input_tokens} > "
                f"{self.max_input_tokens}")
        elif self.output_tokens > self.max_output_tokens:
            self._spent_reason = (
                f"run budget: output tokens {self.output_tokens} > "
                f"{self.max_output_tokens}")

    def as_metadata(self) -> dict[str, Any]:
        """The bound state, for `run_started`/`run_finished` evidence."""
        return {
            "max_requests": self.max_requests,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "deadline": self.deadline,
            "output_reserve": self.output_reserve,
            "requests_used": self.requests,
            "input_tokens_used": self.input_tokens,
            "output_tokens_used": self.output_tokens,
        }


def declared_output(kwargs: dict[str, Any]) -> int | None:
    """The request's own output bound, whichever field the caller set."""
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


async def gated_create(budget: RunBudget, create: Any,
                       *args: Any, **kwargs: Any) -> Any:
    """`chat.completions.create` under a run budget: reserve, call, reconcile."""
    budget.reserve(declared_output(kwargs))
    response = await create(*args, **kwargs)
    usage = getattr(response, "usage", None)
    budget.reconcile(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        declared_output_tokens=declared_output(kwargs))
    return response
