"""`SqlMemoStore` — a unit's memos, each a revision chain (Register · memo,
DEC-306).

Append-only, the lesson store's shape (`nora.infra.sql.lessons`): `create`
inserts a `memo` and its first revision, `append` adds the next one. The head is
the highest `seq`; nothing is updated or deleted, so nothing here keeps
`row_history` (`nora.infra.sql.history.APPEND_ONLY`).

**`append` is compare-and-set on the head revision id**, inside one
transaction, and `uq_memo_revision_seq` decides a race the read cannot see: the
loser raises `StaleMemoError`, never a forked chain. Every read is scoped by
unit, so another unit's memo is not found.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.memo import Memo, MemoRevision, MemoWithdrawnError, StaleMemoError
from nora.infra.sql import memory_index
from nora.infra.sql.schema import MEMO, MEMO_REVISION
from nora.infra.sql.tag_traces import body_turn_lineage

__all__ = ["SqlMemoStore"]


def _revision(row: sa.Row[Any]) -> MemoRevision:
    return MemoRevision(
        id=row.id, memo_id=row.memo_id, seq=row.seq, title=row.title, body=row.body,
        citations=tuple(row.citations), mode=row.mode, state=row.state,
        written_at=row.written_at, established_by=row.established_by, model=row.model,
    )


async def _memo(conn: Any, row: sa.Row[Any]) -> Memo:
    return Memo(
        id=row.mid, unit=row.unit, platform=row.platform,
        conversation_id=row.conversation_id, created_at=row.created_at,
        head=replace(_revision(row), lineage=await body_turn_lineage(
            conn, body=row.body, unit=row.unit, strict=False,
        )),
    )


def _is_uuid(text: str) -> bool:
    """A memo id comes from a model; a string that is not a UUID names no memo,
    and must not reach Postgres as a type error."""
    try:
        UUID(text)
    except ValueError:
        return False
    return True


def _heads() -> sa.Select[Any]:
    """Every memo joined to its head revision (the highest `seq`)."""
    top = MEMO_REVISION.alias("top")
    newest = (
        sa.select(sa.func.max(top.c.seq)).where(top.c.memo_id == MEMO.c.id)
        .scalar_subquery()
    )
    return (
        sa.select(MEMO.c.id.label("mid"), MEMO.c.unit, MEMO.c.platform,
                  MEMO.c.conversation_id, MEMO.c.created_at, *MEMO_REVISION.c)
        .select_from(MEMO.join(
            MEMO_REVISION,
            sa.and_(MEMO_REVISION.c.memo_id == MEMO.c.id, MEMO_REVISION.c.seq == newest),
        ))
    )


class SqlMemoStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def kept(self, *, unit: str) -> tuple[Memo, ...]:
        stmt = (
            _heads().where(MEMO.c.unit == unit, MEMO_REVISION.c.state == "kept")
            .order_by(MEMO.c.created_at, MEMO.c.id)
        )
        async with self._engine.connect() as conn:
            return tuple([await _memo(conn, r) for r in (await conn.execute(stmt)).all()])

    async def get(self, *, unit: str, memo_id: str) -> Memo | None:
        if not _is_uuid(memo_id):
            return None
        stmt = _heads().where(MEMO.c.unit == unit, MEMO.c.id == memo_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
            return None if row is None else await _memo(conn, row)

    async def revisions(self, *, unit: str, memo_id: str) -> tuple[MemoRevision, ...]:
        if not _is_uuid(memo_id):
            return ()
        stmt = (
            sa.select(*MEMO_REVISION.c)
            .select_from(MEMO_REVISION.join(MEMO, MEMO.c.id == MEMO_REVISION.c.memo_id))
            .where(MEMO.c.unit == unit, MEMO_REVISION.c.memo_id == memo_id)
            .order_by(MEMO_REVISION.c.seq.desc())
        )
        async with self._engine.connect() as conn:
            return tuple([replace(_revision(r), lineage=await body_turn_lineage(
                conn, body=r.body, unit=unit, strict=False,
            )) for r in (await conn.execute(stmt)).all()])

    async def create(
        self, *, unit: str, platform: str, conversation_id: str, title: str, body: str,
        citations: Sequence[str], mode: str, established_by: str, model: str | None,
        now_ms: int,
        # Called with (conn, memo) inside this write's transaction: the
        # Cognition session binds its idempotency receipt to the row's commit.
        receipt: Any = None,
    ) -> Memo:
        async with self._engine.begin() as conn:
            lineage = await body_turn_lineage(conn, body=body, unit=unit)
            memo_id = (await conn.execute(
                sa.insert(MEMO).values(
                    unit=unit, platform=platform, conversation_id=conversation_id,
                    created_at=now_ms,
                ).returning(MEMO.c.id)
            )).scalar_one()
            row = (await conn.execute(
                sa.insert(MEMO_REVISION).values(
                    memo_id=memo_id, seq=1, title=title, body=body,
                    citations=list(citations), mode=mode, state="kept",
                    written_at=now_ms, established_by=established_by, model=model,
                ).returning(*MEMO_REVISION.c)
            )).one()
            await memory_index.project(conn, "memo", str(memo_id))
            memo = Memo(
                id=memo_id, unit=unit, platform=platform,
                conversation_id=conversation_id, created_at=now_ms,
                head=replace(_revision(row), lineage=lineage),
            )
            if receipt is not None:
                await receipt(conn, memo)
        return memo

    async def append(
        self, *, unit: str, memo_id: str, base_revision_id: str, title: str, body: str,
        citations: Sequence[str], mode: str, state: str, established_by: str,
        model: str | None, now_ms: int,
    ) -> MemoRevision:
        if not _is_uuid(memo_id):
            raise LookupError(f"no memo {memo_id!r}")
        try:
            async with self._engine.begin() as conn:
                lineage = await body_turn_lineage(conn, body=body, unit=unit)
                head = (await conn.execute(
                    _heads().where(MEMO.c.unit == unit, MEMO.c.id == memo_id)
                )).one_or_none()
                if head is None:
                    raise LookupError(f"no memo {memo_id!r} in this unit")
                if str(head.id) != base_revision_id:
                    raise StaleMemoError(
                        f"memo {memo_id} is at revision {head.id}, not {base_revision_id}",
                    )
                if head.state != "kept":
                    # Withdrawn is final: nothing appends onto it.
                    raise MemoWithdrawnError(f"memo {memo_id} is withdrawn")
                row = (await conn.execute(
                    sa.insert(MEMO_REVISION).values(
                        memo_id=memo_id, seq=head.seq + 1, title=title, body=body,
                        citations=list(citations), mode=mode, state=state,
                        written_at=now_ms, established_by=established_by, model=model,
                    ).returning(*MEMO_REVISION.c)
                )).one()
                await memory_index.project(conn, "memo", memo_id)
        except sa.exc.IntegrityError as exc:
            if "uq_memo_revision_seq" not in str(exc):
                raise
            raise StaleMemoError(f"memo {memo_id} was revised concurrently") from exc
        return replace(_revision(row), lineage=lineage)
