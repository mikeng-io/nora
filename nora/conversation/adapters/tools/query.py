from __future__ import annotations

import itertools
import logging
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal

from nora.agent.toolkit.clock import _HKT
from nora.agent.toolkit.results import _memory_result, _record
from nora.agent.toolkit.rooms import _readable_rooms, _speaker
from nora.agent.toolkit.transcript import (
    _DISCOVERY_LIMIT,
    _HISTORY_LIMIT,
    _OBSERVATION_KINDS,
    _REACTIONS_NOTE,
    _RECORD_QUERY_AS_OF_TARGET,
    _RECORD_QUERY_DELETED,
    _RECORD_QUERY_MORE,
    _RECORD_QUERY_NEAR,
    _RECORD_QUERY_NO_AUTHOR,
    _RECORD_QUERY_NONE,
    _RECORD_QUERY_OFF,
    _RECORD_QUERY_TIME,
    _RECORD_QUERY_WITHHELD,
    _RECORD_SOURCE_MAX,
    _TARGET_MAX,
    _record_interval,
    _source_event_item,
    _target_item,
    _transcript_messages,
)
from nora.agent.turn_recording import record_requirement
from nora.domain.cognition_session import COGNITION_ADAPTOR
from nora.domain.lineage import (
    LiveRoom,
    RoomFacts,
    room_key,
)
from nora.domain.lineage import message as message_lineage
from nora.domain.models import (
    REACTION_PAIRS_MAX,
    TranscriptRow,
    display_reactions,
)
from nora.domain.observations import (
    UNSUPPORTED_KINDS,
    ObjectKind,
    SourceEvent,
    SourceScope,
    TargetContext,
    coverage_label,
    handle_of,
    head_detail,
    head_summary,
    object_handle,
    observation_summary,
    parse_handle,
    revision_label,
    target_context,
)
from nora.domain.record_query import RECORD_QUERY_MAX, RecordHit, RecordQuery
from nora.domain.self_presence import platform_name
from nora.domain.turn_record import DisclosureRequirement

log = logging.getLogger("nora.tools")


def _make__source_name_of(deps):
    persons = deps.persons

    def _source_name_of(platform: str, ref: str) -> str | None:
        """A subject's roster name, or None — a raw ref never reaches a
        tool result (R-18)."""
        found = persons.resolve(platform, ref) if persons is not None else None
        return found.name if found is not None else None

    return _source_name_of


def _make__resolve_targets(deps):
    display_name = deps.display_name
    persons = deps.persons
    store = deps.store

    async def _resolve_targets(
        platform: str,
        conversation_id: str,
        msg_ids: Iterable[int],
        *,
        as_of_ms: int | None = None,
    ) -> dict[int, TargetContext]:
        """The affected messages these `target_message_id`s name, by id —
        ONE bounded batched Record read of the ROOM THE EVENT WAS READ IN
        (`by_ids`), named through the roster. A failed or unresolvable read
        returns what's absent: the caller's `target_unresolved` says so,
        and nothing is guessed or scanned."""
        wanted = tuple(dict.fromkeys(i for i in msg_ids if i))
        if not wanted:
            return {}
        try:
            found, gone = await store.by_ids(
                platform=platform,
                conversation_id=conversation_id,
                ids=wanted[:_TARGET_MAX],
                as_of_ms=as_of_ms,
            )
        except Exception:
            log.exception("record target read failed")
            return {}
        named = persons.name_transcript(list(found.values()), platform=platform)
        return {
            mid: target_context(row, deleted=mid in gone, author=_speaker(row, display_name))
            for mid, row in zip(found, named, strict=True)
        }

    return _resolve_targets


def _make__head_target(deps):
    observations = deps.observations
    _resolve_targets = _make__resolve_targets(deps)
    now_fn = deps.now_fn

    async def _head_target(scope: SourceScope, head: Any) -> dict | None:
        """The affected message behind a folded head — through its newest
        applied row's `target_message_id` — as a `target` dict;
        `{"unresolved": True}` when the link exists but the row cannot be
        shown (hidden, another room, or the bound), None when it names none."""
        for row in await observations.by_ids(scope=scope, ids=(head.head_id,)):
            if row.target_message_id is None:
                return None
            targets = await _resolve_targets(
                scope.platform, scope.conversation_id, (row.target_message_id,)
            )
            t = targets.get(row.target_message_id)
            return (
                _target_item(t, now=now_fn().astimezone(_HKT))
                if t is not None
                else {"unresolved": True}
            )
        return None

    return _head_target


