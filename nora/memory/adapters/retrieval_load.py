"""Load row planes and permitted evidence handles for the returned subgraph."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import FromClause

from nora.access import IdentityAccess
from nora.memory.adapters.retrieval_rows import tombstoned_ids
from nora.memory.domain.graph import ClaimPart, Kind, PointerKind
from nora.memory.domain.registration import ITEM_SOURCES, SEARCHABLE_SOURCES
from nora.memory.domain.retrieval import (
    EvidenceHandle,
    RetrievalScope,
    RetrievalUnavailableError,
    RetrievedEdge,
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
_CONDITION_FIELDS = ("done_when", "drop_when", "deadline", "review_time")
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
    if source == "intention":
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
    scope: RetrievalScope,
) -> tuple[dict[str, list[RetrievedEdge]], dict[str, list[EvidenceHandle]]]:
    edge = rows.alias("result_edge").c
    target = rows.alias("citation_target").c
    image = edge.image
    src, dst, typ = (image[key].astext for key in ("src_id", "dst_id", "type"))
    deleted = dst.in_(tombstoned_ids(scope.unit))
    evidence = (typ == "cites") & sa.or_(deleted, sa.exists(sa.select(1).where(
        target.graph_id == dst,
    )))
    links = await conn.execute(sa.select(src, dst, typ, deleted).where(
        edge.source == "edge", edge.state == "live", src.in_(selected),
        sa.or_(dst.in_(selected), evidence),
    ).order_by(src, dst, typ))
    by_graph: dict[str, list[RetrievedEdge]] = {}
    handles: dict[str, list[EvidenceHandle]] = {}
    for source, destination, kind, tombstoned in links:
        if destination in selected:
            relation = RetrievedEdge(kind, source, destination)
            by_graph.setdefault(source, []).append(relation)
            if destination != source:
                by_graph.setdefault(destination, []).append(relation)
        if kind == "cites":
            handles.setdefault(source, []).append(EvidenceHandle(destination, tombstoned))
    return by_graph, handles


def _content(source: str, image: Mapping[str, Any]) -> object:
    if source in ("claim", "nora_pass_output"):
        return image.get("content")
    if source == "edge":
        return {field: image[field] for field in ("type", "src_id", "dst_id")}
    return {name: image[name] for name in SEARCHABLE_SOURCES.get(source, ()) if name in image}


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


async def load_rows(
    conn: AsyncConnection, rows: FromClause, distances: Mapping[str, int],
    already_shown: frozenset[str], scope: RetrievalScope, access: IdentityAccess,
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
    edges, handles = await _relations(conn, rows, distances, scope)
    support = await _support(conn, rows, tuple(
        key for key, entry in found.items() if entry["kind"] == "claim"
    ))
    result = []
    for graph_id, distance in distances.items():
        entry = found[graph_id]
        image = entry["image"]
        source, kind = entry["source"], entry["kind"]
        unavailable = graph_id in tombstoned
        keys = {
            "intention": _CONDITION_FIELDS, "claim": _VALIDITY_FIELDS,
        }.get(source, ())
        result.append(RetrievedRow(
            graph_id=graph_id, kind=cast(Kind, kind), source=source if kind == "item" else None,
            basis=image["basis"], reasoning=image.get("reasoning"),
            confidence=image.get("confidence"),
            occurred_at=_instant(image.get("occurred_at")), asserted_at=entry["asserted_at"],
            valid_from=_instant(image.get("valid_from")), valid_to=_instant(image.get("valid_to")),
            state=entry["state"], origin_room=entry["origin_room"], exposure=entry["exposure"],
            persons=() if unavailable else await _people(conn, rows, access, source, image),
            edges=tuple(edges.get(graph_id, ())), evidence=tuple(handles.get(graph_id, ())),
            content=None if unavailable else _content(source, image),
            close_when={} if unavailable else {key: image.get(key) for key in keys},
            distance=distance, already_shown=graph_id in already_shown,
            unavailable="tombstoned_since" if unavailable else None,
            part=cast(ClaimPart, image["part"]) if kind == "claim" else None,
            pointer_kind=cast(PointerKind | None, image["pointer_kind"])
            if kind == "entity" else None,
            support=support.get(graph_id),
        ))
    return tuple(result)
