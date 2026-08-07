from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from nora.app.eval_support import REPLY_GUARD_BUDGETS


@dataclass(frozen=True)
class Result:
    passed: bool
    reason: str = ""


class RepliesAtLeastOnce:
    """A triggered turn must produce a reply.

    Was `ExactlyOnceSendReply` (n == 1), the cap-of-1 invariant. Multi-message
    (DEC-42) makes two or three replies valid, so the exactly-one upper bound is
    gone from here; the lower bound (she MUST reply when called) stays, and the
    upper bound (never past the cap) is owned by `ToolBudget`, which reads the
    same `send_reply` budget — so the two are one number, not two (DEC-10)."""

    def evaluate(self, t) -> Result:
        n = t.tool_calls.count("send_reply")
        return Result(n >= 1, f"send_reply called {n} time(s), expected at least 1")


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
        if t.prefill_sufficient and "load_transcript" in t.tool_calls:
            return Result(False, "load_transcript called despite a sufficient pre-fill (R-15)")
        return Result(True)


class HonchoFedOncePerReply:
    def evaluate(self, t) -> Result:
        delivered = bool(t.sent)
        expected = 1 if delivered else 0
        ok = t.honcho_feeds == expected
        return Result(ok, f"honcho fed {t.honcho_feeds} time(s), expected {expected}")
