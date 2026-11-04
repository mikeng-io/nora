"""`BrowserTabStore` over SQLAlchemy Core (`browser_tasks`, migration `0147`;
`nora.domain.browser_tasks`).

`expire_idle` is the sweep's compare-and-set: only rows still `open` with a
stale `last_activity_at` move, so an attach landing mid-sweep keeps its tab.
`expire_instance` retires a scope's open rows bound to a dead VM instance.
Nothing deletes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.browser_tasks import (
    EXPIRED,
    OPEN,
    BrowserTab,
    normalise_tab,
)
from nora.domain.computer_events import ensure_utc_event
from nora.infra.sql.schema import BROWSER_TASKS as T

_FIELDS = (
    "id", "scope", "task_id", "instance", "target_id", "status", "via",
    "last_url", "last_title", "detail", "created_at", "last_activity_at",
)


def _row(r: Row[Any]) -> BrowserTab:
    return normalise_tab(BrowserTab(**{f: getattr(r, f) for f in _FIELDS}))


class SqlBrowserTabStore:
    """Satisfies `nora.domain.browser_tasks.BrowserTabStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def bind(self, tab: BrowserTab) -> BrowserTab:
        tab = normalise_tab(tab)
        async with self._engine.begin() as conn:
            await conn.execute(T.insert().values(
                **{f: getattr(tab, f) for f in _FIELDS}))
        return tab

    async def get(self, tab_id: UUID) -> BrowserTab | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(T).where(T.c.id == tab_id))).first()
        return _row(row) if row is not None else None

    async def for_scope(self, scope: str, *, limit: int) -> list[BrowserTab]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(T).where(T.c.scope == scope)
                .order_by(T.c.last_activity_at.desc()).limit(limit))).all()
        return [_row(r) for r in rows]

    async def open_for_scope(self, scope: str, *, instance: str) -> list[BrowserTab]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(T).where(
                    T.c.scope == scope, T.c.instance == instance,
                    T.c.status == OPEN))).all()
        return [_row(r) for r in rows]

    async def touch(self, tab_id: UUID, *, now: datetime,
                    url: str = "", title: str = "") -> None:
        now = ensure_utc_event(now, field="now")
        values: dict[str, Any] = {"last_activity_at": now}
        if url:
            values["last_url"] = url
        if title:
            values["last_title"] = title
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(T).where(T.c.id == tab_id, T.c.status == OPEN)
                .values(**values))

    async def set_status(self, tab_id: UUID, *, status: str, now: datetime) -> None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(T).where(T.c.id == tab_id).values(status=status))

    async def expire_idle(self, *, idle_before: datetime, now: datetime,
                          limit: int) -> list[BrowserTab]:
        idle_before = ensure_utc_event(idle_before, field="idle_before")
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            rows = (await conn.execute(
                sa.update(T)
                .where(T.c.status == OPEN, T.c.last_activity_at < idle_before)
                .values(status=EXPIRED)
                .returning(T)
            )).all()
        return [_row(r) for r in rows[:limit]]

    async def expire_instance(self, *, scope: str, instance: str,
                              now: datetime) -> int:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.update(T)
                .where(T.c.scope == scope, T.c.status == OPEN,
                       T.c.instance != instance)
                .values(status=EXPIRED)
            )
        return int(result.rowcount or 0)
