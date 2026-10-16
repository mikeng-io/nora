"""Building what a Cognition session is shown beside its agenda: the Record rows
that raised each stimulus and the Register index lines. The pure half is
`nora.domain.cognition_ground` (the types) / `nora.language.cognition_render`
(the renderer); this is the reads that fill it, each a narrow seam and none a
tier write.

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
- *Withdrawal.* A platform delete or unsend hides a message
  (`RecordStore.hide_message`); the Record read excludes hidden rows
  (`transcript_select`), so a hidden message is not «shown as withheld», it is
  absent, and that includes her OWN unsent send. The `deleted` mark and the
  withheld words (the rule `record_query` keeps) are what is left for a delete
  whose hide did not land (DEC-245, DEC-276).
- *Standing is pulled, never pushed.* No Standing text, head or revision is
  rendered here: a pushed head could carry words the lineage predicates would
  withhold. The session reads Standing through `standing_read`, which applies
  them itself.
- *Every pushed index line honours lineage.* A note or an
  event is shown only when lineage permits it for EVERY room on the agenda
  (`nora.agent.lineage`, the tag path's own predicates). `[lineage] mode` reads
  as it does on the tag path: `on` removes, `shadow` computes the same answer,
  removes NOTHING and logs what it would have, `off` applies none.
  `exclude_persons` follows the lineage mode: under
  `on` a row naming one is withheld (the session has no live roster to check it
  against, and unknown denies); under `shadow` it is logged, not removed; under
  `off` it is ignored.
- *Names.* An author is the identity-canonical name (`Persons.name_transcript`)
  else the self-set profile name, never a platform id (R-18), and what a photo
  shows arrives through the ONE media join every transcript builder shares.

**It raises when it cannot read.** A grounder that swallowed a store failure
would hand the model an agenda that looked complete and was not; the session
records the wake `failed` and its stimuli stay pending.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import replace
from datetime import UTC, datetime, tzinfo
from typing import Any, Protocol

from nora.agent.lineage import LineagePolicy, derived_view, visible_sections
from nora.agent.room import RoomView
from nora.agent.whereis import Circle, presence_audience
from nora.domain.blocks import speaker_name
from nora.domain.cognition_ground import (
    UNNAMED_ROOM,
    EventLine,
    Grounding,
    NoteLine,
    RecordItem,
    RecordKind,
    RollupLine,
    StayDeviation,
    parse_rollup_ref,
    record_ref,
)
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.culture import CultureRevision, nora_character_key
from nora.domain.gatherings import Gathering
from nora.domain.memo import Memo
from nora.domain.mobility_forecast import detect_stay_anomalies, place_words
from nora.domain.notes import Note, likely_duplicates
from nora.domain.record_query import RecordHit, RecordRefHit
from nora.domain.rollup import room_key
from nora.domain.stimuli import Stimulus
from nora.language.culture_render import read_culture

log = logging.getLogger("nora.app.cognition_grounding")

#: An event's lineage sections (`nora.domain.gatherings.Gathering.lineage` is
#: keyed by FIELD name); every one a rendered line shows must pass.
_EVENT_SHOWN_FIELDS = ("what", "start", "place", "status")


class RecordRefSource(Protocol):
    """`nora.infra.sql.record_query.SqlRecordQuery.by_refs`."""

    async def by_refs(
        self, refs: Sequence[tuple[str, int]], rooms: Sequence[tuple[str, str]],
    ) -> Sequence[RecordRefHit]: ...

    async def own_sends(
        self, rooms: Sequence[tuple[str, str]], *, start_ms: int, end_ms: int, limit: int,
    ) -> Sequence[RecordHit]: ...


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


def _rollup_lines(
    pending: Sequence[Stimulus], claimed: set[int], persons: Any,
) -> dict[str, RollupLine]:
    """The readable Rollup stimuli of the agenda, by `ref`: the ref's fields with
    each person's roster NAME. A ref that does not parse, or names a person the
    roster does not know, has no entry: the session is not handed a row it
    cannot read (DEC-245). Names are resolved here, at read time, and never
    serialized (`Grounding.as_dict`)."""
    out: dict[str, RollupLine] = {}
    for s in pending:
        if s.id not in claimed or s.kind != "rollup":
            continue
        parsed = parse_rollup_ref(s)
        if parsed is None:
            continue
        refs = [persons.by_person.get(persons.subject_id_for(p)) for p in parsed.person_ids]
        if any(r is None for r in refs):
            continue
        out[s.ref] = RollupLine(
            ref=s.ref, kind=parsed.kind, names=tuple(r.name for r in refs if r is not None),
            person_ids=parsed.person_ids, start_ms=parsed.start_ms, end_ms=parsed.end_ms,
            place=parsed.place,
        )
    return out


#: What a `deviation_anomaly` line can say about its stay, read from the presence
#: store: the stay's place in words, and how it differed from their own past.
StayContext = tuple[str | None, tuple[StayDeviation, ...]]


class StayReader(Protocol):
    """`nora.infra.sql.mobility.SqlMobilityStore`'s two reads this needs."""

    async def stays_for_subject(self, *, subject_id: str) -> Sequence[Any]: ...

    async def places_for_subject(self, *, subject_id: str) -> Sequence[Any]: ...


