"""Nora's computer as a metric (DEC-261).

`ComputerMetrics` is the observer `ComputerdRuntime` and the timed probe tell
about computerd (`nora.domain.computer.ComputerCallObserver`), and it is the one
place those reports become series:

- `nora_computer_up{target="computerd"}`: 1 when Nora's last contact got an
  answer, 0 when it did not. Written by every call and by the probe, so it moves
  on a quiet night (the probe) and the moment a real call fails (the call).
- `nora_computer_calls_total{op,status}`: every call, by operation and the status
  computerd answered.

**Built only while `[computer] enabled`.** With the computer off nothing here
exists, so neither series has a sample: not applicable, which is not the same as
down, and `ComputerDown` (`nora_computer_up == 0`) cannot fire on an absence.

**`prime()` creates the series at zero.** A labelled Prometheus series does not
exist until its first increment, and `increase()` over a series whose first
sample is already 1 reads 0, so the first `unavailable` after a restart would be
invisible to the rule written to catch it. Every `(op, status)` is created at 0
at start, and so is every `(tool, stage)` of
`nora_computer_audit_failures_total`, a should-be-zero counter: "never emitted"
could not be told from "never failed".
"""

from __future__ import annotations

from nora.domain.computer import (
    COMPUTER_AUDIT_STAGES,
    COMPUTER_OPS,
    SandboxStatus,
)
from nora.obs.metrics import Metrics

__all__ = ["COMPUTER_TARGET", "ComputerMetrics"]

#: The one value of `nora_computer_up`'s `target` label.
COMPUTER_TARGET = "computerd"

#: The `tool` values `nora_computer_audit_failures_total` carries: the agent
#: tools' own names plus the sweeper's (`computer_session`). The tools' set is
#: `nora.agent.tools.COMPUTER_TOOLS`, passed in by composition rather than
#: imported here (`nora.app` may import `nora.agent`, but a literal copy would
#: go stale when a tool is added).
SWEEPER_AUDIT_TOOL = "computer_session"


class ComputerMetrics:
    """Satisfies `nora.domain.computer.ComputerCallObserver`."""

    def __init__(self, metrics: Metrics, *, audit_tools: frozenset[str]) -> None:
        self._metrics = metrics
        self._audit_tools = audit_tools

    def prime(self) -> None:
        """Create every series this layer can emit, at zero. `nora_computer_up`
        is deliberately NOT created: it has no value until the first probe, and
        a 0 written here would be a lie."""
        for op in COMPUTER_OPS:
            for status in SandboxStatus:
                self._metrics.inc("nora_computer_calls_total", 0.0, op=op, status=status.value)
        for tool in sorted(self._audit_tools | {SWEEPER_AUDIT_TOOL}):
            for stage in COMPUTER_AUDIT_STAGES:
                self._metrics.inc("nora_computer_audit_failures_total", 0.0, tool=tool, stage=stage)

    def call(self, op: str, status: str, answered: bool) -> None:
        self._metrics.inc("nora_computer_calls_total", op=op, status=status)
        self._set_up(answered)

    def probe(self, answered: bool) -> None:
        self._set_up(answered)

    def _set_up(self, answered: bool) -> None:
        self._metrics.gauge("nora_computer_up", 1.0 if answered else 0.0, target=COMPUTER_TARGET)
