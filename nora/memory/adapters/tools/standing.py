from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Literal

from nora.agent.character_read import account_key, person_named, room_named, rooms_listing
from nora.agent.lineage import profile_lines_for
from nora.agent.rollup_read import iso as rollup_iso
from nora.agent.rollup_read import parse_interval, render_row, visible_rows
from nora.agent.security import scan_identifiers
from nora.agent.standing_ledger import ledger_permits
from nora.agent.standing_read import render_standing
from nora.agent.toolkit.results import _ROLLUP_TIME, _record
from nora.agent.toolkit.rooms import _ambiguous_person
from nora.agent.turn_recording import record_requirement
from nora.domain.lineage import (
    ROW,
    RoomFacts,
    room_key,
)
from nora.domain.lineage import message as message_lineage
from nora.domain.models import (
    PersonRef,
)
from nora.domain.profile import (
    ProfileView,
)
from nora.domain.rollup_read import ROLLUP_READ_KINDS, RollupRead, RollupReadQuery
from nora.domain.self_presence import platform_name
from nora.domain.turn_record import DisclosureRequirement
from nora.language.profile_render import account_text, authored_identity
from nora.memory.adapters.tools.shared import (
    _CHARACTER_NONE,
    _CHARACTER_OFF,
    _CHARACTER_OK,
    _CHARACTER_PROFILE_OK,
    _CHARACTER_WITHHELD,
    _ROLLUP_EVERYONE,
    _ROLLUP_KIND,
    _ROLLUP_MORE,
    _ROLLUP_NO_BUILD,
    _ROLLUP_NO_PERSON,
    _ROLLUP_NONE,
    _ROLLUP_OFF,
    _ROLLUP_SUBJECT,
    _ROLLUP_UNLABELLED,
    _ROLLUP_WITHHELD,
    _STANDING_NONE,
    _STANDING_OFF,
    _STANDING_WITHHELD,
)

log = logging.getLogger("nora.tools")


def _make__rollup_name(deps):
    persons = deps.persons

    def _rollup_name(subject: str) -> str:
        ref = persons.by_person.get(subject)
        return ref.name if ref is not None else "someone"

    return _rollup_name


