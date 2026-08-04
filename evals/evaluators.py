from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from nora.app.eval_support import REPLY_GUARD_BUDGETS


@dataclass(frozen=True)
class Result:
    passed: bool
    reason: str = ""


class ExactlyOnceSendReply:
    def evaluate(self, t) -> Result:
        n = t.tool_calls.count("send_reply")
        return Result(n == 1, f"send_reply called {n} time(s), expected exactly 1")


class NoSendWithoutTrigger:
    def evaluate(self, t) -> Result:
        n = t.tool_calls.count("send_reply")
        return Result(n == 0, f"send_reply called {n} time(s) on a no-trigger case")


class ToolBudget:
    def evaluate(self, t) -> Result:
        counts = Counter(t.tool_calls)
        for name, count in counts.items():
            budget = REPLY_GUARD_BUDGETS.get(name)
            if budget is not None and count > budget:
                return Result(False, f"{name} called {count} time(s), budget is {budget}")
        return Result(True)


class NoRedundantRead:
    def evaluate(self, t) -> Result:
        if t.prefill_sufficient and "read_buffer" in t.tool_calls:
            return Result(False, "read_buffer called despite a sufficient pre-fill (R-15)")
        return Result(True)


class HonchoFedOncePerReply:
    def evaluate(self, t) -> Result:
        delivered = bool(t.sent)
        expected = 1 if delivered else 0
        ok = t.honcho_feeds == expected
        return Result(ok, f"honcho fed {t.honcho_feeds} time(s), expected {expected}")
