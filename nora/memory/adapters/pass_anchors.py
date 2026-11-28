"""Compile each pass's prefetch seeds with scoped, lookup-only graph reads."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import FromClause

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.domain.graph_registration import DEPLOYED_ROLLUP_PERSONS
from nora.domain.lineage import RoomFacts, split_room
from nora.domain.managed import room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.memory.adapters.retrieval_rows import graph_rows
from nora.memory.adapters.retrieval_scope import RetrievalAccess, scoped_rows
from nora.memory.adapters.review_anchors import review_anchors
from nora.memory.domain.graph import PointerKind
from nora.memory.domain.pass_declarations import PassDeclaration
from nora.memory.domain.pass_inputs import (
    AmbientInput,
    CalledInput,
    DebriefInput,
    PassInput,
    PassScope,
    ProactiveInput,
    ReviewCursor,
    ReviewInput,
    WakeInput,
)
from nora.memory.domain.presentations import EntityAnchor
from nora.memory.domain.retrieval import (
    ReadingRoom,
    RetrievalAnchors,
    RetrievalScope,
    RetrievalUnavailableError,
)

_TIME = sa.DateTime(timezone=True)


@dataclass(frozen=True, slots=True)
class AnchorRead:
    anchors: RetrievalAnchors
    room: RoomFacts
    room_ref: str
    room_entity: str
    dm_rooms: frozenset[str]
    concerns: frozenset[str]
    collective: tuple[EntityAnchor, ...]
    people: tuple[EntityAnchor, ...]
    names: Mapping[str, str]
    cause: str | None = None
    review_due: tuple[str, ...] = ()
    review_evidence: tuple[tuple[str, str], ...] = ()
    review_next: ReviewCursor | None = None
    review_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class _Seeds:
    ids: tuple[str, ...] = ()
    rooms: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    query: str = ""
    review_due: tuple[str, ...] = ()
    review_evidence: tuple[tuple[str, str], ...] = ()
    review_next: ReviewCursor | None = None
    review_since: datetime | None = None


class SqlPassAnchors:
    def __init__(self, engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy) -> None:
        self._engine = engine
        self._authority = RetrievalAccess(access, lineage)

    async def profile_subjects(
        self, reading: ReadingRoom, subjects: tuple[tuple[PointerKind, str], ...],
        *, concerns: frozenset[str], as_of: datetime | None, ceiling: datetime | None,
    ) -> tuple[RoomFacts, frozenset[str], dict[tuple[PointerKind, str], EntityAnchor]]:
        target, authority, dm_rooms = await self._authority.authority(reading, concerns, as_of)
        if target.room is None:
            raise RetrievalUnavailableError("reading_room_unavailable")
        rooms = tuple(
            ref for kind, ref in subjects if kind == "room" and ref in authority.room_keys
        )
        people = tuple(ref for kind, ref in subjects if kind == "identity")
        try:
            async with self._engine.connect() as raw:
                conn = await raw.execution_options(isolation_level="REPEATABLE READ")
                entities = graph_rows(authority.unit, as_of, sources=("entity",))
                rows = await scoped_rows(
                    conn, authority, target, dm_rooms, rows=entities, ceiling=ceiling,
                )
                pointers = await _AnchorQueries(conn, rows, authority).pointers(
                    tuple(dict.fromkeys(authority.room_keys[ref] for ref in rooms)), people,
                )
        except sa.exc.SQLAlchemyError as exc:
            raise RetrievalUnavailableError("anchor_read_failed") from exc
        anchors = {}
        for kind, ref in subjects:
            canonical = authority.room_keys.get(ref, "") if kind == "room" else ref
            anchors[kind, ref] = EntityAnchor(pointers.get((kind, canonical), ""), canonical)
        return target.room, dm_rooms, anchors

    async def build(
        self, declaration: PassDeclaration, data: PassInput, scope: PassScope, now: datetime,
        *, ceiling: datetime | None = None,
    ) -> AnchorRead:
        if not isinstance(data, declaration.input_type):
            raise TypeError(f"expected {declaration.input_type.__name__}")
        reading = scope.room
        room = RoomFacts(reading.platform, frozenset(), scope.unit, None)
        room_ref = "room"
        names = {"nora": scope.nora_name}
        dm_rooms: frozenset[str] = frozenset()
        try:
            if scope.anchor_failure is not None:
                raise RetrievalUnavailableError(scope.anchor_failure)
            target, authority, dm_rooms = await self._authority.authority(
                reading, scope.concerns, scope.as_of,
            )
            if target.room is None or authority.unit != scope.unit:
                raise RetrievalUnavailableError("reading_room_unavailable")
            room, room_ref = target.room, authority.room
            names = {
                "nora": scope.nora_name,
                room_ref: room.label or (room.primary_id or reading.conversation_id),
                room.unit: scope.unit_name or room.unit,
            }
            async with self._engine.connect() as raw:
                conn = await raw.execution_options(isolation_level="REPEATABLE READ")
                rows = await scoped_rows(conn, authority, target, dm_rooms, ceiling=ceiling)
                queries = _AnchorQueries(conn, rows, authority)
                seeds = await queries.build(data, now)
                if isinstance(data, (CalledInput, AmbientInput, ProactiveInput)):
                    standing = await queries.open_standing(limit=declaration.window[2] + 1)
                    seeds = replace(seeds, ids=tuple(dict.fromkeys((*seeds.ids, *standing))))
                people = await self._identity_people(seeds.people, authority)
                rooms = tuple(dict.fromkeys(
                    authority.room_keys[ref] for ref in seeds.rooms if ref in authority.room_keys
                ))
                pointers = await queries.pointers(rooms, tuple(people))
            names.update(people)
        except (RetrievalUnavailableError, sa.exc.SQLAlchemyError) as exc:
            return AnchorRead(
                RetrievalAnchors(), room, room_ref, "", dm_rooms, scope.concerns,
                _collective(room_ref, room.unit, {}), (), names,
                str(exc) if isinstance(exc, RetrievalUnavailableError) else "anchor_read_failed",
            )

        id_for = pointers.get
        people_subjects = tuple(
            EntityAnchor(pointers["identity", identity], identity) for identity in sorted(people)
            if ("identity", identity) in pointers
        )
        pointer_seeds = (
            *(id_for(("room", ref)) for ref in rooms),
            *(subject.graph_id for subject in people_subjects),
        )
        ids = tuple(dict.fromkeys((*seeds.ids, *(key for key in pointer_seeds if key))))
        return AnchorRead(
            RetrievalAnchors(graph_ids=ids, query=seeds.query), room, room_ref,
            id_for(("room", room_ref), ""), dm_rooms, scope.concerns | frozenset(people),
            _collective(room_ref, room.unit, pointers), people_subjects, names,
            review_due=seeds.review_due, review_evidence=seeds.review_evidence,
            review_next=seeds.review_next, review_since=seeds.review_since,
        )

    async def _identity_people(
        self, people: tuple[str, ...], authority: RetrievalScope,
    ) -> dict[str, str]:
        names: dict[str, str] = {}
        platform, room = split_room(authority.room)
        if platform is None:
            raise RetrievalUnavailableError("reading_room_unavailable")
        room_scope = Scope(ScopeKind.ROOM, room_scope_id(platform, room))
        try:
            for ref in dict.fromkeys(people):
                person = await self._authority.access.person_ref(ref, scope=room_scope)
                if person is not None:
                    names[person.person_id] = person.name
        except Exception as exc:
            raise RetrievalUnavailableError("anchor_identity_unavailable") from exc
        return names


def _collective(
    room: str, unit: str, pointers: Mapping[tuple[str, str], str],
) -> tuple[EntityAnchor, ...]:
    return tuple(
        EntityAnchor(pointers.get((kind, ref), ""), ref)
        for kind, ref in (("nora", "nora"), ("room", room), ("unit", unit))
    )


class _AnchorQueries:
    def __init__(self, conn: AsyncConnection, rows: FromClause, scope: RetrievalScope) -> None:
        self._conn = conn
        self._graph = rows
        self._rows = rows.c
        self._scope = scope

    async def build(self, data: PassInput, now: datetime) -> _Seeds:
        match data:
            case CalledInput():
                return self.called(data)
            case AmbientInput():
                return self.ambient(data)
            case ProactiveInput():
                return await self.proactive(data)
            case WakeInput():
                return await self.wake(data)
            case DebriefInput():
                return await self.debrief(data)
            case ReviewInput():
                return await self.review(data, now)

    def called(self, data: CalledInput) -> _Seeds:
        ids = tuple(ref for ref in (data.item.id, data.quotes) if ref is not None)
        return _Seeds(ids, (self._scope.room,), data.item.people, data.item.text)

    def ambient(self, data: AmbientInput) -> _Seeds:
        return _Seeds(
            tuple(item.id for item in data.items if item.id is not None), (self._scope.room,),
            tuple(person for item in data.items for person in item.people),
            "\n".join(item.text for item in data.items),
        )

    async def proactive(self, data: ProactiveInput) -> _Seeds:
        if data.event is not None:
            return self.called(CalledInput(data.event))
        if data.intention is None:
            raise ValueError("A proactive pass needs an intention or an event")
        r = self._rows
        row = (await self._conn.execute(sa.select(
            r.origin_room, r.image["identities"],
        ).where(r.graph_id == data.intention, r.kind == "intention"))).one_or_none()
        links = await self._conn.scalars(sa.select(r.image["dst_id"].astext).where(
            r.kind == "edge", r.state == "live", r.image["src_id"].astext == data.intention,
            r.image["type"].astext.in_(("rests_on", "serves")),
        ).order_by(r.image["type"].astext, r.image["dst_id"].astext))
        return _Seeds(
            (data.intention, *links),
            (row[0] or self._scope.room,) if row is not None else (self._scope.room,),
            tuple(person for person in row[1] if person) if row is not None else (),
        )

    async def wake(self, data: WakeInput) -> _Seeds:
        stimuli = tuple(row for row in data.stimuli if row.unit == self._scope.unit)
        refs = tuple(dict.fromkeys(
            ref for row in stimuli for ref in (row.graph_id, row.revision_id, *row.dependents)
            if ref is not None
        ))
        r = self._rows
        rows = await self._conn.execute(sa.select(
            r.graph_id, r.kind, r.origin_room, sa.case(
                (r.source.in_(DEPLOYED_ROLLUP_PERSONS), r.image["persons"]),
                else_=r.image["identities"],
            ),
        ).where(r.graph_id.in_(refs)))
        visible = {graph_id: (kind, room, people) for graph_id, kind, room, people in rows}
        claimed_ids = {stimulus.graph_id for stimulus in stimuli}
        ids = tuple(
            ref for ref in refs if ref in visible
            and (ref in claimed_ids or visible[ref][0] in ("item", "intention"))
        )
        links = await self._touched_intentions(tuple(visible))
        earlier = await self._earlier_verdicts({
            stimulus.graph_id for stimulus in stimuli if stimulus.kind == "verdict"
        })
        return _Seeds(
            tuple(dict.fromkeys((*ids, *earlier, *links))),
            (*tuple(row.room for row in stimuli if row.room is not None),
             *tuple(visible[ref][1] for ref in ids if visible[ref][1] is not None)),
            (*tuple(row.identity for row in stimuli if row.identity is not None),
             *tuple(identity for ref in ids for identity in (visible[ref][2] or ()) if identity)),
        )

    async def _earlier_verdicts(self, claimed: set[str]) -> tuple[str, ...]:
        r = self._rows
        earlier = self._graph.alias("earlier_verdicts").c
        time = r.image["ended_at"].astext.cast(_TIME)
        earlier_time = earlier.image["ended_at"].astext.cast(_TIME)
        same_class = tuple(
            earlier.image[key].astext.is_not_distinct_from(r.image[key].astext)
            for key in ("form", "owed_to", "condition_form")
        )
        previous = sa.select(earlier.graph_id).where(
            earlier.source == "intention_verdict", earlier_time < time, *same_class,
        ).order_by(earlier_time.desc(), earlier.graph_id).limit(10).correlate(
            self._graph,
        ).lateral()
        stmt = sa.select(previous.c.graph_id).select_from(
            self._graph.join(previous, sa.true()),
        ).where(r.source == "intention_verdict", r.graph_id.in_(claimed))
        return tuple(await self._conn.scalars(stmt)) if claimed else ()

    async def _touched_intentions(self, refs: tuple[str, ...]) -> tuple[str, ...]:
        r = self._rows
        link = self._graph.alias("stimulus_links").c
        src, dst = link.image["src_id"].astext, link.image["dst_id"].astext
        return tuple(await self._conn.scalars(sa.select(r.graph_id).where(
            r.kind == "intention", r.state == "open", sa.exists(sa.select(1).where(
                link.kind == "edge", link.state == "live",
                sa.or_(sa.and_(src == r.graph_id, dst.in_(refs)),
                       sa.and_(dst == r.graph_id, src.in_(refs))),
            )),
        ).order_by(r.graph_id)))

    async def debrief(self, data: DebriefInput) -> _Seeds:
        return _Seeds((*data.items, *await self.open_standing()), (self._scope.room,))

    async def open_standing(self, *, limit: int | None = None) -> tuple[str, ...]:
        r = self._rows
        aliases = tuple(ref for ref, key in self._scope.room_keys.items()
                        if key == self._scope.room)
        ids = await self._conn.scalars(sa.select(r.graph_id).where(
            r.kind == "intention", r.state == "open",
            sa.or_(r.origin_room.is_(None), r.origin_room.in_(aliases)),
        ).order_by(r.graph_id).limit(limit))
        return tuple(ids)

    async def review(self, data: ReviewInput, now: datetime) -> _Seeds:
        aliases = tuple(ref for ref, key in self._scope.room_keys.items()
                        if key == self._scope.room)
        due, evidence, cursor, since = await review_anchors(
            self._conn, self._graph, self._scope.unit, data, now, aliases,
        )
        return _Seeds((*due, *(ref for ref, _ in evidence)), review_due=due,
                      review_evidence=evidence, review_next=cursor, review_since=since)

    async def pointers(
        self, rooms: tuple[str, ...], people: tuple[str, ...],
    ) -> dict[tuple[str, str], str]:
        r = self._rows
        kind = r.image["pointer_kind"].astext
        ref = r.image["pointer_ref"].astext
        rows = await self._conn.execute(sa.select(
            kind.label("pointer_kind"), ref.label("pointer_ref"), r.graph_id,
        ).where(
            r.kind == "entity", r.state == "admitted", sa.or_(
                sa.and_(kind == "identity", ref.in_(people)),
                sa.and_(kind == "room", ref.in_((*rooms, self._scope.room))),
                sa.and_(kind == "unit", ref == self._scope.unit),
                sa.and_(kind == "nora", ref == "nora"),
            ),
        ).order_by(r.graph_id.desc()))
        return {(kind, ref): graph_id for kind, ref, graph_id in rows}
