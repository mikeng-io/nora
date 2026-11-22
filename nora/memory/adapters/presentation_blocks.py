"""Render graph presentation selections as registered context blocks."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from nora.domain.blocks import ContextBlock
from nora.domain.composed import compose_body
from nora.domain.lineage import RoomFacts
from nora.memory.adapters.graph_row_render import (
    render_graph_rows,
    render_open_intention,
    render_related_rows,
)
from nora.memory.domain.presentations import EntityAnchor, OpenIntention, profile, standing
from nora.memory.domain.retrieval import RetrievalResult, RetrievedRow


def _graph_block(
    result: RetrievalResult, body: str, *, name: str,
    attrs: tuple[tuple[str, str], ...] = (),
) -> ContextBlock:
    if result.status == "unavailable":
        body = ""
        attrs += (("status", "unavailable"),)
    elif result.status == "partial":
        attrs += (("status", "partial"),)
        if result.limits:
            attrs += (("limit", ", ".join(result.limits)),)
    elif not body:
        attrs += (("status", "empty"),)
    if result.cause:
        attrs += (("cause", result.cause),)
    return ContextBlock(name=name, body=compose_body(body), attrs=attrs, when_empty="self_close")


class GraphPresentationBlocks:
    def __init__(self, now: datetime, room: RoomFacts, dm_rooms: frozenset[str]) -> None:
        self._now = now
        self._room = room
        self._dm_rooms = dm_rooms

    def _rows(self, rows: Sequence[RetrievedRow]) -> str:
        return render_graph_rows(
            rows, now=self._now, reading_room=self._room, dm_rooms=self._dm_rooms,
        )

    def due_intention(
        self, result: RetrievalResult, intention: str,
    ) -> tuple[ContextBlock, frozenset[str]]:
        head = next((row for row in result.rows if row.graph_id == intention
                     and row.state == "open" and row.unavailable is None), None)
        related = {edge.target for edge in head.edges if edge.source == intention
                   and edge.type in ("rests_on", "serves")} if head is not None else set()
        rows = () if head is None else (head, *(
            row for row in result.rows if row.graph_id in related and row.unavailable is None
        ))
        block = _graph_block(result, self._rows(rows), name="due_intention")
        return block, frozenset(row.graph_id for row in rows)

    def profile_block(
        self, result: RetrievalResult, subject: EntityAnchor, names: Mapping[str, str],
    ) -> tuple[ContextBlock, tuple[str, ...]]:
        selected = profile(result, subject, names)
        subject_id = subject.graph_id or subject.pointer_ref or ""
        attrs: tuple[tuple[str, str], ...] = (("of", subject_id),)
        if selected.name is not None:
            attrs += (("name", selected.name),)
        sections = [(part, rows) for part, rows in selected.parts if rows]
        body = "\n".join(f"{part}:\n{self._rows(rows)}" for part, rows in sections)
        block = _graph_block(result, body, name="graph_profile", attrs=attrs)
        return block, tuple(row.graph_id for _, rows in sections for row in rows)

    def standing_block(
        self, result: RetrievalResult, room: str, room_entity: str,
        *, open_room_and_unit: bool = False,
    ) -> tuple[ContextBlock, tuple[str, ...]]:
        selected = standing(result, room, room_entity, self._now,
                            open_room_and_unit=open_room_and_unit)
        rows: list[RetrievedRow] = []
        lines: list[str] = []

        def add_open(entry: OpenIntention, depth: int) -> None:
            rows.append(entry.row)
            lines.append("  " * depth + render_open_intention(
                entry, now=self._now, reading_room=self._room, dm_rooms=self._dm_rooms,
            ))
            for child in entry.children:
                add_open(child, depth + 1)

        for entry in selected.open:
            add_open(entry, 0)
        for heading, group in (
            ("Recently settled", tuple(entry.row for entry in selected.settled)),
            ("Situation", tuple(entry.row for entry in selected.situation)),
        ):
            if group:
                rows.extend(group)
                lines.extend((heading + ":", self._rows(group)))
        return (
            _graph_block(result, "\n".join(lines), name="graph_standing"),
            tuple(row.graph_id for row in rows),
        )

    def lessons_block(self, result: RetrievalResult, rows: Sequence[RetrievedRow]) -> ContextBlock:
        return _graph_block(result, self._rows(rows), name="graph_lessons")

    def related_block(self, result: RetrievalResult, rows: Sequence[RetrievedRow]) -> ContextBlock:
        return _graph_block(
            result, render_related_rows(
                rows, now=self._now, reading_room=self._room, dm_rooms=self._dm_rooms,
            ), name="related",
        )
