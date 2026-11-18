"""Write social items and their observed changes in each observing unit."""

from __future__ import annotations

from collections.abc import Sequence, Set
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.social_stream import (
    PLACE_STATE,
    BackfillEnd,
    ItemState,
    Origin,
    PollStage,
    RecordedCounts,
    SocialItem,
    counts_changed,
)
from nora.infra.sql import memory_index
from nora.infra.sql.item_registration import SqlItemRegistration
from nora.infra.sql.memory_index import social_source_id
from nora.infra.sql.schema import SOCIAL_ITEM, SOCIAL_ITEM_COUNT, SOCIAL_ITEM_STATE, SOCIAL_POLL


class SocialItemWrites:
    """The social stream's observed source writes."""

    def __init__(
        self, engine: AsyncEngine, *, opted_out: Set[str] = frozenset(),
        metrics: Any = None, registration: SqlItemRegistration | None = None,
    ) -> None:
        self._engine = engine
        self._opted_out = opted_out
        self._metrics = metrics
        self._registration = registration

    async def insert_item(
        self, item: SocialItem, *, person: str, origin: Origin, seen_at: datetime,
        media_ref: str | None = None, location: str | None = None,
        tagged: tuple[str, ...] = (), mentioned: tuple[str, ...] = (),
        read_id: str | None = None,
    ) -> bool:
        """Record one item. True when it is new; False when `(network, item_id)`
        was already recorded, in which case nothing was written and the first
        row stands unchanged."""
        stmt = (
            pg_insert(SOCIAL_ITEM)
            .values(
                network=item.network, item_id=item.item_id, person=person,
                account=item.account, source_kind=item.kind, taken_at=item.taken_at,
                expires_at=item.expires_at, caption=item.caption, alt_text=item.alt_text,
                location=location, tagged=list(tagged), mentioned=list(mentioned),
                media_ref=media_ref, seen_at=seen_at, origin=origin, collab=item.collab,
                read_id=read_id,
            )
            .on_conflict_do_nothing(index_elements=["network", "item_id", "unit"])
        )
        wrote = False
        async with self._engine.begin() as conn:
            if (reg := self._registration) is None:
                wrote = bool((await conn.execute(stmt)).rowcount)
            else:
                for unit in await reg.scope.person_units(person) or (None,):
                    inserted = await reg.insert_once(
                        conn, SOCIAL_ITEM, stmt, unit=unit, persons=(person,),
                        occurred_at=item.taken_at,
                    )
                    wrote |= inserted is not None
            if wrote:
                await memory_index.project(
                    conn, "social", social_source_id(item.network, item.item_id),
                    metrics=self._metrics,
                )
        return wrote

    async def record_poll(
        self, *, network: str, person: str, stage: PollStage, outcome: str,
        latest_reel_media: int | None = None, media_count: int | None = None,
        total_clips_count: int | None = None, advanced_marker: bool = False,
        advanced_counts: bool = False, latency_ms: int | None = None,
        id: str | None = None, cursor: str | None = None,
        end_reason: BackfillEnd | None = None,
    ) -> None:
        """Append one probe or read. A read passes its own `id`, the
        `read_id` its items were recorded with; a back-fill page its next
        `cursor`, None when the walk ended on it, and its `end_reason`."""
        values = {
            "network": network, "person": person, "stage": stage, "outcome": outcome,
            "latest_reel_media": latest_reel_media, "media_count": media_count,
            "total_clips_count": total_clips_count, "advanced_marker": advanced_marker,
            "advanced_counts": advanced_counts, "latency_ms": latency_ms,
            "cursor": cursor, "end_reason": end_reason,
        }
        if id is not None:
            values["id"] = id
        async with self._engine.begin() as conn:
            registration = self._registration
            units = await registration.scope.person_units(person) if registration else ()
            if self._registration is not None:
                values["recorded_at"] = await conn.scalar(sa.select(sa.func.clock_timestamp()))
            for index, unit in enumerate(units or (None,)):
                copy = dict(values)
                if id is not None and index:
                    copy["id"] = str(uuid5(NAMESPACE_URL, f"nora-social-poll:{id}:{unit}"))
                await self._record_at(conn, SOCIAL_POLL, copy, unit, person)

    async def _record_at(
        self, conn: AsyncConnection, table: sa.Table, values: dict[str, Any],
        unit: str | None, person: str | None,
    ) -> None:
        if (reg := self._registration) is not None:
            now = values.get("recorded_at")
            if now is None:
                now = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
            values = {
                **values, "recorded_at": now,
                **await reg.planes(
                    conn, table, unit=unit, occurred_at=now, persons=(person,),
                    key=(values["id"],) if "id" in values else None,
                ),
            }
        await conn.execute(sa.insert(table).values(values))

    async def _append_item_state(
        self, conn: AsyncConnection, network: str, item_id: str, values: dict[str, Any],
    ) -> None:
        rows: Sequence[tuple[str | None, str | None]]
        if self._registration is None:
            rows = [(None, None)]
        else:
            item = SOCIAL_ITEM.c
            rows = (await conn.execute(
                sa.select(item.unit, item.person).where(
                    item.network == network, item.item_id == item_id,
                ),
            )).tuples().all() or [(None, None)]
        for unit, person in rows:
            await self._record_at(
                conn, SOCIAL_ITEM_STATE, {"network": network, "item_id": item_id, **values},
                unit, person,
            )

    async def record_state(
        self, *, network: str, item_id: str, state: ItemState, reason: str | None = None,
    ) -> None:
        """Append that an item is gone or hidden."""
        async with self._engine.begin() as conn:
            await self._append_item_state(
                conn, network, item_id, {"source_state": state, "reason": reason},
            )
            await memory_index.project(
                conn, "social", social_source_id(network, item_id), metrics=self._metrics,
            )

    async def record_place(self, *, network: str, item_id: str, place: str | None) -> None:
        """Append that an item's tagged place is now `place` (None: the tag
        was removed), and re-project it, so the index searches the current
        place and nothing else."""
        async with self._engine.begin() as conn:
            await self._append_item_state(
                conn, network, item_id, {"source_state": PLACE_STATE, "place": place},
            )
            await memory_index.project(
                conn, "social", social_source_id(network, item_id), metrics=self._metrics,
            )

    async def record_counts(
        self, *, network: str, items: Sequence[SocialItem], read_id: str | None,
    ) -> int:
        """Append the counts `items` carry, one read's, for each recorded item
        among them whose counts differ from its latest row (`counts_changed`)
        → how many rows were written. An item not recorded, or whose read
        carried nothing, writes none."""
        carried = {i.item_id: i.counts for i in items if i.counts.known}
        if not carried:
            return 0
        i = SOCIAL_ITEM
        async with self._engine.begin() as conn:
            observed = (await conn.execute(
                sa.select(i.c.item_id, i.c.unit, i.c.person)
                .where(i.c.network == network, i.c.item_id.in_(sorted(carried))),
            )).all()
            written: set[str] = set()
            for unit in {r.unit for r in observed}:
                members = {r.item_id: r.person for r in observed if r.unit == unit}
                latest = await latest_counts(conn, [(network, key) for key in members], unit=unit)
                for item, counts in sorted(carried.items()):
                    if item not in members or not counts_changed(
                        counts, latest.get((network, item)),
                    ):
                        continue
                    await self._record_at(conn, SOCIAL_ITEM_COUNT, {
                        "network": network, "item_id": item, "likes": counts.likes,
                        "comments": counts.comments, "reposts": counts.reposts,
                        "likes_hidden": counts.likes_hidden is True, "read_id": read_id,
                    }, unit, members[item])
                    written.add(item)
        return len(written)


