"""`InMemoryStandingLedger` — the scoped Standing Ledger's in-memory sibling.

`InMemoryStandingStore` next door is bound to `SqlStandingStore` by a shared
contract test; this fake follows the same rule for `SqlStandingLedger`: it
runs the REAL `check_ledger_op` — the validator the SQL store itself calls
inside `apply` — so a write that Postgres would refuse is refused here too,
and nothing in a test can pass `status` on an `open`, skip `base`, or close
without `outcome`. The one thing it does not model is concurrency: `apply`
is serial by construction, which is exactly why `base` is still asserted.
"""

from __future__ import annotations

from collections.abc import Sequence

from nora.domain.lineage import RenderRoom
from nora.domain.models import TranscriptRow
from nora.domain.note_body import Shown
from nora.domain.standing_ledger import (
    LedgerEntry,
    LedgerOp,
    LedgerResult,
    ThreadSpan,
)
from nora.domain.standing_revise import check_ledger_op


class InMemoryStandingLedger:
    """`StandingLedger` over a list — the same contract, no engine."""

    def __init__(self, entries: Sequence[LedgerEntry] = ()) -> None:
        self.entries: list[LedgerEntry] = list(entries)
        self.applied: list[LedgerOp] = []

    async def read(
        self, *, unit: str, platform: str, conversation_id: str,
    ) -> tuple[LedgerEntry, ...]:
        return tuple(
            e for e in self.entries
            if e.unit == unit and (
                e.scope == "unit" or
                (e.platform == platform and e.conversation_id == conversation_id)
            ))

    async def span_rows(
        self, span: ThreadSpan, *, limit: int, before_ts: int | None = None,
    ) -> list[TranscriptRow]:
        return []

    async def history(
        self, *, unit: str, kind: str, id: str,
    ) -> tuple[LedgerEntry, ...]:
        return tuple(e for e in self.entries
                     if e.unit == unit and e.kind == kind and e.id == id)

    async def apply(
        self, op: LedgerOp, *, target: RenderRoom, shown: Shown,
        now_ms: int,
    ) -> LedgerResult:
        self.applied.append(op)
        room = target.room
        if room is None or not room.unit:
            return LedgerResult(refusal=_refusal("bad_scope"))
        prior = next((e for e in self.entries if e.id == op.id), None) \
            if op.id else None
        new_id = f"{op.kind[:1]}-{len(self.entries) + 1}"
        revised = check_ledger_op(
            op, prior, shown=shown, unit=room.unit,
            platform=room.platform, conversation_id=room.primary_id,
            now_ms=now_ms, id=new_id,
        )
        if not isinstance(revised, LedgerEntry):
            return LedgerResult(entry=prior, refusal=revised)
        if prior is None:
            self.entries.append(revised)
        else:
            self.entries[self.entries.index(prior)] = revised
        return LedgerResult(entry=revised)


def _refusal(reason: str):
    from nora.domain.standing_revise import Refusal
    return Refusal(reason, "the writing room needs a known unit")
