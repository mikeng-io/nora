"""Retain the graph endpoints when source rows and their cascade children expire."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from itertools import batched
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql import memory_index
from nora.infra.sql.cognitive_graph_schema import EXPIRED_COVERAGE, REGISTRY
from nora.infra.sql.cognitive_graph_store import content_digest
from nora.infra.sql.schema import MESSAGES, METADATA
from nora.memory import ITEM_SOURCES, PROTECTED_ITEM_SOURCES

log = logging.getLogger(__name__)

_BATCH = 500
_Item = tuple[sa.Table, sa.RowMapping]


def expired_between(
    unit: str, start: datetime, end: datetime | sa.ColumnElement[datetime], *,
    sources: Iterable[str] | None = None, persons: Sequence[str] | None = None,
) -> sa.ColumnElement[bool]:
    c = EXPIRED_COVERAGE.c
    query = sa.exists().where(c.unit == unit, c["from"] <= end, c["to"] >= start)
    if sources is not None:
        query = query.where(c.source.in_(sources))
    if persons is not None:
        query = query.where(sa.or_(c.person.is_(None), c.person.in_(persons)))
    return query


def older_than(table: sa.Table, now: datetime, days: int) -> sa.ColumnElement[bool]:
    return sa.func.coalesce(table.c.occurred_at, table.c.asserted_at) < now - timedelta(days=days)


def _key_match(
    columns: Sequence[sa.Column[Any]], rows: Sequence[sa.RowMapping],
) -> sa.ColumnElement[bool]:
    if len(columns) == 1:
        return columns[0].in_([row[columns[0].key] for row in rows])
    return sa.tuple_(*columns).in_([tuple(row[c.key] for c in columns) for row in rows])


async def _lock_candidates(
    conn: AsyncConnection, table: sa.Table, where: Sequence[sa.ColumnElement[bool]],
    limit: int | None,
) -> list[sa.RowMapping]:
    c = table.c
    pk = tuple(table.primary_key)
    candidates = (await conn.execute(
        sa.select(*pk, c.graph_id, c.unit).where(*where).order_by(*pk).limit(limit),
    )).mappings().all()
    # A source upsert registers first. Use the same lock order and recheck
    # the expiry predicate in case the source was refreshed while we waited.
    registry = REGISTRY.c
    keys = sorted({(row["graph_id"], row["unit"]) for row in candidates if row["graph_id"]})
    for key_group in batched(keys, _BATCH):
        await conn.execute(sa.select(registry.id).where(
            sa.tuple_(registry.id, registry.unit).in_(key_group),
        ).order_by(registry.id, registry.unit).with_for_update(key_share=True))
    rows: list[sa.RowMapping] = []
    for candidate_group in batched(candidates, _BATCH):
        identities = {
            tuple(row[col.key] for col in pk): (row["graph_id"], row["unit"])
            for row in candidate_group
        }
        locked = (await conn.execute(
            sa.select(table).where(*where, _key_match(pk, candidate_group))
            .order_by(*pk).with_for_update(),
        )).mappings()
        rows.extend(row for row in locked if identities[
            tuple(row[col.key] for col in pk)
        ] == (row["graph_id"], row["unit"]))
    return rows


async def _cascade_items(
    conn: AsyncConnection, parent: sa.Table, rows: Sequence[sa.RowMapping],
) -> list[_Item]:
    items: list[_Item] = []
    for name in ITEM_SOURCES:
        table = METADATA.tables[name]
        for fk in table.foreign_key_constraints:
            if fk.referred_table is not parent or fk.ondelete != "CASCADE":
                continue
            for group in batched(rows, _BATCH):
                cols = tuple(fk.columns)
                keys = [tuple(row[e.column.key] for e in fk.elements) for row in group]
                children = (await conn.execute(
                    sa.select(table).where(sa.tuple_(*cols).in_(keys)).with_for_update(),
                )).mappings().all()
                items.extend((table, row) for row in children)
                items.extend(await _cascade_items(conn, table, children))
    return items


async def _mark_expired(conn: AsyncConnection, items: Iterable[_Item]) -> None:
    registry = [
        {
            "item_id": row["graph_id"], "item_unit": row["unit"], "item_time": row["occurred_at"],
            "item_hash": content_digest(table.name, row),
        }
        for table, row in items if row["unit"] is not None
    ]
    if registry:
        await conn.execute(sa.update(REGISTRY).where(
            REGISTRY.c.id == sa.bindparam("item_id"), REGISTRY.c.unit == sa.bindparam("item_unit"),
        ).values(
            state="tombstoned", occurred_at=sa.bindparam("item_time"),
            content_hash=sa.bindparam("item_hash"), tombstoned_at=sa.func.now(),
        ), registry)


async def _record_coverage(conn: AsyncConnection, items: Iterable[_Item]) -> None:
    coverage: dict[tuple[str, str, str | None], tuple[datetime, datetime]] = {}
    for table, row in items:
        if row["unit"] is None or table.name not in ITEM_SOURCES:
            continue
        # A source with no occurrence can only put its coverage at observation time.
        at = row["occurred_at"] or row["asserted_at"]
        for person in row["persons"] or (None,):
            key = row["unit"], table.name, person
            first, last = coverage.get(key, (at, at))
            coverage[key] = min(first, at), max(last, at)
    if coverage:
        await conn.execute(sa.insert(EXPIRED_COVERAGE), [
            {"unit": unit, "source": source, "person": person, "from": first, "to": last}
            for (unit, source, person), (first, last) in coverage.items()
        ])


async def tombstone_items(
    conn: AsyncConnection, table: sa.Table, *where: sa.ColumnElement[bool],
    limit: int | None = None, metrics: Any = None, skip_blocked: bool = False,
) -> list[sa.RowMapping]:
    """Remove the selected source rows within the caller's transaction."""
    if table.name in PROTECTED_ITEM_SOURCES:
        raise ValueError(f"protected item source: {table.name}")
    rows = await _lock_candidates(conn, table, where, limit)
    primary_key = tuple(table.primary_key)
    removed = []
    expired = []
    for row in rows:
        try:
            async with conn.begin_nested():
                items = [(table, row), *await _cascade_items(conn, table, [row])]
                await _mark_expired(conn, items)
                if table.name == "messages":
                    await memory_index.drop(
                        conn, "message", [row["id"]], metrics=metrics, strict=True,
                    )
                elif table.name == "social_item":
                    source_id = memory_index.social_source_id(row["network"], row["item_id"])
                    await memory_index.drop(
                        conn, "social", [source_id], metrics=metrics, strict=True,
                    )
                await conn.execute(sa.delete(table).where(_key_match(primary_key, [row])))
                if table.name == "message_edit":
                    await memory_index.project(
                        conn, "message", row["message_id"], metrics=metrics, strict=True,
                    )
                elif table.name == "social_item_state":
                    source_id = memory_index.social_source_id(row["network"], row["item_id"])
                    await memory_index.project(
                        conn, "social", source_id, metrics=metrics, strict=True,
                    )
        except sa.exc.IntegrityError as exc:
            if not skip_blocked or getattr(exc.orig, "sqlstate", None) != "23503":
                raise
            key = tuple(row[column.key] for column in primary_key)
            log.warning("prune skipped %s item key=%r because of a foreign key", table.name, key)
            if metrics is not None:
                metrics.inc("nora_errors_total", stage="prune")
            continue
        removed.append(row)
        expired.extend(items)
    await _record_coverage(conn, expired)
    return removed


async def prune_sources(
    engine: AsyncEngine, horizons: Mapping[str, int], keep_messages: int | None, *,
    now: datetime, metrics: Any = None,
) -> int:
    pruned = 0
    for source, days in horizons.items():
        table = METADATA.tables[source]
        async with engine.begin() as conn:
            rows = await tombstone_items(
                conn, table, older_than(table, now, days), metrics=metrics, skip_blocked=True,
            )
            pruned += len(rows)
    if keep_messages is None:
        return pruned
    m = MESSAGES.c
    ranked = sa.select(
        m.id,
        sa.func.row_number().over(
            partition_by=m.conversation_id, order_by=[m.ts.desc(), m.id.desc()],
        ).label("rank"),
    ).subquery()
    doomed = sa.select(ranked.c.id).where(ranked.c.rank > keep_messages)
    async with engine.begin() as conn:
        rows = await tombstone_items(
            conn, MESSAGES, m.id.in_(doomed), metrics=metrics, skip_blocked=True,
        )
    return pruned + len(rows)
