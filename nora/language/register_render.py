"""Shared notebook and source-object context, rendered from canonical typed inputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, tzinfo
from functools import partial
from typing import Any, Protocol

from nora.domain.blocks import BlockKey, ContextBlock, render_elements, render_group, restamp
from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import Room
from nora.domain.composed import compose_body
from nora.domain.observations import SourceEvent, parse_handle
from nora.domain.prompt import ago
from nora.language.event_render import render_event_rows
from nora.language.memo_render import render_memos
from nora.language.note_render import render_notes


class RegisterBudget(Protocol):
    @property
    def register_lines(self) -> int: ...
    @property
    def register_chars(self) -> int: ...
    @property
    def memo_lines(self) -> int: ...
    @property
    def memo_chars(self) -> int: ...


def room_key(room: Room) -> str:
    return f"{room.platform}:{room.conversation_id}"


def room_label(room: Room, labels: Mapping[str, str]) -> str:
    return f"«{labels.get(room_key(room), 'a room with no name')}»"


def when(at_ms: int, now_ms: int, tz: tzinfo) -> str:
    at = datetime.fromtimestamp(at_ms / 1000, tz=tz)
    return f"{at:%Y-%m-%d %H:%M} ({ago(now_ms - at_ms)})"


def render_register(
    rooms: Sequence[Room],
    grounding: Grounding,
    *,
    now_ms: int,
    tz: tzinfo,
    budget: RegisterBudget,
    placement: Mapping[str, tuple[int, BlockKey]],
) -> list[ContextBlock]:
    """The notes, the memos, and the events of each agenda room, already
    recorded: a `notes` block, a `memos` block and ONE `events`
    block of `<room_events>` groups. At most
    `register_lines` rows of each list, and every row together within
    `register_chars` as rendered: a row that does not fit, and every row after
    it, is left out, and the block or group says how many (`not_listed`) rather
    than cutting a row."""
    spent = 0
    exhausted = False

    def listed(rows: list[str]) -> list[str]:
        nonlocal spent, exhausted
        kept: list[str] = []
        for row in rows:
            cost = len(row) + 1
            if exhausted or spent + cost > budget.register_chars:
                exhausted = True
                break
            kept.append(row)
            spent += cost
        return kept

    blocks: list[ContextBlock] = []
    notes_block = render_notes(
        grounding.notes, ids=True, lines=budget.register_lines, chars=budget.register_chars
    )
    blocks.append(restamp(notes_block, placement))
    spent = len(notes_block.body.text) + (1 if notes_block.body else 0)
    standing_groups = _standing_groups(rooms, grounding, now_ms, tz, budget, listed)
    if standing_groups:
        blocks.append(
            restamp(
                ContextBlock(name="standing", body=compose_body("\n".join(standing_groups))),
                placement,
            )
        )
    groups: list[str] = []
    for room in rooms:
        events = [e for e in grounding.events if e.room == room]
        if not events:
            continue
        rows = listed([render_event_rows([e], tz=tz) for e in events[: budget.register_lines]])
        left_out = len(events) - len(rows)
        groups.append(
            render_group(
                "room_events",
                [
                    ("room", room_label(room, grounding.room_labels)),
                    ("not_listed", str(left_out) if left_out else None),
                ],
                "\n".join(rows),
            )
        )
    if groups:
        blocks.append(
            restamp(ContextBlock(name="events", body=compose_body("\n".join(groups))), placement)
        )
    blocks.append(
        restamp(
            render_memos(grounding.memos, tz=tz, lines=budget.memo_lines, chars=budget.memo_chars),
            placement,
        )
    )
    return blocks


def _standing_groups(
    rooms: Sequence[Room],
    grounding: Grounding,
    now_ms: int,
    tz: tzinfo,
    budget: RegisterBudget,
    listed: Callable[[list[str]], list[str]],
) -> list[str]:
    """The `standing` block's per-room groups: folded source objects, then the retained lane."""
    # Standing: each agenda room's folded source-object state (design 05) — the
    # open poll, the pin set, the member count — typed rows under ONE block, on
    # the Register's own budget row. A gap/partial head says so, never the whole.
    annotated_ids = {r.message_id for r in grounding.records.values() if r.reactions is not None}
    reaction_targets = {
        e.handle: e.target_message_id
        for e in grounding.observations.values()
        if e.kind == "reaction" and e.target_message_id in annotated_ids
    }
    standing_groups: list[str] = []
    for room in rooms:
        objects = grounding.source_objects.get(room_key(room), ())
        if not objects:
            continue
        rows = listed(
            [
                render_elements(
                    [o],
                    tag="source_object",
                    attrs=lambda e, _i: (
                        ("kind", e.kind),
                        ("object", e.handle),
                        ("ordering", e.ordering or None),
                        ("coverage", e.coverage or None),
                        ("rev", e.revision or None),
                        (
                            "state",
                            (
                                "Recorded reaction state on agenda message "
                                f"{reaction_targets[e.handle]}"
                                if e.kind == "reaction" and e.handle in reaction_targets
                                else e.state or None
                            ),
                        ),
                        ("time", e.time_quality if e.time_quality != "upstream" else None),
                        (
                            "observed",
                            when(e.at_ms, now_ms, tz)
                            if e.at_ms > 0 and e.time_quality != "unknown"
                            else None,
                        ),
                        (
                            "received",
                            when(e.received_ms, now_ms, tz)
                            if e.received_ms and e.time_quality != "upstream"
                            else None,
                        ),
                    ),
                    body=lambda _e: "",
                )
                for o in objects[: budget.register_lines]
            ]
        )
        # `not_listed` counts what the grounder's own bounded read already
        # confirmed beyond the shown heads — a lower bound, never a
        # complete-looking cut.
        left = len(objects) - len(rows) + grounding.source_objects_more.get(room_key(room), 0)
        standing_groups.append(
            render_group(
                "room_objects",
                [
                    ("room", room_label(room, grounding.room_labels)),
                    ("not_listed", str(left) if left else None),
                ],
                "\n".join(rows),
            )
        )
    # The retained lane (design 03): an open matter's curated refs, each
    # resolved NOW off the recency page — `matter` names the note tracking
    # it, and an unresolved ref says so rather than vanishing.
    for room in rooms:
        retained = grounding.retained_objects.get(room_key(room), ())
        if not retained:
            continue
        rows = [
            render_elements(
                [_retained_event(r)],
                tag="source_object",
                attrs=partial(_retained_row_attrs, retained=r, now_ms=now_ms, tz=tz),
                body=lambda _e: "",
            )
            for r in retained[: budget.register_lines]
        ]
        omitted = len(retained) - len(rows) + grounding.retained_objects_more.get(room_key(room), 0)
        standing_groups.append(
            render_group(
                "retained_objects",
                [
                    ("room", room_label(room, grounding.room_labels)),
                    ("not_listed", str(omitted) if omitted else None),
                ],
                "\n".join(rows),
            )
        )
    return standing_groups


