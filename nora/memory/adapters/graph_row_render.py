"""Render scoped Cognitive Graph rows consistently across presentations."""

import json
from collections.abc import Mapping, Sequence, Set
from datetime import datetime

from nora.agent.egress import LOCATION_WITHHELD, mask_coordinates, text_fragments
from nora.domain.blocks import RowStyle, render_rows
from nora.domain.lineage import RoomFacts
from nora.domain.prompt import age
from nora.memory.app.act_scope import remember_internal
from nora.memory.domain.presentations import OpenIntention
from nora.memory.domain.retrieval import RetrievedRow


def _age(at: datetime | None, now: datetime) -> str:
    return "unknown" if at is None else age(int(at.timestamp() * 1000), now)


_COORD_KEYS = frozenset({"lat", "lng", "latitude", "longitude", "lon"})


def _without_coordinates(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: LOCATION_WITHHELD if str(key).casefold() in _COORD_KEYS
            else _without_coordinates(member)
            for key, member in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_without_coordinates(member) for member in value]
    return value


def _content(value: object) -> str:
    if value is None or value == {}:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(_without_coordinates(value), ensure_ascii=False, default=str)


def _item(row: RetrievedRow, _now: datetime) -> tuple[str, ...]:
    return (f"source={row.source}", _content(row.content))


def _claim(row: RetrievedRow, now: datetime) -> tuple[str, ...]:
    support = row.support
    evidence = (
        "support=unknown" if support is None else
        f"support={support.count}, last evidenced={_age(support.last_evidenced_at, now)}"
    )
    if row.part == "lesson":
        tally = row.lesson_tally
        evidence = (
            f"support={tally.support}, contradiction={tally.contradiction}"
            if tally is not None else "support=unknown, contradiction=unknown"
        )
    staleness = ""
    if row.part == "situation":
        count = row.items_since if row.items_since is not None else "unknown"
        staleness = f"items_since={count}"
    return (f"part={row.part}", evidence, staleness, _content(row.content))


def _intention(row: RetrievedRow, _now: datetime) -> tuple[str, ...]:
    conditions = {key: value for key, value in row.close_when.items() if value is not None}
    if isinstance(row.content, dict) and row.content.get("holds_while") is not None:
        conditions["holds_while"] = row.content["holds_while"]
    return (f"close when={_content(conditions) or 'unknown'}",)


def _entity(row: RetrievedRow, _now: datetime) -> tuple[str, ...]:
    if row.pointer_kind is not None:
        return (f"pointer={row.pointer_kind}",)
    return (_content(row.content),)


def _aggregate(row: RetrievedRow, _now: datetime) -> tuple[str, ...]:
    return (_content(row.content),)


def _edge(row: RetrievedRow, _now: datetime) -> tuple[str, ...]:
    if not isinstance(row.content, dict):
        return ()
    return (
        f"type={row.content['type']}",
        f"source={row.content['src_id']}",
        f"target={row.content['dst_id']}",
    )


_DETAILS = {
    "item": _item, "claim": _claim, "intention": _intention,
    "entity": _entity, "aggregate": _aggregate, "edge": _edge,
}


def _line(
    row: RetrievedRow, now: datetime, room: RoomFacts, dm_rooms: Set[str],
    show_distance: bool, due: Set[str],
) -> str:
    basis = row.basis
    if basis == "inferred":
        basis = f"{basis} ({row.reasoning}, {row.confidence} confidence)"
    people = ", ".join(f"{person.role}={person.id or 'unknown'}" for person in row.persons)
    edges = ", ".join(
        f"{edge.type} to {edge.target}" if edge.source == row.graph_id
        else f"{edge.type} from {edge.source}"
        for edge in row.edges
    )
    citations = ", ".join(
        f"{handle.graph_id} (deleted-source)" if handle.deleted_source else handle.graph_id
        for handle in row.evidence
    )
    dm_origin = row.origin_room in dm_rooms and not room.is_room(row.origin_room)
    details = _DETAILS[row.kind](row, now)
    if row.exposure == "internal":
        for text in (*text_fragments(row.content), _content(row.content), *details):
            remember_internal(mask_coordinates(text))
    return mask_coordinates(" | ".join(field for field in (
        row.graph_id,
        f"basis={basis}",
        f"occurred={_age(row.occurred_at, now)}",
        f"asserted={_age(row.asserted_at, now)}",
        f"state={row.state}",
        "exposure=internal" if row.exposure == "internal" else "",
        "due" if row.graph_id in due else "",
        f"distance={row.distance}" if show_distance else "",
        f"people={people or 'none'}",
        f"edges={edges or 'none'}",
        f"citations={citations}" if citations else "",
        "origin=DM" if dm_origin else "",
        f"unavailable={row.unavailable}" if row.unavailable else "",
        *details,
    ) if field))


def render_graph_rows(
    rows: Sequence[RetrievedRow], *, now: datetime, reading_room: RoomFacts, dm_rooms: Set[str],
    show_distance: bool = False, due: Set[str] = frozenset(),
) -> str:
    """Render the rows selected for the reading room, with ages against the pass clock."""
    return render_rows(
        rows, style=RowStyle(), speaker=lambda row: row.kind,
        body=lambda row: _line(row, now, reading_room, dm_rooms, show_distance, due),
    )


def render_graph_row(
    row: RetrievedRow, *, now: datetime, reading_room: RoomFacts, dm_rooms: Set[str],
) -> str:
    return render_graph_rows((row,), now=now, reading_room=reading_room, dm_rooms=dm_rooms)


def render_open_intention(
    entry: OpenIntention, *, now: datetime, reading_room: RoomFacts, dm_rooms: Set[str],
) -> str:
    """Render an intention with the due judgment made by Standing."""
    due = frozenset({entry.row.graph_id}) if entry.due else frozenset()
    return render_graph_rows(
        (entry.row,), now=now, reading_room=reading_room, dm_rooms=dm_rooms, due=due,
    )


def render_related_rows(
    rows: Sequence[RetrievedRow], *, now: datetime, reading_room: RoomFacts, dm_rooms: Set[str],
) -> str:
    """Render each related row with its distance from an anchor."""
    return render_graph_rows(
        rows, now=now, reading_room=reading_room, dm_rooms=dm_rooms, show_distance=True,
    )
