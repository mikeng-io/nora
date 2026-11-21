"""Load row planes and permitted evidence handles for the returned subgraph."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import FromClause

from nora.access import IdentityAccess
from nora.infra.sql.cognitive_graph_schema import GRAPH_STIMULUS
from nora.infra.sql.cognitive_graph_store import content_digest, visible_to_reads
from nora.memory.adapters.retrieval_rows import tombstoned_ids
from nora.memory.domain.graph import ClaimPart, Kind, PointerKind
from nora.memory.domain.registration import ITEM_SOURCES, SEARCHABLE_SOURCES
from nora.memory.domain.retrieval import (
    EvidenceHandle,
    RetrievalScope,
    RetrievalUnavailableError,
    RetrievedEdge,
    RetrievedLessonTally,
    RetrievedPerson,
    RetrievedRow,
    RetrievedSupport,
)

_ROLE = {
    "person_id": "author",
    "messages.person_id": "author",
    "actor_id": "author",
    "target_author_id": "target",
    "person": "subject",
    "authority_person": "author",
    "nora_act.authority_person": "author",
    "requester_person_id": "author",
    "author": "author",
}
_CONDITION_FIELDS = (
    "done_when", "drop_when", "deadline", "review_time", "trigger", "ending", "ended_at",
)
_VALIDITY_FIELDS = ("valid_to", "window_end")


def _instant(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


async def _item_person(
    conn: AsyncConnection, rows: FromClause, access: IdentityAccess,
    image: Mapping[str, Any], key: str,
) -> str | None:
    if key == "messages.person_id":
        row = rows.c
        value = await conn.scalar(sa.select(row.image["person_id"].astext).where(
            row.source == "messages",
            row.image["id"].astext == str(image.get("message_id")),
        ).limit(1))
        return cast(str | None, value)
    if key == "nora_act.authority_person":
        row = rows.c
        value = await conn.scalar(sa.select(row.image["authority_person"].astext).where(
            row.source == "nora_act", row.graph_id == image.get("act_id"),
        ))
        return cast(str | None, value)
    value = image.get(key)
    if value and key in ("actor_id", "target_author_id"):
        try:
            person = await access.resolve_ref(image["platform"], value)
        except Exception as exc:
            raise RetrievalUnavailableError("access_view_unavailable") from exc
        return person.person_id if person else None
    return cast(str | None, value)


async def _people(
    conn: AsyncConnection, rows: FromClause, access: IdentityAccess,
    source: str, image: Mapping[str, Any],
) -> tuple[RetrievedPerson, ...]:
    persons = tuple(image.get("persons") or ())
    if source == "claim":
        return tuple(RetrievedPerson(person, "subject") for person in persons)
    if source in ("intention", "intention_verdict"):
        roles = tuple(RetrievedPerson(image[key], key) for key in ("owner", "owed_to")
                      if image.get(key) is not None)
    else:
        spec = ITEM_SOURCES.get(source)
        roles = tuple([
            RetrievedPerson(await _item_person(conn, rows, access, image, key), _ROLE[key])
            for key in spec.person_columns
        ]) if spec is not None else ()
    assigned = {entry.id for entry in roles}
    return (*roles, *(RetrievedPerson(person, "participant")
                      for person in persons if person not in assigned))


async def _relations(
    conn: AsyncConnection, rows: FromClause, selected: Mapping[str, int],
    scope: RetrievalScope, about_sources: tuple[str, ...],
) -> tuple[dict[str, list[RetrievedEdge]], dict[str, list[EvidenceHandle]]]:
    edge = rows.alias("result_edge").c
    target = rows.alias("citation_target").c
    image = edge.image
    src, dst, typ = (image[key].astext for key in ("src_id", "dst_id", "type"))
    deleted = dst.in_(tombstoned_ids(scope.unit))
    visible = sa.exists(sa.select(1).where(
        target.graph_id == dst,
    ))
    evidence = (typ == "cites") & (deleted | visible)
    outgoing = typ.in_(("serves", "closed_by")) | ((typ == "about") & src.in_(about_sources))
    links = await conn.execute(sa.select(src, dst, typ, deleted).where(
        edge.source == "edge", edge.state == "live", src.in_(selected),
        sa.or_(dst.in_(selected), evidence, outgoing & visible),
    ).order_by(src, dst, typ))
    by_graph: dict[str, list[RetrievedEdge]] = {}
    handles: dict[str, list[EvidenceHandle]] = {}
    for source, destination, kind, tombstoned in links:
        if destination in selected or kind != "cites":
            relation = RetrievedEdge(kind, source, destination)
            by_graph.setdefault(source, []).append(relation)
            if destination in selected and destination != source:
                by_graph.setdefault(destination, []).append(relation)
        if kind == "cites":
            handles.setdefault(source, []).append(EvidenceHandle(destination, tombstoned))
    return by_graph, handles


def _content(source: str, image: Mapping[str, Any]) -> object:
    if source in ("claim", "nora_pass_output"):
        return image.get("content")
    if source == "edge":
        return {field: image[field] for field in ("type", "src_id", "dst_id")}
    if source == "intention_verdict":
        return {field: image[field] for field in (
            "intention_id", "ending", "ended_at", "witness_id", "form", "condition_form",
        )}
    if source == "source_health":
        return {field: image[field] for field in ("source", "scope", "event", "outcome")}
    if source == "lesson_tally":
        return {field: image[field] for field in ("claim_id", "support", "contradiction")}
    return {name: image[name] for name in SEARCHABLE_SOURCES.get(source, ()) if name in image}


async def _guess_grades(
    conn: AsyncConnection, rows: FromClause, scope: RetrievalScope, guesses: tuple[str, ...],
) -> dict[str, str]:
    if not guesses:
        return {}
    edge, grade = rows.alias("guess_grades").c, rows.alias("grade_rows").c
    dst, src = edge.image["dst_id"].astext, edge.image["src_id"].astext
    query = sa.select(dst, grade.image["grade"].astext).where(
        edge.source == "edge", edge.state == "live", edge.image["type"].astext == "grades",
        dst.in_(guesses), src == grade.graph_id, grade.source == "claim",
        grade.state == "live", grade.image["part"].astext == "grade",
    )
    if scope.as_of is not None:
        query = query.where(grade.graph_id.not_in(tombstoned_ids(scope.unit, since=scope.as_of)))
    result = await conn.execute(query.order_by(grade.asserted_at, grade.graph_id))
    grades: dict[str, str] = {}
    for id_, value in result:
        grades[id_] = value
    return grades


async def _support(
    conn: AsyncConnection, rows: FromClause, claims: tuple[str, ...],
) -> dict[str, RetrievedSupport]:
    if not claims:
        return {}
    tally = rows.alias("scoped_claim_support").c
    claim_id = tally.image["claim_id"].astext
    count = tally.image["support_count"].astext.cast(sa.Integer)
    last = tally.image["last_evidenced_at"].astext.cast(sa.DateTime(timezone=True))
    heads = await conn.execute(sa.select(claim_id, count, last).where(
        tally.source == "claim_support", claim_id.in_(claims), count > 0,
    ))
    return {key: RetrievedSupport(total, at) for key, total, at in heads}


async def _lesson_tallies(
    conn: AsyncConnection, rows: FromClause, claims: tuple[str, ...],
) -> dict[str, RetrievedLessonTally]:
    if not claims:
        return {}
    tally = rows.alias("scoped_lesson_tallies").c
    image = tally.image
    claim_id = image["claim_id"].astext
    counts = await conn.execute(sa.select(
        claim_id, image["support"].astext.cast(sa.Integer),
        image["contradiction"].astext.cast(sa.Integer),
    ).where(tally.source == "lesson_tally", claim_id.in_(claims)))
    return {key: RetrievedLessonTally(support, contradiction)
            for key, support, contradiction in counts}


async def _optional_counts(
    conn: AsyncConnection, statement: sa.Select[Any],
) -> dict[str, int] | None:
    try:
        async with conn.begin_nested():
            return dict((await conn.execute(statement)).all())
    except sa.exc.SQLAlchemyError:
        return None


async def _items_since(
    conn: AsyncConnection, rows: FromClause, claims: tuple[str, ...],
    scope: RetrievalScope, now: datetime, ceiling: datetime | None,
) -> dict[str, int] | None:
    if not claims:
        return {}
    claim = rows.alias("situation_claim").c
    item = rows.alias("items_since_claim").c
    claim_room: sa.ColumnElement[Any] = claim.origin_room
    item_room: sa.ColumnElement[Any] = item.origin_room
    if scope.room_keys:
        claim_room = sa.case(dict(scope.room_keys), value=claim_room, else_=claim_room)
        item_room = sa.case(dict(scope.room_keys), value=item_room, else_=item_room)
    until = ceiling or (min(now, scope.as_of) if scope.as_of is not None else now)
    statement = sa.select(claim.graph_id, sa.func.count(item.graph_id)).where(
        claim.graph_id.in_(claims), item.kind == "item", item_room == claim_room,
        item.event_time > claim.asserted_at, item.event_time <= until,
    ).group_by(claim.graph_id)
    return await _optional_counts(conn, statement)


async def _published_occurrences(
    conn: AsyncConnection, intentions: tuple[str, ...], scope: RetrievalScope,
    ceiling: datetime | None,
) -> dict[str, int] | None:
    if not intentions:
        return {}
    stimulus = GRAPH_STIMULUS.c
    statement = sa.select(stimulus.graph_id, sa.func.count()).where(
        stimulus.unit == scope.unit, stimulus.kind == "intention_due",
        stimulus.graph_id.in_(intentions), visible_to_reads(GRAPH_STIMULUS),
    ).group_by(stimulus.graph_id)
    as_of = ceiling or scope.as_of
    if as_of is not None:
        statement = statement.where(stimulus.recorded_at <= as_of)
    return await _optional_counts(conn, statement)


def _staleness_cause(
    origin: str | None, scope: RetrievalScope, dm_rooms: frozenset[str],
) -> str | None:
    if origin is None:
        return "items_since_unavailable"
    if origin in dm_rooms and scope.room_keys.get(origin, origin) != scope.room:
        return "items_since_out_of_scope"
    return None


def _has_time_trigger(image: Mapping[str, Any]) -> bool:
    trigger = image.get("trigger")
    return isinstance(trigger, dict) and trigger.get("kind") == "time"


async def load_rows(
    conn: AsyncConnection, rows: FromClause, distances: Mapping[str, int],
    already_shown: frozenset[str], scope: RetrievalScope, access: IdentityAccess,
    now: datetime, dm_rooms: frozenset[str], *, ceiling: datetime | None = None,
) -> tuple[RetrievedRow, ...]:
    """Read the chosen revisions and serialize their visible relations."""
    row = rows.c
    found = {
        entry["graph_id"]: entry for entry in (await conn.execute(sa.select(rows).where(
            row.graph_id.in_(distances),
        ))).mappings()
    }
    if found.keys() != distances.keys():
        raise RetrievalUnavailableError("revision_unavailable")
    tombstoned: frozenset[str] = frozenset()
    if scope.as_of is not None:
        removed = tombstoned_ids(scope.unit, since=scope.as_of).subquery()
        tombstoned = frozenset(await conn.scalars(
            sa.select(removed.c.id).where(removed.c.id.in_(distances)),
        ))
    edges, handles = await _relations(conn, rows, distances, scope, tuple(
        key for key, entry in found.items() if entry["kind"] in ("claim", "intention")
    ))
    support = await _support(conn, rows, tuple(
        key for key, entry in found.items() if entry["kind"] == "claim"
    ))
    lesson_tallies = await _lesson_tallies(conn, rows, tuple(
        key for key, entry in found.items() if entry["image"].get("part") == "lesson"
        and key not in tombstoned
    ))
    grades = await _guess_grades(conn, rows, scope, tuple(
        key for key, entry in found.items() if entry["image"].get("part") == "guess"
        and key not in tombstoned
    ))
    situations = {
        key: _staleness_cause(entry["origin_room"], scope, dm_rooms)
        for key, entry in found.items()
        if entry["source"] == "claim" and entry["image"]["part"] == "situation"
        and key not in tombstoned
    }
    counts = await _items_since(
        conn, rows, tuple(key for key, cause in situations.items() if cause is None),
        scope, now, ceiling,
    )
    scheduled = tuple(
        key for key, entry in found.items() if entry["source"] == "intention"
        and key not in tombstoned and _has_time_trigger(entry["image"])
    )
    published = await _published_occurrences(conn, scheduled, scope, ceiling)
    result = []
    for graph_id, distance in distances.items():
        entry = found[graph_id]
        image = entry["image"]
        source, kind = entry["source"], entry["kind"]
        unavailable = graph_id in tombstoned
        keys = {
            "intention": _CONDITION_FIELDS, "claim": _VALIDITY_FIELDS,
        }.get(source, ())
        close_when = {} if unavailable else {key: image.get(key) for key in keys}
        if graph_id in scheduled:
            close_when["published_occurrences"] = (
                published.get(graph_id, 0) if published is not None else None
            )
        staleness_cause = situations.get(graph_id)
        items_since = None
        if graph_id in situations and staleness_cause is None:
            if counts is None:
                staleness_cause = "items_since_unavailable"
            else:
                items_since = counts.get(graph_id, 0)
        result.append(RetrievedRow(
            graph_id=graph_id, kind=cast(Kind, kind),
            source=source if kind in ("item", "aggregate") else None,
            basis=image["basis"], reasoning=image.get("reasoning"),
            confidence=image.get("confidence"),
            occurred_at=_instant(image.get("occurred_at")), asserted_at=entry["asserted_at"],
            valid_from=_instant(image.get("valid_from")), valid_to=_instant(image.get("valid_to")),
            state=entry["state"], origin_room=entry["origin_room"], exposure=entry["exposure"],
            persons=() if unavailable else await _people(conn, rows, access, source, image),
            edges=tuple(edges.get(graph_id, ())), evidence=tuple(handles.get(graph_id, ())),
            content=None if unavailable else _content(source, image),
            close_when=close_when,
            distance=distance, already_shown=graph_id in already_shown,
            unavailable="tombstoned_since" if unavailable else None,
            part=cast(ClaimPart, image["part"]) if kind == "claim" else None,
            pointer_kind=cast(PointerKind | None, image["pointer_kind"])
            if kind == "entity" else None,
            support=support.get(graph_id), lesson_tally=lesson_tallies.get(graph_id),
            items_since=items_since, items_since_cause=staleness_cause,
            input_digest=(
                content_digest(source, image) if ceiling is not None and kind == "item" else None
            ),
            grade=None if unavailable else grades.get(graph_id, image.get("grade")),
            prediction=None if unavailable else image.get("prediction"),
        ))
    return tuple(result)