def stay_context_reader(
    mobility: StayReader, *, tz_name: str, min_samples: int, z_threshold: float,
    novelty_radius_m: float,
) -> Callable[[str, int], Awaitable[StayContext | None]]:
    """`(subject_id, stay started_ts) -> (place words, deviations)` for the stay a
    `deviation_anomaly` rollup names, or None when the store holds no such stay.
    The deviations are recomputed on read (`detect_stay_anomalies`, as the
    rollup scored them and as `where_been` marks them), with the analytics
    process's own thresholds, so what the line says is what raised it. Nothing is
    invented: a stay with no place has no `at`, and a kind with no baseline is not
    said."""

    async def read(subject_id: str, started_ts: int) -> StayContext | None:
        stays = list(await mobility.stays_for_subject(subject_id=subject_id))
        stay = next((s for s in stays if s.started_ts == started_ts), None)
        if stay is None:
            return None
        places = {p.id: p for p in await mobility.places_for_subject(subject_id=subject_id)}

        def words(place_id: int | None) -> str | None:
            place = places.get(place_id) if place_id is not None else None
            if place is None:
                return None
            return place_words(place.label, place.kind, place.saved_place_id is not None)

        deviations = tuple(
            StayDeviation(
                kind=a.kind, observed=a.observed, expected=a.expected,
                usual_place=words(a.expected_place_id) if a.kind == "unusual_place" else None,
            )
            for a in detect_stay_anomalies(
                stay, history=stays, tz_name=tz_name, min_samples=min_samples,
                z_threshold=z_threshold, novelty_radius_m=novelty_radius_m,
            )
        )
        return words(stay.place_id), deviations

    return read


def place_release(
    *, rooms_of: Callable[[str], Collection[tuple[str, str, bool]]],
    room_view: Callable[[str, str], Awaitable[RoomView]], circles: Sequence[Circle],
) -> Callable[[str, str], Awaitable[bool]]:
    """`(unit, person_id) -> may a place of theirs be named here`: True only
    when EVERY room of the unit passes `presence_audience`, the decision
    `where_been` takes before it names a place (R-35, DEC-279), for a circle
    the person is in. `rooms_of(unit)` is each room's `(platform,
    conversation_id, location_trusted)`. A Cognition session reads across its
    unit and may write into any of its rooms, so one room that may not hear
    it withholds it from all. FAIL-CLOSED: no room, no circle holding the
    person, a room that cannot be read, or any error is False."""

    async def may(unit: str, person_id: str) -> bool:
        try:
            rooms = list(rooms_of(unit))
            mine = [c for c in circles if person_id in c.person_ids]
            if not rooms or not mine:
                return False
            for platform, conversation_id, trusted in rooms:
                view = await room_view(platform, conversation_id)
                if not any(
                    presence_audience(view, c, location_trusted=trusted) == "ok" for c in mine
                ):
                    return False
            return True
        except Exception:
            log.exception("cognition grounding: could not check who may hear a place")
            return False

    return may


def _withheld(deviations: tuple[StayDeviation, ...]) -> tuple[StayDeviation, ...]:
    """The deviations with no place in them: what kind of change, never where."""
    return tuple(replace(d, usual_place=None) for d in deviations)


