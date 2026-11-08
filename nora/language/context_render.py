"""Shared context builders: canonical typed input, one format per source."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from nora.domain.blocks import ContextBlock, render_elements, render_messages, speaker_name
from nora.domain.composed import compose_body
from nora.domain.context_data import MembersView, RoomLine, TranscriptLine
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.prompt import age
from nora.domain.quote_window import first_by_ts


def render_members(view: MembersView) -> ContextBlock:
    return ContextBlock(
        name="members",
        body=compose_body(
            render_elements(
                () if view.unavailable else view.people,
                tag="person",
                attrs=lambda p, _: (
                    ("id", p.person_id),
                    ("name", p.name),
                    ("aliases", ", ".join(p.aliases) or None),
                ),
                body=lambda _: "",
            )
        ),
        attrs=(
            ("room", view.room),
            ("source", "identity and room roster"),
            *((("status", "unavailable"),) if view.unavailable else ()),
            *((("unnamed", str(view.unnamed)),) if view.unnamed else ()),
        ),
        when_empty="self_close",
    )


def render_rooms(rooms: Sequence[RoomLine], *, now: datetime, withheld: int = 0) -> ContextBlock:
    return ContextBlock(
        name="rooms",
        body=compose_body(
            render_elements(
                rooms,
                tag="room",
                attrs=lambda x, _: (
                    ("id", f"{x.room.platform}:{x.room.conversation_id}"),
                    ("label", x.room.label),
                    ("platform", x.room.platform),
                    ("purpose", x.room.description),
                    (
                        "activity",
                        "unavailable"
                        if x.activity_unavailable
                        else age(x.last_ms, now)
                        if x.last_ms is not None
                        else "none on record",
                    ),
                ),
                body=lambda _: "",
            )
        ),
        attrs=(("withheld", str(withheld)),) if withheld else (),
        when_empty="self_close",
    )


def transcript_rows(
    rows: Sequence[TranscriptLine],
    *,
    display_name: str,
    now: datetime,
    source_ids: bool = False,
) -> str:
    # A quote timestamp is meaningful within its source room only.
    room_rows: dict[str, list[TranscriptRow]] = {}
    for line in rows:
        room_rows.setdefault(line.room, []).append(line.row)
    by_room_ts = {room: first_by_ts(group) for room, group in room_rows.items()}
    indexes = {line.row.id: i for i, line in enumerate(rows, 1)}

    def parent(line: TranscriptLine) -> int | None:
        row = line.row
        hit = (
            by_room_ts.get(line.room, {}).get(row.quoted_ts) if row.quoted_ts is not None else None
        )
        n = indexes.get(hit.id) if hit is not None else None
        return n if n is not None and n < indexes[row.id] else None

    return render_messages(
        rows,
        speaker=lambda x: (
            display_name if x.row.is_nora else speaker_name(x.row.person_name, x.row.sender_name)
        ),
        body=lambda x: x.row.body,
        timestamp=lambda x: x.row.ts,
        replying_to=parent,
        now=now,
        edited=lambda x: x.row.edit.ts if x.row.edit else None,
        was=lambda x: x.row.edit.was if x.row.edit else None,
        turn=lambda x: x.row.turn_handle,
        reactions=lambda x: x.row.reactions.label if x.row.reactions else None,
        reactions_observed=lambda x: (
            f"{x.row.reactions.coverage} {age(x.row.reactions.observed_at, now)}"
            if x.row.reactions is not None and x.row.reactions.observed_at is not None
            else None
        ),
        extra=lambda x: (
            ("source_id", str(x.row.id) if source_ids else None),
            ("author_id", "nora" if x.row.is_nora else x.row.person_id or "unknown"),
            ("room", x.room or None),
            ("at", datetime.fromtimestamp(x.row.ts / 1000, now.tzinfo).isoformat()),
            ("item", str(x.item) if x.item is not None else None),
            (
                "delivery",
                ("delivered" if x.row.delivery_status is DeliveryStatus.SENT else "not delivered")
                if x.row.is_nora
                else None,
            ),
        ),
    )


def render_transcript(
    rows: Sequence[TranscriptLine],
    *,
    display_name: str,
    now: datetime,
    not_listed: int = 0,
    withheld: int = 0,
    read_limited: int = 0,
) -> ContextBlock:
    return ContextBlock(
        name="transcript",
        body=compose_body(
            transcript_rows(
                rows,
                display_name=display_name,
                now=now,
                source_ids=True,
            )
        ),
        attrs=(
            ("not_listed", str(not_listed)),
            ("withheld", str(withheld)),
            ("source", "recorded messages"),
            ("read_limited_rooms", str(read_limited)),
        ),
        when_empty="self_close",
    )


def render_clock(*, now: datetime, last_session_ms: int | None) -> ContextBlock:
    from nora.domain.prompt import escape

    since = (
        "This is your first look at this group."
        if last_session_ms is None
        else f"You last looked at this group {age(last_session_ms, now)}."
    )
    return ContextBlock(
        name="clock",
        body=compose_body(escape(f"It is {now:%A %-d %B %Y, %H:%M} ({now.tzname()}). {since}")),
        attrs=(("as_of", now.isoformat()),),
    )


def render_reads(bindings: Sequence[tuple[str, str]]) -> ContextBlock:
    return ContextBlock(
        name="reads",
        body=compose_body(
            render_elements(
                bindings,
                tag="binding",
                attrs=lambda x, _: (("agenda_room", x[0]), ("label", x[1])),
                body=lambda _: "",
            )
        ),
        when_empty="self_close",
    )
