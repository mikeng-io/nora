"""Building what a Cognition session is shown beside its agenda: the Record rows
that raised each stimulus and the Register index lines (design/cognition-loop/
30-cognition.md §1; #637 review, major 2). The pure half is
`nora.domain.cognition_ground` / `nora.domain.cognition_render`; this is the
reads that fill it, each a narrow seam and none a tier write.

**Independent of `record_query`.** `[record_query] enabled` decides whether a
MODEL is offered the tool. The rows that raised a stimulus are code's own input
to build the agenda from, so they are read through `RecordRefSource` whether or
not the tool is on, and a session with the tool off still sees who said what.

**Boundaries, each in the read and not after it.**

- *The unit.* Only rows of rooms that belong to the unit the wake is for
  (`unit_rooms`, from policy) are selected; a stimulus naming a room that is not
  one of them is unreadable, never rendered.
- *The room.* A row is joined to the stimulus that named it and must be in that
  stimulus's own room: the item's room is the one the reads and the render
  resolve through (R-21).
- *Withdrawal.* `nora person forget` hides a message; the Record read excludes
  hidden rows (`transcript_select`), so a hidden message is not «shown as
  withheld», it is absent. A deleted message's words are withheld (the row is
  shown as deleted), the rule `record_query` keeps.
- *Standing is pulled, never pushed* (30 §1: "Standing bodies are pulled ...
  never pushed"). No Standing text, head or revision is rendered here:
  `standing_store.read()` returns rows the withdrawal cascade marked
  unsupported, so a pushed head could carry a withdrawn person's words to the
  model. The session sees no Standing until `standing_read` lands and applies
  the withdrawal and lineage predicates itself (#637 second review, blocker).
- *Every pushed index line honours withdrawal and lineage.* A note marked
  `unsupported_at` is dropped here as well as in the store's read (the seam is
  injected, so the rule is held where the line is built), and a note or an
  event is shown only when lineage permits it for EVERY room on the agenda
  (`nora.agent.lineage`, the tag path's own predicates; `[lineage] mode = "off"`
  applies none, exactly as the tag path). A row naming `exclude_persons` is
  withheld: the session has no live roster to check it against, and unknown
  denies.
- *Names.* An author is the identity-canonical name (`Persons.name_transcript`)
  else the self-set profile name, never a platform id (R-18), and what a photo
  shows arrives through the ONE media join every transcript builder shares.

**It raises when it cannot read.** A grounder that swallowed a store failure
would hand the model an agenda that looked complete and was not; the session
records the wake `failed` and its stimuli stay pending.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Any, Protocol

from nora.agent.lineage import LineagePolicy, derived_view, visible_sections
from nora.domain.blocks import speaker_name
from nora.domain.cognition_ground import (
    EventLine,
    Grounding,
    NoteLine,
    RecordItem,
    RecordKind,
    record_ref,
)
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.gatherings import Gathering
from nora.domain.notes import Note
from nora.domain.record_query import RecordRefHit
from nora.domain.rollup import room_key
from nora.domain.stimuli import Stimulus

log = logging.getLogger("nora.app.cognition_grounding")

#: An event's lineage sections (`nora.domain.gatherings.Gathering.lineage` is
#: keyed by FIELD name); every one a rendered line shows must pass.
_EVENT_SHOWN_FIELDS = ("what", "start", "place", "status")


class RecordRefSource(Protocol):
    """`nora.infra.sql.record_query.SqlRecordQuery.by_refs`."""

    async def by_refs(
        self, refs: Sequence[tuple[str, int]], rooms: Sequence[tuple[str, str]],
    ) -> Sequence[RecordRefHit]: ...


def _record_item(
    stimulus: Stimulus, ref: tuple[str, int], found: RecordRefHit, *, body: str | None,
    author: str,
) -> RecordItem:
    table, _ = ref
    hit = found.hit
    kind: RecordKind
    if table == "message_reaction":
        kind = "reaction"
    elif table == "message_delete":
        kind = "delete"
    else:
        kind = "edit" if stimulus.item == "edit" else "message"
    withheld = hit.deleted or kind == "delete"
    return RecordItem(
        ref=stimulus.ref, kind=kind, message_id=hit.row.id,
        room=Room(hit.platform, hit.conversation_id), author=author,
        person_id=stimulus.person_id,
        at_ms=stimulus.at_ms if kind != "message" else hit.row.ts,
        text=None if withheld else body, emoji=found.emoji,
        edited=hit.row.edit is not None,
    )


def build_grounder(
    *,
    records: RecordRefSource,
    persons: Any,
    media_join: Any | None,
    note_index: Callable[[str], Awaitable[Sequence[Note]]],
    events_in: Callable[[str, str], Awaitable[Sequence[Gathering]]],
    lineage: LineagePolicy,
    unit_rooms: Callable[[str], Collection[tuple[str, str]]],
    ai_display_name: str = "Nora",
) -> Callable[[str, Sequence[AgendaItem], Sequence[Stimulus]], Awaitable[Grounding]]:
    """The session's `Grounder` (`nora.app.cognition_session.Grounder`).

    `persons` is `nora.persons.Persons` (`name_transcript`), `media_join` the
    process's one `MediaJoin` or None (photos then stay as their bare handles,
    which is what the row says). Everything else is one read seam each."""

    async def ground(
        unit: str, items: Sequence[AgendaItem], pending: Sequence[Stimulus],
    ) -> Grounding:
        allowed = set(unit_rooms(unit))
        claimed = {sid for item in items for sid in item.stimulus_ids}
        wanted: list[tuple[Stimulus, tuple[str, int]]] = []
        wanted_rooms: set[tuple[str, str]] = set()
        for s in pending:
            ref = record_ref(s)
            if s.id not in claimed or ref is None:
                continue
            if s.platform is None or s.conversation_id is None:
                continue
            if (s.platform, s.conversation_id) not in allowed:
                # A stimulus for a room that is not one of the unit's: never
                # read, never rendered. The item still says so («could not be
                # shown»).
                log.warning(
                    "cognition grounding: stimulus %s names a room outside unit %s; "
                    "not read", s.id, unit,
                )
                continue
            wanted.append((s, ref))
            wanted_rooms.add((s.platform, s.conversation_id))
        found: dict[tuple[str, int], RecordRefHit] = {}
        if wanted:
            hits = await records.by_refs([r for _, r in wanted], sorted(wanted_rooms))
            found = {(h.table, h.row_id): h for h in hits}
        rows = []
        pairs: list[tuple[Stimulus, tuple[str, int], RecordRefHit]] = []
        for s, ref in wanted:
            hit = found.get(ref)
            # The row must be in the room the stimulus named.
            if hit is None or (hit.hit.platform, hit.hit.conversation_id) != (
                s.platform, s.conversation_id,
            ):
                continue
            pairs.append((s, ref, hit))
            rows.append(hit.hit.row)
        # Names and what the photos show, once per platform batch.
        named = []
        for (_s, _ref, hit), row in zip(pairs, rows, strict=True):
            named.append(persons.name_transcript([row], platform=hit.hit.platform)[0])
        if media_join is not None and named:
            named = list(await media_join.enrich(named, path="dreamer"))
        records_out: dict[str, RecordItem] = {}
        for (s, ref, hit), row in zip(pairs, named, strict=True):
            author = (
                ai_display_name if row.is_nora
                else speaker_name(row.person_name, row.sender_name)
            )
            records_out[s.ref] = _record_item(
                s, ref, hit, body=row.body, author=author,
            )

        agenda_rooms = list(dict.fromkeys(i.room for i in items))
        targets = [
            lineage.render_room(platform=r.platform, conversation_id=r.conversation_id)
            for r in agenda_rooms
        ]
        applies = lineage.mode != "off"
        notes = tuple(
            NoteLine(id=n.id, title=n.title, description=n.description)
            for n in await note_index(unit)
            # WITHDRAWN: dropped here too. LINEAGE: shown only where it passes
            # for every room the session reads.
            if n.unsupported_at is None and (
                not applies or all(derived_view(
                    notes=(n,), standing=None, culture=None, target=t,
                ).notes for t in targets)
            )
        )
        events: list[EventLine] = []
        for room in agenda_rooms:
            if (room.platform, room.conversation_id) not in allowed:
                continue
            target = lineage.render_room(
                platform=room.platform, conversation_id=room.conversation_id,
            )
            for e in await events_in(unit, room.conversation_id):
                if applies and not all(visible_sections(
                    e.lineage, _EVENT_SHOWN_FIELDS, target,
                    origin=room_key(room.platform, room.conversation_id),
                    exclude_persons=e.exclude_persons,
                ).values()):
                    continue
                events.append(EventLine(
                    id=str(e.id), room=room, title=e.title,
                    when_ms=None if e.when is None else int(e.when.timestamp() * 1000),
                    venue=e.venue_name or e.venue_text or "", status=e.status.value,
                    revision=None if e.head_revision_id is None else str(e.head_revision_id),
                ))
        return Grounding(records=records_out, notes=notes, events=tuple(events))

    return ground


__all__ = ["RecordRefSource", "build_grounder"]
