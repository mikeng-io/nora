"""The per-tool call ledger: what a guard has SPENT, and what it has PROMISED.

One class, two dictionaries, and the whole of DEC-33's in-flight reservation.
Extracted from `ReplyGuard` (which still holds one) so that a second guard —
`nora.agent.dream_guard.DreamGuard`, whose loop has no terminal verb and no
`Deliverer` — enforces the SAME ceiling with the same code rather than a
re-derivation of it. Two copies of a budget check are two chances to fix only
one of them.

**Why the two counts are separate, and must stay separate.** `counts` is calls
that COMPLETED; `_in_flight` is calls ADMITTED but not yet finished. A budget
check that reads only `counts` holds the ceiling BETWEEN model turns and not
WITHIN one: a model emitting its tool calls as a PARALLEL BATCH is asked about
every call in the batch before any of them completes, so all of them see the
pre-batch count and all pass.

Without the reservation, nine `place_info` calls could run against a budget
of four, and the turn would end `limit_total_tokens` with an empty message —
so the group hears nothing, which is R-23's shape and the worst way for a
budget to fail.

Folding the two into one counter would close that hole and open DEC-33's:
**a call that is vetoed, or that raises, or that otherwise never runs must
consume no budget**, because the model's own correction is the thing that would
be refused. So a reservation is RELEASED on those paths, while `counts` is only
ever incremented by a call that genuinely completed. One number cannot mean
both "this ran" and "this is running".
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["CallLedger"]


class CallLedger:
    """Per-tool budget accounting for one agent loop. Constructed fresh per
    invocation, like the guards that hold it, so counters reset by construction
    rather than by bookkeeping.

    `budgets` is DEC-10: the ceiling a tool costs lives with the tool
    (`nora.agent.registry.ToolRegistry.budgets()`), not with the guard. Its
    KEYS are also the ledger's membership set — `knows` is R-7's default-deny
    half, and every mutator ignores a tool this ledger was never told about, so
    an unknown name can neither be counted nor reserved.
    """

    def __init__(self, budgets: Mapping[str, int]) -> None:
        self._budgets = dict(budgets)
        self._counts: dict[str, int] = dict.fromkeys(self._budgets, 0)
        self._in_flight: dict[str, int] = {}
        # Which CALL each outstanding reservation belongs to, keyed by the
        # SDK's `toolUseId`. See `release`.
        self._reserved: dict[str, str] = {}

    @property
    def counts(self) -> dict[str, int]:
        """Calls that COMPLETED, per tool.

        Exposed as the live dict rather than a copy because callers read it by
        subscript (`counts["send_reply"]`) and because `ReplyGuard` publishes
        it under its own `counts` attribute, which tests read. Write through
        `record` — a caller that increments
        this directly bypasses nothing today, but says nothing about in-flight
        calls either, which is the half that makes the ceiling hold.
        """
        return self._counts

    def knows(self, tool_name: str) -> bool:
        """R-7's default-deny membership: was this tool ever granted a budget?

        The negative answer is what stops a model inventing a tool name. It is
        asked BEFORE `at_ceiling`, which fails closed for an unknown tool
        (budget 0) and would otherwise report an invented name as «you already
        called it» — a sentence that invites a retry instead of a correction.
        """
        return tool_name in self._budgets

    def record(self, tool_name: str) -> None:
        """Count a call that RAN. Never called for a vetoed or failed one."""
        if tool_name in self._counts:
            self._counts[tool_name] += 1

    def reserve(self, tool_name: str, *, ref: str = "") -> None:
        """Mark a call ADMITTED BUT NOT YET FINISHED, so it counts against the
        ceiling while it runs — see this module's docstring for the parallel
        batch that made this necessary.

        `ref` is the SDK's `toolUseId`, which is what makes `release` able to
        drop THIS call's reservation rather than whichever one happens to be
        outstanding. It defaults to empty only so a caller that has no id (a
        test driving the ledger directly) still works; production always has
        one.
        """
        if tool_name not in self._counts:
            return
        self._in_flight[tool_name] = self._in_flight.get(tool_name, 0) + 1
        if ref:
            self._reserved[ref] = tool_name

    def release(self, tool_name: str, *, ref: str = "") -> None:
        """Drop THIS call's reservation, whatever its outcome.

        **Keyed on `ref` when there is one.** `reserve` is called only on the
        ADMITTED path, but the after-tool hook fires for a VETOED call too — so
        a name-keyed release would let a vetoed call decrement a reservation
        belonging to a sibling in the same parallel batch:

            A admitted, B admitted     -> in_flight = 2, ceiling closed
            C VETOED (never reserved)
            C's after-hook releases    -> in_flight = 1
            D admitted                 -> a third call runs against a budget of 2

        which reopens the parallel-batch overrun this module exists to prevent
        with a single veto. Unknown `ref` releases nothing, which is the safe
        direction: a hook firing twice must not lend budget back.

        The name-only path serves callers with no id; it cannot be made exact
        without one.
        """
        if ref:
            name = self._reserved.pop(ref, None)
            if name is None:
                return
            tool_name = name
        if self._in_flight.get(tool_name):
            self._in_flight[tool_name] -= 1

    def spent(self, tool_name: str) -> int:
        """Finished PLUS in flight — the number a veto message must quote.

        Telling a model it «already called `place_info` 0 time(s)» while
        refusing the fifth call of a batch is a sentence that contradicts
        itself, and a model reads that as a reason to retry.
        """
        return self._counts.get(tool_name, 0) + self._in_flight.get(tool_name, 0)

    def at_ceiling(self, tool_name: str) -> bool:
        """Has this tool no call left? Fail-closed for a tool with no budget:
        an unknown name has a ceiling of 0 and is therefore always at it."""
        return self.spent(tool_name) >= self._budgets.get(tool_name, 0)