def _make_rollup_read(deps):
    async def rollup_read(
        kind: Literal["stay", "trip", "colocation", "outing", "daybook", "tie"],
        subject: str,
        interval: str,
    ) -> dict:
        """What people here did, as computed rows: outings together, stays, trips.
        Call this when you need a pattern over days or weeks.

        SHOULD: name people as they are named here. Rows come oldest first,
        each with `kind`, `subject`, `from` and `to`, the key a note cites it
        by. Statuses: ok, nothing_found, ambiguous, unavailable.

        Args:
            kind: stay, trip or daybook is one person; colocation and outing
                are who was together; tie is two people's interaction in a week.
            subject: A person's name used in this conversation, or several
                separated by commas for colocation, outing and tie.
            interval: ISO 8601, a date or start/end; an end date includes that
                whole day.
        """
        c = SimpleNamespace(
            kind=kind,
            subject=subject,
            interval=interval,
            metrics=deps.metrics,
            persons=deps.persons,
            rollup=deps.rollup,
            _been_everyone=deps._been_everyone,
            _lineage_target=deps._lineage_target,
            _rollup_name=_make__rollup_name(deps),
            by_id=deps.by_id,
        )
        k_, _, v_ = await _rollup_read__fetch(c)
        if k_:
            return v_
        k_, _, v_ = await _rollup_read__finish(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return rollup_read


def _make_standing_read(deps):
    ledger = deps.ledger
    metrics = deps.metrics
    room = deps.room
    standing_head = deps.standing_head
    _lineage_target = deps._lineage_target
    _rollup_name = _make__rollup_name(deps)
    bound_conversation_id = deps.bound_conversation_id

    async def standing_read() -> dict:
        """Read Standing and its history. Call this when you need the full ledger.

        Returns the legacy sections and their revision, plus all visible ledger
        entries, each with its revision and history. Statuses: ok, nothing_found, unavailable.
        """
        if standing_head is None:
            _record(metrics, "standing_read", "unavailable", reason="unwired")
            return {"status": "unavailable", "note": _STANDING_OFF}
        bound = await _lineage_target("standing_read")
        if bound is None:
            return {"status": "unavailable", "note": _STANDING_OFF}
        target = bound[0]
        try:
            head = await standing_head.read_head(
                platform=room.platform,
                conversation_id=bound_conversation_id,
            )
        except Exception:
            log.exception("standing_read failed")
            _record(metrics, "standing_read", "unavailable")
            return {"status": "unavailable", "note": _STANDING_OFF}
        shown, withheld = (None, 0) if head is None else render_standing(head, target)
        shown = shown or {}
        if ledger is not None and target.room is not None:
            try:
                entries = await ledger.read(
                    unit=target.room.unit,
                    platform=room.platform,
                    conversation_id=bound_conversation_id,
                )
                visible = [e for e in entries if ledger_permits(e, target)]
                withheld += len(entries) - len(visible)
                if visible:
                    for e in visible:
                        record_requirement(
                            DisclosureRequirement(
                                room_key(e.platform, e.conversation_id),
                                e.lineage.section(ROW),
                                e.exclude_persons,
                            )
                        )
                    shown["entries"] = [
                        {
                            "id": e.id,
                            "kind": e.kind,
                            "scope": e.scope,
                            "status": e.status,
                            "subject": e.subject,
                            "body": e.body,
                            "revision": e.revision,
                            "opened_at": rollup_iso(e.opened_at),
                            "last_revised_at": rollup_iso(e.last_revised_at),
                            "last_moved_at": (
                                rollup_iso(e.last_moved_at) if e.last_moved_at is not None else None
                            ),
                            "closed_at": rollup_iso(e.closed_at)
                            if e.closed_at is not None
                            else None,
                            "note_id": e.note_id,
                            "owed_to": [
                                "the room" if p == "room" else _rollup_name(p) for p in e.owed_to
                            ],
                            "due_at": rollup_iso(e.due_at) if e.due_at is not None else None,
                            "note_ids": list(e.note_ids),
                            "decision_ids": list(e.decision_ids),
                            "history": [
                                {
                                    "revision": h.revision,
                                    "subject": h.subject,
                                    "body": h.body,
                                    "status": h.status,
                                    "scope": h.scope,
                                    "last_revised_at": rollup_iso(h.last_revised_at),
                                }
                                for h in await ledger.history(unit=e.unit, kind=e.kind, id=e.id)
                                if ledger_permits(h, target)
                            ],
                        }
                        for e in visible
                    ]
            except Exception:
                log.exception("standing ledger read failed")
                _record(metrics, "standing_read", "unavailable")
                return {"status": "unavailable", "note": _STANDING_OFF}
        if not shown:
            _record(metrics, "standing_read", "nothing_found", withheld=withheld)
            return {
                "status": "nothing_found",
                "note": _STANDING_WITHHELD if withheld else _STANDING_NONE,
            }
        _record(
            metrics,
            "standing_read",
            "ok",
            withheld=withheld,
            revision=head is not None and head.revision_id is not None,
        )
        result: dict = {"status": "ok", **shown}
        if withheld:
            result["note"] = _STANDING_WITHHELD
        return result

    return standing_read


def _make_character_read(deps):
    async def character_read(entity: Literal["room", "person"], subject: str) -> dict:
        """Read the current account of a room of this group or of one person in it.
        Call this when you need how a room talks and decides, or what someone is
        like here.

        SHOULD: name a room by its label and a person as they are named here.
        Returns each account with its `as_of` time, and its `revision` while
        the culture store backs it. Statuses: ok, nothing_found, unavailable.

        Args:
            entity: room for a room's culture, person for one person's account.
            subject: The room's label, or the person's name.
        """
        c = SimpleNamespace(
            entity=entity,
            subject=subject,
            content_checks=deps.content_checks,
            culture=deps.culture,
            lineage=deps.lineage,
            metrics=deps.metrics,
            persons=deps.persons,
            profile_read=deps.profile_read,
            room=deps.room,
            bound_conversation_id=deps.bound_conversation_id,
            now_fn=deps.now_fn,
        )
        k_, _, v_ = await _character_read__wired(c)
        if k_:
            return v_
        k_, _, v_ = await _character_read__heads(c)
        if k_:
            return v_
        k_, _, v_ = await _character_read__record(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return character_read


async def _rollup_read__fetch(c):
    if c.rollup is None:
        _record(c.metrics, "rollup_read", "unavailable", reason="unwired")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_OFF})
    try:
        c.start_ms, c.end_ms = parse_interval(c.interval)
    except (ValueError, OverflowError):
        _record(c.metrics, "rollup_read", "unavailable", reason="time")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_TIME})
    if c.kind not in ROLLUP_READ_KINDS:
        _record(c.metrics, "rollup_read", "unavailable", reason="kind")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_KIND})
    c.bound = await c._lineage_target("rollup_read")
    if c.bound is None:
        return (True, None, {"status": "unavailable", "note": _ROLLUP_OFF})
    c.target, c.here, c.view = c.bound
    if not c.view.available:
        _record(c.metrics, "rollup_read", "unavailable", reason="roster")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_OFF})
    c.names = [n.strip() for n in str(c.subject).split(",") if n.strip()]
    c.everyone = any(n.casefold() in c._been_everyone for n in c.names)
    if not c.names or (c.kind in ("stay", "trip", "daybook") and len(c.names) > 1):
        _record(c.metrics, "rollup_read", "unavailable", reason="subject")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_SUBJECT})
    if c.everyone and len(c.names) > 1:
        _record(c.metrics, "rollup_read", "unavailable", reason="subject")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_EVERYONE})
    c.people: list[PersonRef] = []
    if c.everyone:
        c.people = list(c.view.people)
        if not c.people:
            _record(c.metrics, "rollup_read", "nothing_found", reason="subject")
            return (True, None, {"status": "nothing_found", "note": _ROLLUP_NO_PERSON})
    for _w1 in [] if c.everyone else c.names:
        c.name = _w1
        c.matches = c.view.find(c.name) or tuple(
            p for p in c.view.people if c.by_id and p.person_id == c.name
        )
        if not c.matches:
            _record(c.metrics, "rollup_read", "nothing_found", reason="subject")
            return (True, None, {"status": "nothing_found", "note": _ROLLUP_NO_PERSON})
        if len(c.matches) > 1:
            _record(c.metrics, "rollup_read", "ambiguous")
            return (True, None, _ambiguous_person(c.matches, c.view.people, by_id=c.by_id))
        c.people.append(c.matches[0])
    c.subjects = tuple(dict.fromkeys(c.persons.subject_id_for(p.person_id) for p in c.people))
    try:
        c.got: RollupRead = await c.rollup.read(
            RollupReadQuery(
                kind=c.kind,
                unit=c.here.unit,
                subjects=c.subjects,
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                match=("all") if len(c.names) > 1 and (not c.everyone) else "any",
            )
        )
    except Exception:
        log.exception("rollup_read failed")
        _record(c.metrics, "rollup_read", "unavailable")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_OFF})
    if c.got.build_id is None or c.got.through_ms is None:
        _record(c.metrics, "rollup_read", "unavailable", reason="build")
        return (True, None, {"status": "unavailable", "note": _ROLLUP_NO_BUILD})
    c.shown, c.withheld = visible_rows(c.got.rows, c.target)
    for _w2 in c.shown:
        c.row = _w2
        record_requirement(DisclosureRequirement(None, c.row.lineage.section("row")))
    c.through = rollup_iso(c.got.through_ms)
    c.notes: list[str] = []
    if not c.shown:
        _record(
            c.metrics,
            "rollup_read",
            "nothing_found",
            kind=c.kind,
            withheld=c.withheld,
            more=c.got.more,
        )
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "through": c.through,
                "note": _ROLLUP_WITHHELD if c.withheld else _ROLLUP_NONE,
            },
        )

    def _room_label(key: str) -> str:
        found = c.target.resolve(key)
        return found[0].label or platform_name(found[0].platform) if found else "another room"

    c._room_label = _room_label
    c.out = [render_row(r, name_of=c._rollup_name, room_label=c._room_label) for r in c.shown]
    if c.got.more:
        c.notes.append(_ROLLUP_MORE)
    if c.withheld:
        c.notes.append(_ROLLUP_WITHHELD)
    if any("unlabelled" in str(r.get("place", "")) for r in c.out):
        c.notes.append(_ROLLUP_UNLABELLED)
    _record(
        c.metrics,
        "rollup_read",
        "ok",
        kind=c.kind,
        rows=len(c.out),
        withheld=c.withheld,
        more=c.got.more,
    )
    c.result: dict = {"status": "ok", "through": c.through, "rows": c.out}
    return (False, None, None)