_ALL_COUNT_UNITS = object()


async def latest_counts(
    conn: Any, items: Sequence[tuple[str, str]],
    *, unit: object = _ALL_COUNT_UNITS,
) -> dict[tuple[str, str], RecordedCounts]:
    """Each of `items` (`(network, item_id)`) that has a `social_item_count`
    row → its latest. Unfiltered: a caller passes only items it may show or
    write for."""
    if not items:
        return {}
    c = SOCIAL_ITEM_COUNT
    scope = [] if unit is _ALL_COUNT_UNITS else [c.c.unit.is_not_distinct_from(unit)]
    rows = (await conn.execute(
        sa.select(
            c.c.network, c.c.item_id, c.c.likes, c.c.comments, c.c.reposts,
            c.c.likes_hidden, c.c.recorded_at,
        )
        .where(sa.tuple_(c.c.network, c.c.item_id).in_(sorted(set(items))), *scope)
        .distinct(c.c.network, c.c.item_id)
        .order_by(c.c.network, c.c.item_id, c.c.recorded_at.desc(), c.c.id.desc()),
    )).all()
    return {
        (r.network, r.item_id): RecordedCounts(
            likes=r.likes, comments=r.comments, reposts=r.reposts,
            likes_hidden=r.likes_hidden, read_at=r.recorded_at,
        )
        for r in rows
    }
