"""Reserve a historical session's next call before sending it to the provider.

Reserve ceil(1.1 * estimated input) plus the configured per-call output cap.
If actual input exceeds that allowance, residual overshoot is at most that
call's excess (assuming the provider honours its output cap). Unknown usage
stops the session; retries cannot spend against an unread balance.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from opentelemetry import trace
from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent, HookProvider, HookRegistry

from nora.obs.tracing import annotate


class TokenReservation(HookProvider):
    def __init__(self, total_tokens: int) -> None:
        self.limit = total_tokens
        self.total: int | None = 0
        self.stopped = False
        self._estimate = 0
        self._input_allowance = 0
        self._in_flight = False

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before)
        registry.add_callback(AfterModelCallEvent, self.after)

    def before(self, event: BeforeModelCallEvent) -> None:
        if event.cancel:
            return
        estimate = event.projected_input_tokens
        output = event.agent.model.get_config().get("params", {}).get("max_tokens")
        if (self.total is None or type(estimate) is not int or estimate <= 0
                or type(output) is not int or output <= 0):
            raise RuntimeError(
                "token reservation unavailable: input, output ceiling or usage unknown")
        self._estimate = estimate
        self._input_allowance = (estimate * 11 + 9) // 10
        reserved = self._input_allowance + output
        attrs = {"nora.budget.estimated_input_tokens": estimate,
                 "nora.budget.reserved_input_tokens": self._input_allowance,
                 "nora.budget.output_ceiling": output,
                 "nora.budget.remaining_tokens": self.limit - self.total}
        # Strands copies these into its chat span, alongside its own actual usage.
        event.agent.trace_attributes.update(attrs)
        annotate(**attrs)
        if self.total + reserved > self.limit:
            self.stopped = True
            event.cancel = "limit_total_tokens"
            annotate(**{"nora.budget.admitted": False})
            return
        self._in_flight = True
        annotate(**{"nora.budget.admitted": True})

    def after(self, event: AfterModelCallEvent) -> None:
        if not self._in_flight:
            return
        self._in_flight = False
        response = event.stop_response
        usage = response.message.get("metadata", {}).get("usage", {}) if response else {}
        actual = usage.get("inputTokens")
        output = usage.get("outputTokens")
        total = usage.get("totalTokens")
        if (type(actual) is not int or actual <= 0 or type(output) is not int or output < 0
                or type(total) is not int or total < actual + output):
            self.total = None
            event.retry = False
            raise RuntimeError("token reservation unavailable: model usage unknown")
        if self.total is None:
            raise RuntimeError("token reservation unavailable: model usage unknown")
        self.total += total
        attrs = {"nora.budget.estimated_input_tokens": self._estimate,
                 "nora.budget.actual_input_tokens": actual,
                 "nora.budget.input_residual_tokens": max(0, actual - self._input_allowance),
                 "nora.budget.actual_output_tokens": output,
                 "nora.budget.actual_total_tokens": total,
                 "nora.budget.overshoot_tokens": max(0, self.total - self.limit)}
        annotate(**attrs)
        # Retry attempts share a cycle span; retain every attempt's measurement.
        with suppress(Exception):
            trace.get_current_span().add_event("token_reservation", attributes=attrs)