async def _rollup_read__finish(c):
    if c.notes:
        c.result["note"] = " ".join(c.notes)
    return (True, None, c.result)
    return (False, None, None)


async def _character_read__wired(c):
    if c.profile_read is None and c.culture is None:
        _record(c.metrics, "character_read", "unavailable", reason="unwired")
        return (True, None, {"status": "unavailable", "note": _CHARACTER_OFF})
    if c.lineage is None:
        _record(c.metrics, "character_read", "unavailable", reason="unwired")
        return (True, None, {"status": "unavailable", "note": _CHARACTER_OFF})
    c.here = c.lineage.render_room(
        platform=c.room.platform, conversation_id=c.bound_conversation_id
    ).room
    if c.here is None:
        _record(c.metrics, "character_read", "unavailable", reason="room")
        return (True, None, {"status": "unavailable", "note": _CHARACTER_OFF})
    c.unit_rooms = tuple(r for r in c.lineage.rooms if r.unit == c.here.unit)
    c.wanted: list[tuple[RoomFacts, str | None]] = []
    if c.entity == "room":
        c.found = room_named(c.unit_rooms, c.subject)
        c.wanted = [(c.found, None)] if c.found is not None else []
    elif c.entity == "person":
        c.unit_people = {
            pid: ref for pid, ref in c.persons.by_person.items() if rooms_listing(c.unit_rooms, pid)
        }
        c.person_id = person_named(c.unit_people, c.subject)
        c.listing = rooms_listing(c.unit_rooms, c.person_id) if c.person_id is not None else ()
        c.wanted = [(c.listing[0], c.person_id)] if c.listing else []
    else:
        _record(c.metrics, "character_read", "unavailable", reason="entity")
        return (True, None, {"status": "unavailable", "note": _CHARACTER_OFF})
    c.keyed = [
        (r, pid, c.key)
        for r, pid in c.wanted
        if (c.__dict__.__setitem__("key", account_key(r, pid)) or c.key) is not None
    ]
    return (False, None, None)


