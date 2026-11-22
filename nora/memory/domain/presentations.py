"""Select presentations from an already scoped graph read."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nora.domain.lineage import split_room
from nora.domain.ticks import due_tick_of_at
from nora.memory.domain.graph import ClaimPart
from nora.memory.domain.retrieval import (
    RetrievalLimit,
    RetrievalResult,
    RetrievalStatus,
    RetrievedRow,
)
from nora.memory.domain.time_trigger import aware_instant, decode_time_trigger

_PROFILE_PARTS: tuple[ClaimPart, ...] = ("fact", "trait", "guess")
_INFERENCE_ORDER: Mapping[str | None, int] = {"deduced": 2, "induced": 3, "abduced": 4}
_CLOSED = frozenset({"ended", "retired", "superseded", "tombstoned"})
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _recency(row: RetrievedRow) -> timedelta:
    return _EPOCH - (row.occurred_at or row.asserted_at)


def order_rows(
    rows: Iterable[RetrievedRow], *, due: Collection[str] = (),
) -> tuple[RetrievedRow, ...]:
    """Order presentation rows by their distance, lifecycle, event time and id."""
    def key(row: RetrievedRow) -> tuple[int, int, timedelta, str]:
        is_due = row.kind == "intention" and row.state == "open" and row.graph_id in due
        bucket = 2 if row.state in _CLOSED else 0 if is_due else 1
        return row.distance, bucket, _recency(row), row.graph_id

    return tuple(sorted(rows, key=key))


@dataclass(frozen=True, slots=True)
class EntityAnchor:
    """The retrieved entity's graph id and its pointer reference, if any."""

    graph_id: str
    pointer_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Profile:
    name: str | None
    parts: tuple[tuple[ClaimPart, tuple[RetrievedRow, ...]], ...]
    status: RetrievalStatus
    limits: tuple[RetrievalLimit, ...]
    cause: str | None


@dataclass(frozen=True, slots=True)
class Like:
    claim: RetrievedRow
    topic: RetrievedRow
    item_sources: tuple[str, ...]
    personal_response: bool


def _about(row: RetrievedRow) -> tuple[str, ...]:
    return _outgoing(row, "about")


def _outgoing(row: RetrievedRow, kind: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        edge.target for edge in row.edges if edge.type == kind and edge.source == row.graph_id
    ))


def _cited_items(
    row: RetrievedRow, items: Mapping[str, RetrievedRow],
) -> tuple[RetrievedRow, ...]:
    return tuple(
        items[cite.graph_id] for cite in row.evidence
        if not cite.deleted_source and cite.graph_id in items
    )


def _own_statement(
    row: RetrievedRow, items: tuple[RetrievedRow, ...], people: Collection[str],
) -> bool:
    return row.reasoning == "told" and any(
        author.role == "author" and author.id in people
        for item in items for author in item.persons
    )


def _order(row: RetrievedRow, items: Mapping[str, RetrievedRow]) -> tuple[int, bool, float]:
    if row.reasoning == "told":
        subjects = {
            person.id for person in row.persons
            if person.role == "subject" and person.id is not None
        }
        precedence = int(not _own_statement(row, _cited_items(row, items), subjects))
    else:
        precedence = _INFERENCE_ORDER.get(row.reasoning, 4)
    if row.part == "trait" and row.support is not None:
        return precedence, False, -row.support.last_evidenced_at.timestamp()
    return precedence, row.part == "trait", -row.asserted_at.timestamp()


def _profile_name(
    subject: EntityAnchor, rows: Mapping[str, RetrievedRow], names: Mapping[str, str],
) -> str | None:
    if subject.pointer_ref is not None:
        return names.get(subject.pointer_ref)
    entity = rows.get(subject.graph_id)
    if entity is not None and entity.pointer_kind is None and isinstance(entity.content, dict):
        name = entity.content.get("name")
        return name if isinstance(name, str) else None
    return None


def profile(result: RetrievalResult, entity: EntityAnchor, names: Mapping[str, str]) -> Profile:
    """Read the entity's three profile parts and keep the retrieval status."""
    rows = {row.graph_id: row for row in result.rows}
    items = {key: row for key, row in rows.items() if row.kind == "item"}
    claims = [
        row for row in result.rows if row.kind == "claim" and row.part in _PROFILE_PARTS
        and entity.graph_id in _about(row)
    ]
    parts = tuple(
        (part, tuple(sorted((row for row in claims if row.part == part),
                            key=lambda row: _order(row, items))))
        for part in _PROFILE_PARTS
    )
    status = "nothing_found" if result.status == "ok" and not claims else result.status
    return Profile(_profile_name(entity, rows, names), parts, status, result.limits, result.cause)


def likes(result: RetrievalResult, person: EntityAnchor) -> tuple[Like, ...]:
    """Read a person's traits about things and identify their cited evidence."""
    items = {row.graph_id: row for row in result.rows if row.kind == "item"}
    topics = {row.graph_id: row for row in result.rows
              if row.kind == "entity" and row.pointer_kind is None}
    subject = (person.pointer_ref,) if person.pointer_ref is not None else ()
    entries: list[Like] = []
    for row in result.rows:
        if row.kind != "claim" or row.part != "trait":
            continue
        about = _about(row)
        if person.graph_id not in about:
            continue
        evidence = _cited_items(row, items)
        sources = tuple(sorted({item.source for item in evidence if item.source is not None}))
        own = _own_statement(row, evidence, subject)
        entries.extend(Like(row, topics[key], sources, own) for key in about if key in topics)
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class OpenIntention:
    row: RetrievedRow
    due: bool
    children: tuple[OpenIntention, ...]


