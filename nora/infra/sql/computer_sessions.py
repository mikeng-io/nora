"""`ComputerSessionStore` over SQLAlchemy Core (`computer_sessions`, migration
`0082_computer_sessions`; `nora.domain.computer_sessions`).

`continue_or_open` is atomic by the partial unique index on `(scope) WHERE
status = 'open'`: an UPDATE continues the open session; with none, an
INSERT ... ON CONFLICT DO NOTHING opens one; a call that lost that race to a
concurrent one re-runs the UPDATE and continues the winner's. `expire` is a
compare-and-set on `last_activity_at`. Nothing here deletes a row.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.computer_sessions import (
    EXPIRED,
    OPEN,
    ComputerSession,
    ensure_utc_event,
    normalise_session,
)
from nora.infra.sql.schema import COMPUTER_SESSIONS

T = COMPUTER_SESSIONS
# Migration leaves the original scope, session IDs and state untouched.
_workspace_scope = sa.func.coalesce(T.c.stopped_at_detail["workspace_scope"].astext, T.c.scope)
#: The most tmux names a session records as held; more is a runaway, not a task.
MAX_HELD_TMUX = 20

_FIELDS = (
    "id", "platform", "conversation_id", "scope", "objective", "status",
    "created_at", "updated_at", "last_activity_at", "actor", "created_by_tag",
    "expired_at", "expired_reason", "stopped_at_detail",
)


def _row(r: Row[Any]) -> ComputerSession:
    return normalise_session(ComputerSession(**{f: getattr(r, f) for f in _FIELDS}))


class SqlComputerSessionStore:
    """Satisfies `nora.domain.computer_sessions.ComputerSessionStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def continue_or_open(
        self, *, platform: str, conversation_id: str, scope: str, objective: str,
        actor: str, tag_id: str, now: datetime, open_new: bool = True,
    ) -> tuple[ComputerSession, bool] | None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            found = await self._touch(conn, scope, now)
            if found is not None:
                return found, False
            if not open_new:
                return None
            inserted = (await conn.execute(
                pg.insert(T).values(
                    id=uuid.uuid4(), platform=platform, conversation_id=conversation_id,
                    scope=scope, objective=objective, status=OPEN, stopped_at_detail={},
                    created_at=now, updated_at=now, last_activity_at=now,
                    actor=actor, created_by_tag=tag_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[T.c.scope], index_where=sa.text("status = 'open'"),
                )
                .returning(T)
            )).first()
            if inserted is not None:
                return _row(inserted), True
            # A concurrent call opened it between the two statements: continue
            # that one (READ COMMITTED, so this UPDATE sees its commit).
            found = await self._touch(conn, scope, now)
        if found is None:
            raise RuntimeError("computer session neither continued nor opened")
        return found, False

    @staticmethod
    async def _touch(conn: AsyncConnection, scope: str, now: datetime) -> ComputerSession | None:
        row = (await conn.execute(
            sa.update(T)
            .where(T.c.id == (
                sa.select(T.c.id).where(_workspace_scope == scope, T.c.status == OPEN)
                .order_by(T.c.last_activity_at.desc(), T.c.id.desc()).limit(1).scalar_subquery()
            ), T.c.status == OPEN)
            .values(
                updated_at=sa.func.greatest(T.c.updated_at, now),
                last_activity_at=sa.func.greatest(T.c.last_activity_at, now),
            )
            .returning(T)
        )).first()
        return _row(row) if row is not None else None

    async def open_sessions(self, *, limit: int) -> list[ComputerSession]:
        stmt = (
            sa.select(T).where(T.c.status == OPEN)
            .order_by(T.c.last_activity_at).limit(limit)
        )
        async with self._engine.connect() as conn:
            return [_row(r) for r in (await conn.execute(stmt)).all()]

    async def for_scope(
        self, *, scope: str, limit: int,
    ) -> list[ComputerSession]:
        stmt = (
            sa.select(T)
            .where(_workspace_scope == scope)
            .order_by(T.c.last_activity_at.desc()).limit(limit)
        )
        async with self._engine.connect() as conn:
            return [_row(r) for r in (await conn.execute(stmt)).all()]

    async def note_resources(
        self, session_id: UUID, *, tmux: Sequence[str], now: datetime,
    ) -> None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.select(T.c.stopped_at_detail)
                .where(T.c.id == session_id, T.c.status == OPEN)
                .with_for_update()
            )).first()
            if row is None:
                return
            detail = dict(row.stopped_at_detail or {})
            held = [n for n in detail.get("tmux", []) if isinstance(n, str)]
            merged = held + [n for n in dict.fromkeys(tmux) if n not in held]
            if merged == held:
                return
            detail["tmux"] = merged[:MAX_HELD_TMUX]
            await conn.execute(
                sa.update(T).where(T.c.id == session_id, T.c.status == OPEN)
                .values(stopped_at_detail=detail, updated_at=sa.func.greatest(T.c.updated_at, now))
            )

    async def expire(
        self, session_id: UUID, *, seen_last_activity_at: datetime, reason: str,
        now: datetime,
    ) -> ComputerSession | None:
        now = ensure_utc_event(now, field="now")
        seen = ensure_utc_event(seen_last_activity_at, field="seen_last_activity_at")
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.update(T)
                .where(T.c.id == session_id, T.c.status == OPEN,
                       T.c.last_activity_at == seen)
                .values(status=EXPIRED, expired_at=now, expired_reason=reason,
                        updated_at=sa.func.greatest(T.c.updated_at, now))
                .returning(T)
            )).first()
        return _row(row) if row is not None else None
