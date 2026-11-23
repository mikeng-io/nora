"""Select profiles and likes from an already scoped graph read."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from nora.memory.domain.graph import ClaimPart
from nora.memory.domain.retrieval import (
    RetrievalLimit,
    RetrievalResult,
    RetrievalStatus,
    RetrievedRow,
)

_PROFILE_PARTS: tuple[ClaimPart, ...] = ("fact", "trait", "guess")
_INFERENCE_ORDER: Mapping[str | None, int] = {"deduced": 2, "induced": 3, "abduced": 4}


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
    return tuple(dict.fromkeys(
        edge.target for edge in row.edges if edge.type == "about" and edge.source == row.graph_id
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
