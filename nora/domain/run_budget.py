"""A run-scoped bound on model dispatch, fail-closed by construction.

Admission lives at the transport: `_BoundedTransport` wraps the
`httpx` transport of every client a bounded run can reach — factory
clients, the judge's direct client, and SDK-owned provider clients
(`structured.build_structured_model` injects `bounded_http_client`
into the provider's `client_args`, so the `AsyncOpenAI` the SDK
builds internally dispatches through it too). Measuring at the wire
is the point: the transport sees the FINAL request body after every
SDK transformation — `response_format` schema expansion, `extra_body`
merges, default params — so the input bound and the output ceiling
apply to what actually leaves, not to Python-level arguments.

Per attempt, before the inner transport is called: the request count,
the campaign deadline, the serialized body's byte size against the
input bound, and the body's declared `max_tokens`/`max_completion_tokens`
(after every merge) against what the output cap still affords — an
`extra_body` override cannot enlarge the ceiling because the bound is
read from the merged body itself. Non-text message shapes and
non-JSON bodies refuse: the byte bound cannot speak for them.

Reconcile rides the response: non-stream JSON bodies yield `usage`;
streams are consumed through a wrapped iterator that bounds each
chunk by the remaining deadline and reconciles the terminal usage
chunk. Usage counts must be real nonnegative ints — `None`, a bool,
a negative or anything malformed is indeterminate, and indeterminate
is terminal: `spent` names it and every later admission refuses.
Each call's output reservation is released exactly once when its
response finishes reconciling, success or failure.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class RunBudgetError(RuntimeError):
    """A model dispatch was refused or a budget check failed terminally."""


#: The installed run budget, or None (production default: ungated).
active_run_budget: ContextVar[RunBudget | None] = ContextVar(
    "active_run_budget", default=None)


@dataclass
class RunBudget:
    """Counts and bounds dispatched model calls for one run.

    `admit` is the ONLY admission, one per HTTP attempt: request count,
    the final wire bytes against the input bound (the wire's own serialized
    size — a hard bound on what leaves, NOT a provider-token count;
    measured tokens reconcile separately and a measured overrun is
    terminal either way),
    the post-merge declared output ceiling, and the campaign deadline
    are all checked before the call reaches the inner transport.
    `reconcile` lands measured usage with strict nonnegative-int
    validation; anything indeterminate latches `spent`, which
    `finished` reports so a run can never return success over its
    bound.
    """

    max_requests: int
    max_input_tokens: int | None = None
    max_output_tokens: int = 40_000
    max_input_bytes: int | None = None
    stop_after_total_tokens: int | None = None
    deadline: float | None = None
    output_reserve: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_bytes: int = 0
    _reserved_out: int = field(default=0, repr=False)
    _spent_reason: str | None = field(default=None, repr=False)
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        # A measured cutoff stops the NEXT call; the final dispatched call
        # may overshoot. It is deliberately distinct from a hard token cap.
        if self.stop_after_total_tokens is not None and (
                type(self.stop_after_total_tokens) is not int
                or self.stop_after_total_tokens <= 0):
            raise ValueError("measured token cutoff must be a positive integer")
        # A strict input-TOKEN bound is a real request — and there is no
        # authoritative pre-dispatch bound for it: provider-side
        # template/framing expansion is not measurable without a
        # tokenizer (none may be invented), so the bounded path refuses
        # every dispatch rather than pretend a byte count is the token
        # bound. `max_input_bytes` remains the supported serialized-size
        # cap; measured tokens still reconcile and a measured overrun
        # is terminal either way.
        if self.max_input_tokens is not None:
            self._spent_reason = (
                "run budget: strict input-token bound unsupported — "
                "no authoritative pre-dispatch token bound exists")

    @property
    def spent(self) -> str | None:
        return self._spent_reason

    def _fail(self, reason: str) -> RunBudgetError:
        self._spent_reason = reason
        return RunBudgetError(reason)

    def remaining(self) -> float | None:
        """Seconds of campaign time left, or None when unbounded."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - self.monotonic())

    def would_admit(self) -> None:
        """The non-mutating half of `admit`: spent, deadline and request
        count refuse BEFORE the SDK builds a request — the real
        admission still happens once at the transport."""
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)
        if self.stop_after_total_tokens is not None:
            if self.input_tokens + self.output_tokens >= self.stop_after_total_tokens:
                raise self._fail("run budget: measured total-token cutoff reached")
            if self._reserved_out:
                raise self._fail("run budget: measured cutoff requires sequential calls")
        left = self.remaining()
        if left is not None and left <= 0:
            raise self._fail("run budget: active-time cap reached")
        if self.requests + 1 > self.max_requests:
            raise self._fail(
                f"run budget: model requests {self.requests + 1} > "
                f"{self.max_requests}")

    def admit(self, *, input_bytes: int,
              declared_output_tokens: int) -> int:
        """Admit one HTTP attempt or refuse BEFORE dispatch.

        Returns the output reservation to release exactly once when the
        attempt's response finishes reconciling."""
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)
        if self.stop_after_total_tokens is not None:
            if self.input_tokens + self.output_tokens >= self.stop_after_total_tokens:
                raise self._fail("run budget: measured total-token cutoff reached")
            if self._reserved_out:
                raise self._fail("run budget: measured cutoff requires sequential calls")
        left = self.remaining()
        if left is not None and left <= 0:
            raise self._fail("run budget: active-time cap reached")
        if self.requests + 1 > self.max_requests:
            raise self._fail(
                f"run budget: model requests {self.requests + 1} > "
                f"{self.max_requests}")
        if (self.max_input_bytes is not None
                and self.input_bytes + input_bytes > self.max_input_bytes):
            raise self._fail(
                f"run budget: input size {self.input_bytes + input_bytes} "
                f"B > {self.max_input_bytes}")
        out = declared_output_tokens or self.output_reserve
        if out <= 0:
            raise self._fail("run budget: no output ceiling to enforce")
        if self.output_tokens + self._reserved_out + out > self.max_output_tokens:
            raise self._fail(
                f"run budget: reserved output {self._reserved_out + out} "
                f"> {self.max_output_tokens}")
        self.requests += 1
        self.input_bytes += input_bytes
        self._reserved_out += out
        return out

    def reconcile(self, *, input_tokens: int,
                  output_tokens: int) -> None:
        """Land the measured usage — strict nonnegative ints only."""
        for name, value in (("input", input_tokens), ("output", output_tokens)):
            if type(value) is not int or value < 0:
                raise self._fail(
                    f"run budget: invalid {name} usage {value!r} — "
                    "indeterminate usage is spent, never zero")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if (self.max_input_tokens is not None
                and self.input_tokens > self.max_input_tokens):
            raise self._fail(
                f"run budget: input tokens {self.input_tokens} > "
                f"{self.max_input_tokens}")
        if self.output_tokens > self.max_output_tokens:
            raise self._fail(
                f"run budget: output tokens {self.output_tokens} > "
                f"{self.max_output_tokens}")

    def release(self, reservation: int) -> None:
        """Drop one attempt's output reservation, exactly once."""
        self._reserved_out = max(0, self._reserved_out - reservation)

    def invalidate(self, reason: str) -> None:
        """A dispatch ended indeterminate (cancel/timeout): spent."""
        if self._spent_reason is None:
            self._spent_reason = reason

    def finished(self) -> None:
        """Terminal check: anything invalid makes the run ERROR."""
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "max_requests": self.max_requests,
            "max_input_tokens": self.max_input_tokens,
            "max_input_bytes": self.max_input_bytes,
            "stop_after_total_tokens": self.stop_after_total_tokens,
            "max_output_tokens": self.max_output_tokens,
            "deadline": self.deadline,
            "output_reserve": self.output_reserve,
            "requests_used": self.requests,
            "input_bytes": self.input_bytes,
            "input_tokens_used": self.input_tokens,
            "output_tokens_used": self.output_tokens,
            "spent_reason": self._spent_reason,
        }

#: The transport/client adapters live in `nora.subject.run_budget_transport`
#: — `gate_client`, `bounded_http_client` and the httpx layer are not
#: domain code (the standard-library boundary).

#: The dispatch layer — `gated_create`, `gate_client`,
#: `bounded_http_client`, `_BoundedTransport`, `_BoundedStream` — lives in
#: `nora.agent.run_budget_transport` (httpx is not domain; evals reach it
#: through `nora.subject.run_budget_transport`, a re-export).
