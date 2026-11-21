"""`FetchedPageStore` over SQLAlchemy Core.

**Never raises** (the `SqlMediaObjectStore` discipline): the index is a
dedup and a read-back, not the fetch itself — a failed `get` costs the
served-from-store path, a failed `put` costs only the next call's cache.
`web_fetch` already holds the text it is about to serve, so a store error
degrades to no persistence, never to a lost answer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.fetched_page import FetchedPage
from nora.infra.sql.blob_refs import held_elsewhere
from nora.infra.sql.item_registration import SqlItemRegistration
from nora.infra.sql.item_retention import older_than, tombstone_items
from nora.infra.sql.schema import FETCHED_PAGES

log = logging.getLogger("nora.infra.sql.fetched_pages")


def _row_to_page(r: Row[Any]) -> FetchedPage:
    return FetchedPage(
        conversation_id=r.conversation_id, url=r.url,
        blob_key=r.blob_key, fetched_at=r.fetched_at,
        expires_at=r.expires_at, provenance=r.provenance or {},
    )


class SqlFetchedPageStore:
    """Satisfies `nora.domain.fetched_page.FetchedPageStore`."""

    def __init__(
        self, engine: AsyncEngine, *, registration: SqlItemRegistration | None = None,
        horizon_days: int | None = None,
    ) -> None:
        self._engine = engine
        self._horizon_days = horizon_days
        self._registration = registration or engine.get_execution_options().get("item_registration")

    async def get(
        self, *, conversation_id: str, url: str, now: datetime
    ) -> FetchedPage | None:
        try:
            stmt = sa.select(FETCHED_PAGES).where(
                FETCHED_PAGES.c.conversation_id == conversation_id,
                FETCHED_PAGES.c.url == url,
                FETCHED_PAGES.c.expires_at > now,
            )
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("fetched_pages get failed")
            return None
        return _row_to_page(row) if row is not None else None

    async def put(self, *, page: FetchedPage) -> None:
        try:
            async with self._engine.begin() as conn:
                reg = self._registration
                values = await reg.planes(
                    conn, FETCHED_PAGES, unit=reg.scope.unqualified_room_unit(page.conversation_id),
                    room=page.conversation_id, occurred_at=page.fetched_at,
                    key=(page.conversation_id, page.url),
                ) if reg else {}
                await conn.execute(self._put_statement(page, values))
        except Exception:
            log.exception("fetched_pages put failed")

    @staticmethod
    def _put_statement(page: FetchedPage, planes: dict[str, Any]) -> Any:
        return pg_insert(FETCHED_PAGES).values(
                **planes,
                conversation_id=page.conversation_id, url=page.url,
                blob_key=page.blob_key, fetched_at=page.fetched_at,
                expires_at=page.expires_at, provenance=page.provenance,
            ).on_conflict_do_update(
                index_elements=[
                    FETCHED_PAGES.c.conversation_id, FETCHED_PAGES.c.url],
                set_={
                    "blob_key": page.blob_key,
                    "fetched_at": page.fetched_at,
                    "expires_at": page.expires_at,
                    "provenance": page.provenance,
                    **planes,
                },
            )

    async def purge_expired(self, *, now: datetime, limit: int = 20) -> list[str]:
        try:
            due = FETCHED_PAGES.c.expires_at <= now
            if self._horizon_days is not None:
                due = sa.or_(due, older_than(FETCHED_PAGES, now, self._horizon_days))
            async with self._engine.begin() as conn:
                rows = await tombstone_items(conn, FETCHED_PAGES, due, limit=limit)
                keys = list(dict.fromkeys(row["blob_key"] for row in rows))
                held = await held_elsewhere(conn, keys)
        except Exception:
            log.exception("fetched_pages purge failed")
            return []
        return [k for k in keys if k not in held]