async def _with_stays(
    unit: str, rollups: dict[str, RollupLine], persons: Any,
    stay_context: Callable[[str, int], Awaitable[StayContext | None]] | None,
    release: Callable[[str, str], Awaitable[bool]] | None,
) -> dict[str, RollupLine]:
    """Each `deviation_anomaly` line with its stay's place and what is usual for
    the person (DEC-279). FAIL-SOFT, like `where_been`'s unusual-stay flags: the
    line without them is still true, so a presence read that fails is logged and
    the line is shown as before, never made up.

    **A place is named only where `where_been` would name it** (`release`,
    `place_release`): otherwise the line says how the stay differed and never
    where, neither the stay's place nor where they usually are. No `release`
    wired is no place."""
    if stay_context is None:
        return rollups
    out = dict(rollups)
    for ref, line in rollups.items():
        if line.kind != "deviation_anomaly" or line.start_ms is None or not line.person_ids:
            continue
        subject = persons.subject_id_for(line.person_ids[0])
        try:
            found = await stay_context(subject, line.start_ms)
        except Exception:
            log.exception("cognition grounding: the stay behind %s could not be read", ref)
            continue
        if found is None:
            continue
        place, deviations = found
        if release is None or not await release(unit, subject):
            place, deviations = None, _withheld(deviations)
        out[ref] = replace(line, stay_place=place, deviations=deviations)
    return out


async def _own_sends(
    records: RecordRefSource, items: Sequence[AgendaItem], pending: Sequence[Stimulus],
    allowed: set[tuple[str, str]], ai_name: str, *, now_ms: int, lookback_ms: int, limit: int,
) -> dict[int, tuple[RecordItem, ...]]:
    """Nora's own sends in each message item's window, by item index (D1): from
    `lookback_ms` before the item's oldest stimulus until now, at most `limit`
    newest, in a room of the unit only. One read per room, over the widest window
    any of its items has."""
    by_id = {s.id: s for s in pending}
    windows: dict[int, tuple[Room, int]] = {}
    for index, item in enumerate(items):
        if item.kind != "message" or (item.room.platform, item.room.conversation_id) not in allowed:
            continue
        times = [by_id[sid].at_ms for sid in item.stimulus_ids if sid in by_id]
        if times:
            windows[index] = (item.room, min(times) - lookback_ms)
    widest: dict[Room, int] = {}
    counts: dict[Room, int] = {}
    for room, start in windows.values():
        widest[room] = min(start, widest.get(room, start))
        counts[room] = counts.get(room, 0) + 1
    read: dict[Room, list[RecordHit]] = {}
    for room, start in widest.items():
        read[room] = list(await records.own_sends(
            [(room.platform, room.conversation_id)], start_ms=start, end_ms=now_ms + 1,
            limit=limit * counts[room],
        ))
    out: dict[int, tuple[RecordItem, ...]] = {}
    for index, (room, start) in windows.items():
        mine = [h for h in read[room] if h.row.ts >= start][-limit:]
        out[index] = tuple(
            RecordItem(
                ref=f"messages:{h.row.id}", kind="message", message_id=h.row.id,
                room=Room(h.platform, h.conversation_id), author=ai_name, at_ms=h.row.ts,
                text=None if h.deleted else (h.row.body or ""), own=True,
            )
            for h in mine
        )
    return out