def _make__room_source_events(deps):
    observations = deps.observations
    _resolve_targets = _make__resolve_targets(deps)
    _source_name_of = _make__source_name_of(deps)

    async def _room_source_events(
        scope: SourceScope,
        *,
        since_ms: int,
        limit: int = 24,
        as_of_ms: int | None = None,
    ) -> tuple[tuple[SourceEvent, ...], int] | None:
        """The room's typed source events at/after `since_ms`, projected —
        `(events, more)` where `more` counts what the bound cut — or None
        when the read failed (the caller says so in its result: a failed
        read is `unavailable`, never an empty list, and a bounded one never
        reads as complete). `as_of_ms` bounds the event window AND the poll
        labels at the caller's instant: a head folded after it (an option
        renamed, a vote recorded later) is not the label the event then
        carried, so it is withheld rather than shown."""
        try:
            rows = await observations.recent(
                scope=scope, since_ms=since_ms, limit=limit + 1, before_ms=as_of_ms
            )
            poll_heads = {
                (h.object_ref, h.account_key, h.thread): h
                for h in await observations.heads(scope=scope, kind="poll", limit=32)
                if as_of_ms is None or h.received_ms <= as_of_ms
            }
        except Exception:  # noqa: BLE001 — any read failure is `unavailable`
            return None

        def option_label(r: Any, ref: str) -> str | None:
            h = poll_heads.get(
                (
                    r.observation.object_ref,
                    r.observation.scope.account_key,
                    r.observation.scope.thread,
                )
            )
            entry = (h.state.get("options") or {}).get(ref) if h else None
            return (entry or {}).get("label")

        def event_of(r: Any) -> SourceEvent:
            o = r.observation
            actor = None
            if o.actor_id and o.actor_status == "known":
                actor = _source_name_of(o.scope.platform, o.actor_id)
            return SourceEvent(
                ref=f"platform_observation:{r.id}",
                kind=o.object_kind,
                operation=o.operation,
                at_ms=(o.occurred_ms if o.occurred_ms is not None else o.received_ms),
                time_quality=o.time_quality,
                actor=actor,
                actor_status=o.actor_status,
                handle=object_handle(
                    o.object_kind,
                    o.object_ref,
                    account_key=o.scope.account_key,
                    thread=o.scope.thread,
                ),
                summary=observation_summary(
                    o,
                    name_of=lambda ref, o=o: _source_name_of(o.scope.platform, ref),
                    option_label=lambda ref, r=r: option_label(r, ref),
                ),
                target_message_id=r.target_message_id,
                received_ms=o.received_ms,
                revision=revision_label(o.source_position, o.revision_scope),
                coverage=coverage_label(o.coverage),
            )

        shown = tuple(event_of(r) for r in rows[:limit])
        # The affected messages the shown events name — one batched Record
        # read of the room they were read in, fail-soft like the source
        # read itself: an unresolvable target says `target_unresolved`,
        # never a guess.
        targets = await _resolve_targets(
            scope.platform,
            scope.conversation_id,
            (e.target_message_id for e in shown if e.target_message_id is not None),
            as_of_ms=as_of_ms,
        )
        return (
            tuple(
                replace(
                    e,
                    target=targets.get(e.target_message_id)
                    if e.target_message_id is not None
                    else None,
                )
                for e in shown
            ),
            max(0, len(rows) - limit),
        )

    return _room_source_events


def _make__observation_query(deps):
    async def _observation_query(
        kind: str,
        object: str | None,
        history: bool,
        at_id: int | None,
    ) -> dict:
        """The typed `kind` branch of `record_query`: ONE bounded, scoped
        read of the observation Record — discovery (`heads`) with no
        `object`, the object's folded head with one, its retained history
        or an as-of point when asked. R-27 holds: an unwired store is
        `unavailable`, a scope with no such object is `nothing_found`,
        and a bounded `as_of` that cut its history says `partial`."""
        c = SimpleNamespace(
            kind=kind,
            object=object,
            history=history,
            at_id=at_id,
            metrics=deps.metrics,
            observations=deps.observations,
            room=deps.room,
            _head_target=_make__head_target(deps),
            _resolve_targets=_make__resolve_targets(deps),
            _source_name_of=_make__source_name_of(deps),
            bound_conversation_id=deps.bound_conversation_id,
            now_fn=deps.now_fn,
        )
        k_, _, v_ = await __observation_query__p1(c)
        if k_:
            return v_
        try:
            k_, _, v_ = await __observation_query__p2(c)
            if k_:
                return v_
            k_, _, v_ = await __observation_query__p3(c)
            if k_:
                return v_
        except Exception:  # noqa: BLE001
            k_, _, v_ = await __observation_query__p4(c)
            if k_:
                return v_
        return None  # type: ignore[return-value]

    return _observation_query