def _retained_row_attrs(
    event: SourceEvent, _i: int, retained: Any, now_ms: int, tz: tzinfo
) -> tuple[tuple[str, Any], ...]:
    """The `render_elements` attrs signature over one retained row."""
    return _retained_attrs(event, retained, now_ms, tz)


def _retained_event(retained: Any) -> SourceEvent:
    """The retained ref's resolved head, or the `unresolved` placeholder it renders as."""
    if retained.head is not None:
        head: SourceEvent = retained.head
        return head
    return SourceEvent(
        ref="",
        kind=parse_handle(retained.ref.handle)[0] or "object",
        operation="state",
        at_ms=0,
        time_quality="unknown",
        actor=None,
        actor_status="not_applicable",
        handle=retained.ref.handle,
        summary="",
    )


def _retained_attrs(
    event: SourceEvent, retained: Any, now_ms: int, tz: tzinfo,
) -> tuple[tuple[str, Any], ...]:
    """A retained lane row's attributes: the event's own plus `matter`/`unresolved`."""
    return (
        ("kind", event.kind or None),
        ("object", event.handle),
        ("matter", retained.matter or None),
        ("unresolved", "yes" if retained.head is None else None),
        ("ordering", event.ordering or None),
        ("coverage", event.coverage or None),
        ("rev", event.revision or None),
        ("state", event.state or None),
        ("time", event.time_quality if event.time_quality != "upstream" else None),
        (
            "observed",
            when(event.at_ms, now_ms, tz)
            if event.at_ms > 0 and event.time_quality != "unknown"
            else None,
        ),
        (
            "received",
            when(event.received_ms, now_ms, tz)
            if event.received_ms and event.time_quality != "upstream"
            else None,
        ),
    )