@dataclass(frozen=True, slots=True)
class SettledIntention:
    row: RetrievedRow
    ending: str | None
    closed_by: str | None


@dataclass(frozen=True, slots=True)
class Situation:
    row: RetrievedRow
    age: timedelta
    items_since: int | None


@dataclass(frozen=True, slots=True)
class Standing:
    open: tuple[OpenIntention, ...]
    settled: tuple[SettledIntention, ...]
    situation: tuple[Situation, ...]
    status: RetrievalStatus
    limits: tuple[RetrievalLimit, ...]
    cause: str | None


def intention_is_due(row: RetrievedRow, now: datetime) -> bool:
    """Evaluate the intention's next unpublished occurrence against the pass clock."""
    if row.kind != "intention" or row.state != "open":
        return False
    for key in ("deadline", "review_time"):
        at = aware_instant(row.close_when.get(key))
        if at is not None and at <= now:
            return True
    published = row.close_when.get("published_occurrences")
    trigger = decode_time_trigger(row.close_when.get("trigger"))
    if not isinstance(published, int) or trigger is None:
        return False
    occurrence = next(trigger.due_occurrences(due_tick_of_at(now), published), None)
    return occurrence is not None and occurrence <= now


def _in_room(row: RetrievedRow, room: str) -> bool:
    if row.origin_room is None:
        return False
    platform, key = split_room(room)
    origin_platform, origin_key = split_room(row.origin_room)
    return key == origin_key and origin_platform in (None, platform)


def _open_intentions(
    intentions: tuple[RetrievedRow, ...], due: Collection[str],
) -> tuple[OpenIntention, ...]:
    open_ids = {row.graph_id for row in intentions if row.state == "open"}
    children: dict[str, list[RetrievedRow]] = {}
    roots = []
    for row in intentions:
        parents = open_ids.intersection(_outgoing(row, "serves"))
        for parent in parents:
            children.setdefault(parent, []).append(row)
        if not parents and row.graph_id in open_ids:
            roots.append(row)
    shown: set[str] = set()

    def branch(row: RetrievedRow, path: frozenset[str]) -> OpenIntention:
        shown.add(row.graph_id)
        path = path | {row.graph_id}
        nested = tuple(
            branch(child, path) for child in children.get(row.graph_id, ())
            if child.graph_id not in path
        )
        return OpenIntention(row, row.graph_id in due, nested)

    groups = [branch(row, frozenset()) for row in roots]
    for row in intentions:
        if row.graph_id in open_ids and row.graph_id not in shown:
            groups.append(branch(row, frozenset()))
    roots_by_id = {group.row.graph_id: group for group in groups}
    return tuple(
        roots_by_id[row.graph_id] for row in intentions if row.graph_id in roots_by_id
    )


def standing(
    result: RetrievalResult, room: str, room_entity: str, now: datetime,
    *, open_room_and_unit: bool = False,
) -> Standing:
    """Read the room's open intentions, recent endings and situation claims."""
    room_intentions = tuple(
        row for row in result.rows if row.kind == "intention" and (
            (_in_room(row, room) or row.origin_room is None) and row.state == "open"
            if open_room_and_unit else _in_room(row, room)
        )
    )
    due = {row.graph_id for row in room_intentions if intention_is_due(row, now)}
    intentions = order_rows(room_intentions, due=due)
    settled = []
    for row in intentions:
        ended_at = aware_instant(row.close_when.get("ended_at"))
        if ended_at is not None and result.bounds.since <= ended_at <= result.bounds.until:
            ending = row.close_when.get("ending")
            witness = next(iter(_outgoing(row, "closed_by")), None)
            settled.append(SettledIntention(
                row, ending if isinstance(ending, str) else None, witness,
            ))
    situations = () if open_room_and_unit else tuple(
        Situation(row, now - row.asserted_at, row.items_since)
        for row in order_rows(
            row for row in result.rows if row.kind == "claim" and row.part == "situation"
            and room_entity in _about(row)
        )
    )
    return Standing(
        _open_intentions(intentions, due), tuple(settled), situations,
        result.status, result.limits, result.cause,
    )


def lessons(result: RetrievalResult) -> tuple[RetrievedRow, ...]:
    """Read the unit's lesson claims, including exact references to retired lessons."""
    rows = (
        row for row in result.rows if row.kind == "claim" and row.part == "lesson"
        and row.state in ("live", "retired")
    )
    def key(row: RetrievedRow) -> tuple[bool, int, timedelta, str]:
        return (
            row.support is None, -row.support.count if row.support is not None else 0,
            _recency(row), row.graph_id,
        )

    return tuple(sorted(rows, key=key))


def related(result: RetrievalResult, shown: Collection[str]) -> tuple[RetrievedRow, ...]:
    """Read reached items and claims outside this pass's other presentations."""
    return order_rows(
        row for row in result.rows if row.kind in ("item", "claim") and row.graph_id not in shown
    )