def _make_record_query(deps):
    async def record_query(
        start: str = "",
        end: str | None = None,
        scope: Literal["this_room", "all_rooms"] = "this_room",
        author: str | None = None,
        text: str | None = None,
        kind: ObjectKind | None = None,
        object: str | None = None,
        history: bool = False,
        at_id: int | None = None,
    ) -> dict:
        """Search recorded messages and source objects when somebody asks what
        was said.

        SHOULD: give `text` as the words were written.
        Message queries return `messages` oldest first with room, sender,
        body and time. Object queries return objects, state or history.
        Statuses: ok, nothing_found, ambiguous, unavailable.

        Args:
            start: From this Hong Kong time, "YYYY-MM-DD HH:MM", or a date
                for the start of that day.
            end: Up to this Hong Kong time, same form, a date meaning the end
                of that day. Leave it out to read up to now.
            scope: this_room, or all_rooms of this group.
            author: Only messages by this person, a name used in this
                conversation.
            text: Only messages containing these words.
            kind: One of poll, room, membership, pin, artifact, reaction,
                message_metadata, switches the query to the room's recorded
                source objects of that kind.
            object: An object handle a `kind` call returned, for its detail.
            history: With `object`, the object's retained source events.
            at_id: With `object`, the object's folded state through that
                observation id (partial when the bound cut the history).
        """
        c = SimpleNamespace(
            start=start,
            end=end,
            scope=scope,
            author=author,
            text=text,
            kind=kind,
            object=object,
            history=history,
            at_id=at_id,
            lineage=deps.lineage,
            media_join=deps.media_join,
            metrics=deps.metrics,
            observations=deps.observations,
            persons=deps.persons,
            record=deps.record,
            room=deps.room,
            _observation_query=_make__observation_query(deps),
            _record_messages=_make__record_messages(deps),
            _room_source_events=_make__room_source_events(deps),
            bound_conversation_id=deps.bound_conversation_id,
            now_fn=deps.now_fn,
        )
        k_, _, v_ = await _record_query__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _record_query__p2(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return record_query


def _make__record_messages(deps):
    display_name = deps.display_name
    dream_adaptor = deps.dream_adaptor
    on_rows = deps.on_rows

    def _record_messages(
        hits: Sequence[RecordHit],
        rows: Sequence[TranscriptRow],
        *,
        rooms: Sequence[RoomFacts],
        near: frozenset[int],
        now: datetime,
    ) -> list[dict]:
        """The hits in `load_transcript`'s one shape (`_transcript_messages`),
        each named with its room, its reactions and whether it was deleted.
        A deleted message keeps its place and loses its words: its author
        took them back (`RecordStore.hide_message` hides it).

        Each row carries its number `n` ONLY for the Cognition session, the
        citation handle a note's line rests on (DEC-250); never on a tag turn
        or any other pass (R-18)."""
        names = {
            (r.platform, cid): (r.label or platform_name(r.platform))
            for r in rooms
            for cid in r.ids
        }
        out: list[dict] = []
        for hit, row in zip(hits, rows, strict=True):
            origin = room_key(hit.platform, hit.conversation_id)
            record_requirement(DisclosureRequirement(origin, (message_lineage(origin),)))
            item = _transcript_messages(
                [row],
                now=now,
                with_ids=dream_adaptor == COGNITION_ADAPTOR,
                display_name=display_name,
                on_rows=on_rows if not hit.deleted else None,
            )[0][0]
            item = {"room": names.get((hit.platform, hit.conversation_id), ""), **item}
            if hit.reactions:
                item["reactions"] = dict(
                    itertools.islice(
                        display_reactions(hit.reactions).items(),
                        REACTION_PAIRS_MAX,
                    )
                )
            if hit.deleted:
                item["body"] = None
                item.pop("was", None)
                item["deleted"] = True
            if hit.row.id in near:
                item["near"] = True
            out.append(item)
        return out

    return _record_messages


async def __observation_query__p1(c):
    if c.kind in UNSUPPORTED_KINDS.get(c.room.platform, frozenset()):
        _record(c.metrics, "record_query", "unavailable", kind=c.kind, reason="unsupported")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    f"This source cannot report "
                    f"{c.kind}s here — the platform does not carry them to the bridge."
                ),
            },
        )
    c.scope = SourceScope(
        platform=c.room.platform, account_key="", conversation_id=c.bound_conversation_id
    )
    return (False, None, None)


