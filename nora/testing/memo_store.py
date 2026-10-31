"""`InMemoryMemoStore` — the domain-only double for
`nora.infra.sql.memo.SqlMemoStore`, bound to it by
`tests/contract/test_memo_store_contract.py` so the two cannot drift.

It refuses what Postgres refuses (`ck_memo_revision_*`): an empty title or
body, one over the ceiling, no citation, a mode or state outside the closed
sets. A fake that stored whatever it was handed would turn a missing guarantee
into a passing gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from nora.domain.memo import Memo, MemoRevision, MemoWithdrawnError, StaleMemoError

#: Mirrors `nora.infra.sql.schema.MEMO_TITLE_CEILING_CHARS` /
#: `MEMO_BODY_CEILING_CHARS`; the contract suite pins them equal.
MEMO_TITLE_CEILING_CHARS = 200
MEMO_BODY_CEILING_CHARS = 4_000


@dataclass
class _Row:
    id: str
    unit: str
    platform: str
    conversation_id: str
    created_at: int
    chain: list[MemoRevision] = field(default_factory=list)


def _check(
    *, title: str, body: str, citations: Sequence[str], mode: str, state: str,
    established_by: str,
) -> None:
    if not title.strip() or len(title) > MEMO_TITLE_CEILING_CHARS:
        raise ValueError("ck_memo_revision_title")
    if not body.strip() or len(body) > MEMO_BODY_CEILING_CHARS:
        raise ValueError("ck_memo_revision_body")
    if not citations:
        raise ValueError("ck_memo_revision_cites_something")
    if mode not in ("told", "deduced", "abduced"):
        raise ValueError("ck_memo_revision_mode")
    if state not in ("kept", "withdrawn"):
        raise ValueError("ck_memo_revision_state")
    if not established_by.strip():
        raise ValueError("ck_memo_revision_names_its_writer")


class InMemoryMemoStore:
    def __init__(self) -> None:
        self.memos: dict[str, _Row] = {}

    def _memo(self, row: _Row) -> Memo:
        return Memo(
            id=row.id, unit=row.unit, platform=row.platform,
            conversation_id=row.conversation_id, created_at=row.created_at,
            head=row.chain[-1],
        )

    def _of(self, unit: str, memo_id: str) -> _Row | None:
        row = self.memos.get(memo_id)
        return row if row is not None and row.unit == unit else None

    async def kept(self, *, unit: str) -> tuple[Memo, ...]:
        rows = sorted(
            (r for r in self.memos.values() if r.unit == unit and r.chain[-1].state == "kept"),
            key=lambda r: (r.created_at, r.id),
        )
        return tuple(self._memo(r) for r in rows)

    async def get(self, *, unit: str, memo_id: str) -> Memo | None:
        row = self._of(unit, memo_id)
        return None if row is None else self._memo(row)

    async def revisions(self, *, unit: str, memo_id: str) -> tuple[MemoRevision, ...]:
        row = self._of(unit, memo_id)
        return () if row is None else tuple(reversed(row.chain))

    async def create(
        self, *, unit: str, platform: str, conversation_id: str, title: str, body: str,
        citations: Sequence[str], mode: str, established_by: str, model: str | None,
        now_ms: int, receipt: Any = None,
    ) -> Memo:
        _check(title=title, body=body, citations=citations, mode=mode, state="kept",
               established_by=established_by)
        if not unit.strip():
            raise ValueError("ck_memo_has_a_unit")
        memo_id = str(uuid.uuid4())
        row = _Row(id=memo_id, unit=unit, platform=platform,
                   conversation_id=conversation_id, created_at=now_ms)
        row.chain.append(MemoRevision(
            id=str(uuid.uuid4()), memo_id=memo_id, seq=1, title=title, body=body,
            citations=tuple(citations), mode=mode, state="kept", written_at=now_ms,
            established_by=established_by, model=model,
        ))
        self.memos[memo_id] = row
        memo = self._memo(row)
        if receipt is not None:
            await receipt(None, memo)
        return memo

    async def append(
        self, *, unit: str, memo_id: str, base_revision_id: str, title: str, body: str,
        citations: Sequence[str], mode: str, state: str, established_by: str,
        model: str | None, now_ms: int,
    ) -> MemoRevision:
        row = self._of(unit, memo_id)
        if row is None:
            raise LookupError(f"no memo {memo_id!r} in this unit")
        head = row.chain[-1]
        if head.id != base_revision_id:
            raise StaleMemoError(f"memo {memo_id} is at revision {head.id}")
        if head.state != "kept":
            raise MemoWithdrawnError(f"memo {memo_id} is withdrawn")
        _check(title=title, body=body, citations=citations, mode=mode, state=state,
               established_by=established_by)
        revision = MemoRevision(
            id=str(uuid.uuid4()), memo_id=memo_id, seq=head.seq + 1, title=title,
            body=body, citations=tuple(citations), mode=mode, state=state,
            written_at=now_ms, established_by=established_by, model=model,
        )
        row.chain.append(revision)
        return revision


__all__ = ["MEMO_BODY_CEILING_CHARS", "MEMO_TITLE_CEILING_CHARS", "InMemoryMemoStore"]
