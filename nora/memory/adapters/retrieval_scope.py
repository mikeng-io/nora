"""Fix the managed audience and compile the lineage permits before searching."""

from dataclasses import replace
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import FromClause

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy, entry_permits, section_permits
from nora.domain.lineage import (
    MESSAGE,
    UNRESOLVED,
    Lineage,
    LineageEntry,
    RenderRoom,
    RoomFacts,
    room_key,
)
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.infra.sql.schema import SOCIAL_ITEM
from nora.memory.adapters.retrieval_rows import graph_rows
from nora.memory.domain.retrieval import ReadingRoom, RetrievalScope, RetrievalUnavailableError


def _rooms(room: RoomFacts) -> frozenset[str]:
    return room.ids | {room_key(room.platform, ref) for ref in room.ids}


class RetrievalAccess:
    def __init__(self, access: IdentityAccess, lineage: LineagePolicy) -> None:
        self.access = access
        self._lineage = replace(lineage, mode="on", _access=access)

    async def authority(
        self, room: ReadingRoom, concerns: frozenset[str], as_of: datetime | None,
    ) -> tuple[RenderRoom, RetrievalScope, frozenset[str]]:
        try:
            rendered = await self._lineage.read_room(
                platform=room.platform, conversation_id=room.conversation_id, live=room.live,
            )
            here = rendered.room
            if (
                here is None or not here.unit
                or (room.expected_unit is not None and room.expected_unit != here.unit)
                or (here.persons is None and (rendered.live is None or not rendered.live.available))
            ):
                raise RetrievalUnavailableError("reading_room_unavailable")
            rooms = tuple(r for r in rendered.rooms if r.unit == here.unit)
            direct = {platform: await self.access.direct_addresses(platform)
                      for platform in {r.platform for r in rooms}}
            dm_rooms = frozenset(
                alias for r in rooms if r.ids & direct[r.platform] for alias in _rooms(r)
            )
            live = rendered.live
            people = concerns | (here.persons or frozenset())
            if live is not None:
                people |= live.person_ids
            audience = frozenset(rendered.canonical(person) for person in people)
            if self.access.is_file_backend:
                audience |= frozenset(
                    alias for person in audience for alias in self.access.former_ids_for(person)
                )
            else:
                audience |= frozenset(
                    alias for alias in rendered.aliases if rendered.canonical(alias) in audience
                )
            key = room_key(here.platform, here.primary_id or room.conversation_id)
            room_keys = {
                alias: room_key(r.platform, r.primary_id)
                for r in rooms if r.primary_id for alias in _rooms(r)
            }
            scope = RetrievalScope(
                here.unit, key, key in dm_rooms, audience, frozenset(), as_of, room_keys,
                audience_complete=not room.require_live_for_exclusions or (
                    here.persons is not None and live is not None and live.available
                    and not live.degraded and not live.unnamed
                ),
            )
            return rendered, scope, dm_rooms
        except RetrievalUnavailableError:
            raise
        except Exception as exc:
            raise RetrievalUnavailableError("access_view_unavailable") from exc

    async def social_opt_outs(
        self, conn: AsyncConnection, scope: RetrievalScope,
    ) -> frozenset[str]:
        if scope.as_of is None:
            social = SOCIAL_ITEM.c
            statement = sa.select(social.person).distinct().where(
                social.unit == scope.unit, visible_to_reads(SOCIAL_ITEM),
            )
        else:
            past = graph_rows(scope.unit, scope.as_of, sources=("social_item",))
            statement = sa.select(past.c.image["person"].astext).distinct()
        social_subjects = tuple(await conn.scalars(statement))
        try:
            return await self.access.social_off_ids(social_subjects)
        except Exception as exc:
            raise RetrievalUnavailableError("access_view_unavailable") from exc


def _row_scope(
    rows: FromClause, scope: RetrievalScope, *,
    reading_aliases: frozenset[str], dm_rooms: frozenset[str],
) -> sa.ColumnElement[bool]:
    row = rows.c
    exclusions = row.image["excluded_identities"]
    raw_dm = sa.or_(
        sa.func.coalesce(row.origin_room.in_(dm_rooms), False),
        row.image["conversation_type"].astext.is_not_distinct_from("dm"),
    )
    excluded = exclusions.bool_op("?|")(array(sorted(scope.audience), type_=sa.Text))
    if not scope.audience_complete:
        excluded = exclusions != sa.literal([], type_=pg.JSONB)
    opted_out = row.image["person"].astext.in_(scope.social_off)
    return sa.and_(
        row.unit == scope.unit,
        row.exposure != "operator",
        row.state != "hidden",
        ~excluded,
        sa.or_(row.kind != "item", ~raw_dm, sa.and_(
            sa.literal(scope.is_dm), row.origin_room.in_(reading_aliases),
        )),
        sa.or_(row.source != "social_item", ~opted_out),
    )


def _permits(
    raw: Any, target: RenderRoom, *, origin: str | None, source: str,
    dm_rooms: frozenset[str], reading_room: str,
) -> bool:
    lineage = Lineage.from_json(raw, origin=origin)
    if raw in ({}, []):
        return True
    if not lineage.sections:
        return section_permits((), target, origin=origin)
    if source != "claim" or origin not in dm_rooms:
        return all(
            section_permits(entries, target, origin=origin) for _, entries in lineage.sections
        )

    def claim_entry_permits(entry: LineageEntry) -> bool:
        if entry.origin in dm_rooms and entry.kind in (MESSAGE, UNRESOLVED):
            # A claim may be read across a DM boundary while its evidence stays in the DM.
            return entry_permits(replace(entry, origin=reading_room), target, origin=reading_room)
        return entry_permits(entry, target, origin=origin)

    return all(
        all(claim_entry_permits(entry) for entry in entries)
        if entries else section_permits(entries, target, origin=origin)
        for _, entries in lineage.sections
    )


async def scoped_rows(
    conn: AsyncConnection, scope: RetrievalScope, target: RenderRoom, dm_rooms: frozenset[str],
    *, ceiling: datetime | None = None, rows: FromClause | None = None,
) -> FromClause:
    here = target.room
    if here is None:
        raise RetrievalUnavailableError("reading_room_unavailable")
    aliases = _rooms(here)
    if rows is None:
        rows = graph_rows(scope.unit, scope.as_of, source_clock=ceiling)
    row = rows.c
    base = _row_scope(rows, scope, reading_aliases=aliases, dm_rooms=dm_rooms)
    if ceiling is not None:
        base = sa.and_(base, row.event_time <= ceiling)
    details = (await conn.execute(sa.select(
        row.lineage, row.origin_room, row.source,
    ).where(base).distinct())).all()
    permits = [
        {"lineage": lineage, "origin_room": origin, "source": source}
        for lineage, origin, source in details
        if _permits(lineage, target, origin=origin, source=source,
                    dm_rooms=dm_rooms, reading_room=scope.room)
    ]
    if not permits:
        return sa.select(rows).where(sa.false()).cte("scoped_graph")
    permitted = sa.func.jsonb_to_recordset(sa.literal(permits, type_=pg.JSONB)).table_valued(
        sa.column("lineage", pg.JSONB), sa.column("origin_room", sa.Text),
        sa.column("source", sa.Text),
    ).render_derived(name="permitted_lineage", with_types=True)
    return sa.select(rows).where(base, sa.exists(sa.select(1).where(
        permitted.c.lineage.is_not_distinct_from(row.lineage),
        permitted.c.origin_room.is_not_distinct_from(row.origin_room),
        permitted.c.source == row.source,
    ))).cte("scoped_graph")