async def __observation_query__p2(c):
    if c.object is None and (not c.history) and (c.at_id is None):
        c.heads = await c.observations.heads(scope=c.scope, kind=c.kind, limit=_DISCOVERY_LIMIT + 1)
        c.heads = sorted(
            (h for h in c.heads if h.applied > 0),
            key=lambda h: max(h.observed_ms or 0, h.received_ms),
            reverse=True,
        )
        c.more = len(c.heads) > _DISCOVERY_LIMIT
        c.out = [
            {
                "object": handle_of(h),
                "state": head_summary(
                    h, name_of=lambda ref: c._source_name_of(c.scope.platform, ref)
                ),
                ("ordering"): h.ordering,
                **({"time": h.observed_quality} if h.observed_quality != "upstream" else {}),
                **({"coverage": coverage_label(h.coverage)} if coverage_label(h.coverage) else {}),
            }
            for h in c.heads[:_DISCOVERY_LIMIT]
        ]
        if not c.out:
            _record(c.metrics, "record_query", "nothing_found", kind=c.kind)
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    "note": (f"No {c.kind} objects are recorded for this room."),
                },
            )
        _record(c.metrics, "record_query", "ok", kind=c.kind, objects=len(c.out), more=c.more)
        return (
            True,
            None,
            _memory_result(
                {
                    "status": "ok",
                    "kind": c.kind,
                    "objects": c.out,
                    **({("more"): True} if c.more else {}),
                    "note": "Objects are named by opaque handles; pass "
                    "one back as `object` for its detail.",
                }
            ),
        )
    c.head = await c.observations.head_for_handle(scope=c.scope, handle=c.object)
    if c.head is None or c.head.kind != c.kind:
        _record(c.metrics, "record_query", "nothing_found", kind=c.kind, handle=bool(c.object))
        return (
            True,
            None,
            {"status": "nothing_found", "note": ("No recorded object has that handle here.")},
        )
    if c.at_id is not None:
        c.folded, c.more = await c.observations.as_of(
            scope=c.scope, kind=c.kind, object_ref=c.head.object_ref, through_id=c.at_id
        )
        c.note = (
            (
                "The read cut off part-way through the object's history — this is an earlier, "
                "partial state."
            )
            if c.more
            else None
        )
        if c.folded is not None:
            for _w1 in await c.observations.by_ids(scope=c.scope, ids=(c.folded.head_id,)):
                c.row = _w1
                if c.row.target_message_id is not None:
                    c.note = " ".join(n for n in (c.note, _RECORD_QUERY_AS_OF_TARGET) if n)
        _record(c.metrics, "record_query", "ok", kind=c.kind, as_of=True)
        return (
            True,
            None,
            _memory_result(
                {
                    "status": "ok",
                    "kind": c.kind,
                    "object": c.object,
                    ("state"): head_summary(
                        c.folded, name_of=lambda ref: c._source_name_of(c.scope.platform, ref)
                    )
                    if c.folded
                    else "no state",
                    "detail": head_detail(
                        c.folded, name_of=lambda ref: c._source_name_of(c.scope.platform, ref)
                    )
                    if c.folded
                    else None,
                    "ordering": c.folded.ordering if c.folded else "",
                    **({"note": c.note} if c.note else {}),
                }
            ),
        )
    return (False, None, None)