async def _character_read__heads(c):
    if c.profile_read is not None:
        try:
            c.profile_heads = await asyncio.gather(
                *(c.profile_read.account_heads(key=key) for _, _, key in c.keyed)
            )
        except Exception:
            log.exception("character_read (profile) failed")
            _record(c.metrics, "character_read", "unavailable", reason="store")
            return (True, None, {"status": "unavailable", "note": _CHARACTER_OFF})
        c.target = c.lineage.render_room(
            platform=c.room.platform, conversation_id=c.bound_conversation_id
        )

        def name_of(person_id: str) -> str:
            named = c.persons.by_person.get(person_id)
            return named.name if named is not None else "someone"

        c.name_of = name_of
        c.profile_accounts = []
        for _w1 in zip(c.keyed, c.profile_heads, strict=True):
            (c.r, c.pid, c._), c.lines = _w1
            c.of = c.r.label or platform_name(c.r.platform)
            c.authored: tuple[str, ...] = ()
            if c.pid is not None and c.pid in c.persons.by_person:
                c.named = c.persons.by_person[c.pid]
                c.of = c.named.name
                c.authored = authored_identity(
                    name=c.named.name,
                    aliases=c.named.aliases,
                    platforms=tuple(
                        sorted(
                            {
                                platform_name(network)
                                for (network, _), owner in c.persons.index.items()
                                if owner == c.pid
                            }
                        )
                    ),
                )
            c.kept = await profile_lines_for(c.lines, c.target, c.room)
            c.origin = room_key(c.r.platform, sorted(c.r.ids)[0])
            for _w2 in c.kept:
                c.line = _w2
                record_requirement(
                    DisclosureRequirement(
                        c.origin,
                        (message_lineage(c.origin),),
                        c.line.exclude_persons,
                        c.line.location,
                        c.line.location_subjects,
                    )
                )
            c.rendered = account_text(
                ProfileView(entity=c.entity, of=c.of, lines=c.kept, authored=c.authored),
                name_of=c.name_of,
                now=c.now_fn(),
            )
            if c.rendered is None:
                continue
            c.text, c.written = c.rendered
            c.entry: dict = {"room": c.of, "account": c.text}
            if c.written is not None:
                c.entry["as_of"] = rollup_iso(c.written)
            c.profile_accounts.append(c.entry)
        if not c.profile_accounts:
            _record(c.metrics, "character_read", "nothing_found", entity=c.entity)
            return (True, None, {"status": "nothing_found", "note": _CHARACTER_NONE})
        _record(
            c.metrics, "character_read", "ok", entity=c.entity, accounts=len(c.profile_accounts)
        )
        return (
            True,
            None,
            {
                "status": "ok",
                "entity": c.entity,
                "accounts": c.profile_accounts,
                ("note"): _CHARACTER_PROFILE_OK,
            },
        )
    if c.culture is None:
        _record(c.metrics, "character_read", "unavailable", reason="unwired")
        return (True, None, {"status": "unavailable", "note": _CHARACTER_OFF})
    try:
        c.heads = await asyncio.gather(*(c.culture.head(key=key) for _, _, key in c.keyed))
    except Exception:
        log.exception("character_read failed")
        _record(c.metrics, "character_read", "unavailable", reason="store")
        return (True, None, {"status": "unavailable", "note": _CHARACTER_OFF})
    c.held = [
        (r, head)
        for (r, _, _), head in zip(c.keyed, c.heads, strict=True)
        if head is not None and head.body.strip()
    ]
    c.accounts = [
        {
            "room": r.label or platform_name(r.platform),
            "account": head.body,
            "as_of": rollup_iso(head.written_at),
            ("revision"): head.id,
        }
        for r, head in c.held
        if scan_identifiers(head.body, content_checks=c.content_checks) is None
    ]
    return (False, None, None)