def unit_self_reader(
    culture: Any | None, policy: Any,
) -> Callable[[str], Awaitable[CultureRevision | None] | None] | None:
    """`build_grounder`'s `self_account`: her account of the unit, or none.

    A Cognition session is unit-scoped and what it sends can reach any room of
    the unit, so the account (written from location rooms' sends too, R-31) is
    read only when every room of the unit agrees on `location`
    (`Policy.shows_self_to_unit`): never into a prompt whose audience could be
    a location-off room. `None` while `[culture]` is off."""
    if culture is None:
        return None

    def read(unit: str) -> Awaitable[CultureRevision | None] | None:
        if not policy.shows_self_to_unit(unit):
            return None
        head: Awaitable[CultureRevision | None] = culture.head(key=nora_character_key(unit))
        return head

    return read


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
    room_label: Callable[[str, str], str] | None = None,
    clock: Callable[[], float] = time.time,
    own_send_lookback_seconds: float = 1800.0,
    own_sends_max: int = 6,
    tz: tzinfo = UTC,
    stay_context: Callable[[str, int], Awaitable[StayContext | None]] | None = None,
    place_release: Callable[[str, str], Awaitable[bool]] | None = None,
    self_account: Callable[[str], Awaitable[CultureRevision | None] | None] | None = None,
    metrics: Any = None,
    memo_index: Callable[[str], Awaitable[Sequence[Memo]]] | None = None,
) -> Callable[[str, Sequence[AgendaItem], Sequence[Stimulus]], Awaitable[Grounding]]:
    """The session's `Grounder` (`nora.app.cognition_session.Grounder`).

    `persons` is `nora.persons.Persons` (`name_transcript`), `media_join` the
    process's one `MediaJoin` or None (photos then stay as their bare handles,
    which is what the row says). `room_label(platform, conversation_id)` is the
    policy's label for a room ('' or None-built: the prompt says `UNNAMED_ROOM`,
    never the id). Everything else is one read seam each.

    **Nora's own sends (D1, DEC-245).** Inside each message item's window (from
    `own_send_lookback_seconds` before its oldest stimulus until now) her own
    delivered sends in that room are read, at most `own_sends_max` newest, and
    handed to the render marked as hers. They are read through the same select
    as every Record row (hidden rows absent, an unsent one's words withheld), in
    a room of the unit only, and they are never a stimulus and never in
    `records`."""

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
        # `on` removes, `shadow` only REPORTS what it would have removed (a log
        # line with the counts) and `off` applies nothing: the tag path's own
        # reading (`nora.agent.prefill._enforced`: `shadow` computes the same
        # answer and only reports it).
        applies = lineage.mode != "off"
        enforced = lineage.mode == "on"
        withheld = {"notes": 0, "events": 0}
        notes_out: list[NoteLine] = []
        # The notebook review's facts, marked on every agenda's index (the `time`
        # item is what asks her to act on them): the anchor day passed or
        # the revisit date came, and a title that shares most of its words with
        # another open note's. Read off the rows the index already holds.
        now_ms = int(clock() * 1000)
        now = datetime.fromtimestamp(now_ms / 1000, tz=tz)
        today = now.year * 10_000 + now.month * 100 + now.day
        indexed = list(await note_index(unit))
        twins = likely_duplicates(indexed)
        for n in indexed:
            # LINEAGE: shown only where it passes for every room the session reads.
            if applies and not all(derived_view(
                notes=(n,), standing=None, culture=None, target=t,
            ).notes for t in targets):
                withheld["notes"] += 1
                if enforced:
                    continue
            review = (
                "anchor_passed" if n.anchor_date is not None and n.anchor_date < today
                else "revisit_due" if n.revisit_at is not None and n.revisit_at <= now_ms
                else None
            )
            notes_out.append(NoteLine(
                id=n.id, title=n.title, description=n.description, review=review,
                may_duplicate=twins.get(n.id),
            ))
        notes = tuple(notes_out)
        # The unit's kept memos (DEC-306): unit-wide, no lineage (the unit is the
        # read boundary, DEC-231). A failed read raises like the note index's.
        memos = tuple(await memo_index(unit)) if memo_index is not None else ()
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
                    withheld["events"] += 1
                    if enforced:
                        continue
                events.append(EventLine(
                    id=str(e.id), room=room, title=e.title,
                    when_ms=None if e.when is None else int(e.when.timestamp() * 1000),
                    venue=e.venue_name or e.venue_text or "", status=e.status.value,
                    revision=None if e.head_revision_id is None else str(e.head_revision_id),
                ))
        rollups = await _with_stays(
            unit, _rollup_lines(pending, claimed, persons), persons, stay_context,
            place_release,
        )
        own_sends = await _own_sends(
            records, items, pending, allowed, ai_display_name,
            now_ms=int(clock() * 1000), lookback_ms=int(own_send_lookback_seconds * 1000),
            limit=own_sends_max,
        )
        labels = {
            f"{r.platform}:{r.conversation_id}":
                (room_label(r.platform, r.conversation_id) if room_label else "") or UNNAMED_ROOM
            for r in agenda_rooms
        }
        if lineage.mode == "shadow" and any(withheld.values()):
            log.info(
                "cognition grounding lineage shadow (unit=%s): would withhold %d note(s) "
                "and %d event(s); shown", unit, withheld["notes"], withheld["events"],
            )
        # Her own account of herself in this unit. Unlike
        # the reads above it is background, never the ground for a claim, so a
        # failed read costs the `self` block and not the session.
        # Bounded and counted like the tag and fire reads (`read_culture`,
        # `stage="cognition_self"`).
        account = await read_culture(
            None if self_account is None else self_account(unit),
            stage="cognition_self", platform="cognition", metrics=metrics,
        )
        return Grounding(
            records=records_out, notes=notes, memos=memos, events=tuple(events),
            rollups=rollups,
            own_sends=own_sends, room_labels=labels, self_account=account,
        )

    return ground


__all__ = ["RecordRefSource", "build_grounder"]