async def __observation_query__p3(c):
    if c.history:
        c.rows = await c.observations.history(
            scope=c.scope, kind=c.kind, object_ref=c.head.object_ref, limit=_HISTORY_LIMIT + 1
        )
        c.targets = await c._resolve_targets(
            c.scope.platform,
            c.scope.conversation_id,
            (r.target_message_id for r in c.rows[:_HISTORY_LIMIT]),
        )
        c.now = c.now_fn().astimezone(_HKT)
        c.out = [
            {
                "event": f"platform_observation:{r.id}",
                "op": r.observation.operation,
                **(
                    {("at_ms"): r.observation.occurred_ms}
                    if r.observation.occurred_ms is not None
                    and r.observation.time_quality != "unknown"
                    else {}
                ),
                "time": r.observation.time_quality,
                **(
                    {"received_ms": r.observation.received_ms}
                    if r.observation.received_ms and r.observation.time_quality != "upstream"
                    else {}
                ),
                "coverage": coverage_label(r.observation.coverage),
                "summary": observation_summary(
                    r.observation,
                    name_of=lambda ref, o=r.observation: c._source_name_of(o.scope.platform, ref),
                ),
                **(
                    {"target": _target_item(c.targets[r.target_message_id], now=c.now)}
                    if r.target_message_id in c.targets
                    else {"target_unresolved": True}
                    if r.target_message_id is not None
                    else {}
                ),
            }
            for r in c.rows[:_HISTORY_LIMIT]
        ]
        _record(c.metrics, "record_query", "ok", kind=c.kind, events=len(c.out))
        return (
            True,
            None,
            _memory_result(
                {
                    "status": "ok",
                    "kind": c.kind,
                    "object": c.object,
                    ("events"): c.out,
                    **({"more": True} if len(c.rows) > _HISTORY_LIMIT else {}),
                }
            ),
        )
    c.detail = head_detail(c.head, name_of=lambda ref: c._source_name_of(c.room.platform, ref))
    c.target = await c._head_target(c.scope, c.head)
    if c.target is not None:
        c.detail["target"] = c.target
    _record(c.metrics, "record_query", "ok", kind=c.kind, detail=True)
    return (
        True,
        None,
        _memory_result({"status": "ok", "kind": c.kind, "object": c.object, "detail": c.detail}),
    )
    return (False, None, None)


async def __observation_query__p4(c):
    log.exception("record_query observation branch failed")
    _record(c.metrics, "record_query", "unavailable", kind=c.kind)
    return (True, None, {"status": "unavailable", "note": _RECORD_QUERY_OFF})
    return (False, None, None)


async def _record_query__p1(c):
    if (c.kind is not None or c.object is not None) and c.scope != "this_room":
        _record(c.metrics, "record_query", "unavailable", reason="object_scope")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "Source object reads are bound to "
                    "this room. Choose the authorized room before reading its objects."
                ),
            },
        )
    if c.kind is not None or c.object is not None:
        if c.observations is None or (c.kind is not None and c.kind not in _OBSERVATION_KINDS):
            _record(
                c.metrics,
                "record_query",
                "unavailable",
                reason="unwired" if c.observations is None else ("kind"),
            )
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": _RECORD_QUERY_OFF
                    if c.observations is None
                    else (f"`kind` must be one of {', '.join(_OBSERVATION_KINDS)}."),
                },
            )
        c.parsed = parse_handle(c.object) if c.object else ("", "")
        if c.object is not None and c.kind is None and (not c.parsed[0]):
            _record(c.metrics, "record_query", "nothing_found", reason="handle")
            return (
                True,
                None,
                {"status": "nothing_found", "note": ("No recorded object has that handle here.")},
            )
        return (
            True,
            None,
            await c._observation_query(c.kind or c.parsed[0], c.object, c.history, c.at_id),
        )
    if c.record is None or c.lineage is None:
        _record(c.metrics, "record_query", "unavailable", reason="unwired")
        return (True, None, {"status": "unavailable", "note": _RECORD_QUERY_OFF})
    c.target = c.lineage.render_room(
        platform=c.room.platform, conversation_id=c.bound_conversation_id
    )
    c.here = c.target.room
    if c.here is None:
        _record(c.metrics, "record_query", "unavailable", reason="room")
        return (True, None, {"status": "unavailable", "note": _RECORD_QUERY_OFF})
    c.now = c.now_fn().astimezone(_HKT)
    c.as_of_ms = int(c.now.timestamp() * 1000)
    try:
        c.start_ms, c.end_ms = _record_interval(c.start, c.end, c.now)
    except (ValueError, OverflowError):
        _record(c.metrics, "record_query", "unavailable", reason="time")
        return (True, None, {"status": "unavailable", "note": _RECORD_QUERY_TIME})
    if c.scope == "all_rooms":
        c.view = await c.room.scope()
        c.target = c.target.with_live(
            LiveRoom(
                person_ids=frozenset(p.person_id for p in c.view.people),
                unnamed=c.view.unnamed,
                available=c.view.available,
                degraded=c.view.degraded,
            )
        )
    c.rooms, c.withheld = _readable_rooms(c.target, c.here, all_rooms=c.scope == "all_rooms")
    c.person_id: str | None = None
    if c.author is not None and c.author.strip():
        c.view = await c.room.scope()
        if not c.view.available:
            _record(c.metrics, "record_query", "unavailable", reason="roster")
            return (True, None, {"status": "unavailable", "note": _RECORD_QUERY_OFF})
        c.matches = c.view.find(c.author)
        if not c.matches:
            _record(c.metrics, "record_query", "nothing_found", reason="author")
            return (True, None, {"status": "nothing_found", "note": _RECORD_QUERY_NO_AUTHOR})
        if len(c.matches) > 1:
            _record(c.metrics, "record_query", "ambiguous")
            return (
                True,
                None,
                {
                    "status": "ambiguous",
                    "could_be": [p.name for p in c.matches],
                    ("note"): "More than one person here goes by that. Ask which one.",
                },
            )
        c.person_id = c.matches[0].person_id
    return (False, None, None)