async def _character_read__record(c):
    for _w1 in c.held:
        c.r, c.head = _w1
        if scan_identifiers(c.head.body, content_checks=c.content_checks) is None:
            record_requirement(
                DisclosureRequirement(
                    room_key(c.r.platform, sorted(c.r.ids)[0]),
                    c.head.lineage.section("body"),
                    tuple(c.head.exclude_persons),
                )
            )
    c.withheld = len(c.held) - len(c.accounts)
    if not c.accounts and c.withheld:
        _record(c.metrics, "character_read", "nothing_found", entity=c.entity, withheld=c.withheld)
        return (
            True,
            None,
            {"status": "nothing_found", "withheld": c.withheld, "note": _CHARACTER_WITHHELD},
        )
    if not c.accounts:
        _record(c.metrics, "character_read", "nothing_found", entity=c.entity)
        return (True, None, {"status": "nothing_found", "note": _CHARACTER_NONE})
    _record(c.metrics, "character_read", "ok", entity=c.entity, accounts=len(c.accounts))
    c.result: dict = {
        "status": "ok",
        "entity": c.entity,
        "accounts": c.accounts,
        "note": _CHARACTER_OK,
    }
    if c.withheld:
        c.result["withheld"] = c.withheld
    return (True, None, c.result)
    return (False, None, None)