async def _record_query__p2(c):
    try:
        c.hits, c.more, c.near = await c.record.query(
            RecordQuery(
                rooms=tuple((r.platform, cid) for r in c.rooms for cid in sorted(r.ids)),
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                person_id=c.person_id,
                text=c.text.strip() if c.text and c.text.strip() else None,
                limit=RECORD_QUERY_MAX,
            ),
            as_of_ms=c.as_of_ms,
        )
    except Exception:
        log.exception("record_query failed")
        _record(c.metrics, "record_query", "unavailable")
        return (True, None, {"status": "unavailable", "note": _RECORD_QUERY_OFF})
    if not c.hits:
        _record(c.metrics, "record_query", "nothing_found", rooms=len(c.rooms), withheld=c.withheld)
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": _RECORD_QUERY_NONE + (_RECORD_QUERY_WITHHELD if c.withheld else ("")),
            },
        )
    c.rows = [c.persons.name_transcript([h.row], platform=h.platform)[0] for h in c.hits]
    if c.media_join is not None:
        c.rows = list(await c.media_join.enrich(c.rows, path="load_transcript"))
    c.out = c._record_messages(c.hits, c.rows, rooms=c.rooms, near=c.near, now=c.now)
    c.notes = []
    if c.more:
        c.notes.append(_RECORD_QUERY_MORE)
    if any(h.deleted for h in c.hits):
        c.notes.append(_RECORD_QUERY_DELETED)
    if any(h.row.reactions is not None and h.row.reactions.recorded for h in c.hits):
        c.notes.append(_REACTIONS_NOTE)
    if c.near:
        c.notes.append(_RECORD_QUERY_NEAR)
    if c.withheld:
        c.notes.append(_RECORD_QUERY_WITHHELD.strip())
    _record(
        c.metrics,
        "record_query",
        "ok",
        rows=len(c.out),
        more=c.more,
        rooms=len(c.rooms),
        withheld=c.withheld,
        near=len(c.near),
    )
    c.result: dict = {"status": "ok", "messages": c.out}
    if c.observations is not None:
        c.src_pairs: list[tuple[SourceEvent, str]] = []
        c.src_failed = False
        c.src_more = 0
        for _w1 in c.rooms:
            c.rf = _w1
            c.label = c.rf.label or platform_name(c.rf.platform)
            for _w2 in sorted(c.rf.ids):
                c.cid = _w2
                c.found = await c._room_source_events(
                    SourceScope(platform=c.rf.platform, account_key=(""), conversation_id=c.cid),
                    since_ms=c.start_ms,
                    as_of_ms=c.as_of_ms,
                )
                if c.found is None:
                    c.src_failed = True
                    continue
                c.ev, c.source_more = c.found
                c.src_more += c.source_more
                c.src_pairs += [(e, c.label) for e in c.ev if e.at_ms <= c.end_ms]
        if c.src_failed:
            c.notes.append(
                "Some rooms' recorded source events could not be read; the messages are complete."
            )
        if c.src_pairs:
            c.src_pairs.sort(key=lambda el: el[0].at_ms)
            c.shown, c.dropped = (
                c.src_pairs[:_RECORD_SOURCE_MAX],
                c.src_pairs[_RECORD_SOURCE_MAX:],
            )
            c.index_of = {h.row.id: i for i, h in enumerate(c.hits, 1)}
            c.result["source_events"] = [
                {
                    "room": label,
                    **_source_event_item(
                        e,
                        now=c.now,
                        on=c.index_of.get(e.target_message_id)
                        if e.target_message_id is not None
                        else None,
                    )[("source_event")],
                }
                for e, label in c.shown
            ]
            if c.dropped or c.src_more:
                c.result["source_events_more"] = len(c.dropped) + c.src_more
    if c.notes:
        c.result["note"] = " ".join(c.notes)
    return (True, None, _memory_result(c.result))
    return (False, None, None)
