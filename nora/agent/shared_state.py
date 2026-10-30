"""Reads the unit's shared cognitive state, ONCE, from the canonical stores.

`design/notebook-memory-types/20-shared-cognitive-state.md`: one typed
projection over the existing stores — open notes, open ledger judgements,
pending and recently resolved act rows, self-wakes, working-set findings and
her own delivered sends — with every source's coverage named, so a failed
read is `unavailable`, never `nothing_found` (R-27). Every source's read AND
entry-build sit inside its own `try`: a row shape the projection did not
expect is that source's `unavailable`, never an exception escaping to a
caller that would read it as an empty unit.

`room` is the requesting destination `(platform, conversation_id)`; `None`
is the unit read (the session — the unit IS the read boundary, DEC-231). A
destination read keeps unit-scoped entries and its own room's entries
readable, subjects other rooms' ledger entries to the caller's `permit`
predicate, and renders every other room's scoped entries `permitted=False` —
existence visible, content withheld. A person's reminder never carries its
text into the shared block: existence only, in any read.

A store left `None` is a source this view was never asked for, not an
unavailable one — the coverage line names only the sources that ran.
`nora.language.shared_render` renders the view; this module only reads.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from nora.domain.shared_state import SharedEntry, SharedView

log = logging.getLogger("nora.agent.shared_state")

#: How far back the "said lately" and act-history reads reach.
_SEND_LOOKBACK_MS = 24 * 3600_000
_HISTORY_LOOKBACK_MS = 7 * 24 * 3600_000


def _ms(dt: datetime | None) -> int:
    return 0 if dt is None else int(dt.timestamp() * 1000)


def _room_of(entry_room: str) -> str:
    return entry_room


async def read_shared(
    *,
    unit: str,
    rooms: Sequence[tuple[str, str]],
    ai_peer: str,
    now_ms: int,
    room: tuple[str, str] | None = None,
    notes: Any = None,
    ledger: Any = None,
    reminders: Any = None,
    record_query: Any = None,
    standing: Any = None,
    wakes: Sequence[Any] | None = None,
    conclusions: Sequence[str] = (),
    unresolved: Sequence[str] = (),
    permit: Callable[[Any], bool] | None = None,
    send_limit: int = 6,
    history_limit: int = 50,
) -> SharedView:
    """One view, assembled per source so a failed read is named, not absent.

    `permit`, when given, is the destination's ledger-disclosure predicate —
    the same `ledger_permits` the ledger read applies. For a `room` read, an
    entry scoped to a DIFFERENT room is `permitted=False` (withheld); the
    ledger predicate can further withhold unit/other-room ledger entries the
    lineage rules refuse. `wakes`, `conclusions` and `unresolved` are read by
    the caller that owns them and passed in.
    """
    entries: list[SharedEntry] = []
    sources: list[tuple[str, str]] = []
    partial = False
    room_set = set(rooms)
    dest = f"{room[0]}:{room[1]}" if room else None

    def scoped(entry_scope: str, entry_room: tuple[str, str] | None = None,
               *, ledger_entry: Any = None) -> bool:
        """Whether THIS read may show the entry's content to its audience:
        the unit read sees everything; a destination read sees unit-scoped
        entries, its own room's, and ledger entries the permit admits."""
        if room is None:
            return True
        if entry_scope == "unit":
            in_scope = True
        elif entry_room is not None:
            in_scope = entry_room == room
        else:
            in_scope = entry_scope == dest
        if not in_scope:
            return False
        if ledger_entry is not None and permit is not None:
            return permit(ledger_entry)
        return True

    if notes is not None:
        try:
            open_notes = await notes.standing(unit=unit)
            sources.append(("notes", "ok" if open_notes else "nothing_found"))
            for n in open_notes:
                detail = " · ".join(
                    part for part in (
                        getattr(n, "uncertainty", "") or "",
                        f"closes when {n.closing_condition}"
                        if getattr(n, "closing_condition", "") else "",
                    ) if part
                )
                entries.append(SharedEntry(
                    kind="note", id=n.id, scope="unit", status="open",
                    subject=n.title, detail=detail,
                    refs=(f"note:{n.id}",),
                ))
        except Exception:
            log.exception("shared view: notes read failed for %s", unit)
            sources.append(("notes", "unavailable"))

    if ledger is not None:
        try:
            raw: dict[str, Any] = {}
            for platform, conversation_id in rooms:
                for e in await ledger.read(
                    unit=unit, platform=platform, conversation_id=conversation_id,
                ):
                    raw.setdefault(e.id, e)
            sources.append(("ledger", "ok" if raw else "nothing_found"))
            for e in sorted(raw.values(), key=lambda e: e.last_revised_at,
                            reverse=True):
                scope = (
                    "unit" if e.scope == "unit"
                    else f"{e.platform}:{e.conversation_id}"
                )
                entries.append(SharedEntry(
                    kind=e.kind, id=e.id, scope=scope,
                    status=e.status, subject=e.subject,
                    detail=e.body,
                    revision=str(e.revision),
                    refs=(e.id, *(f"note:{i}" for i in e.note_ids)),
                    at_ms=e.last_revised_at,
                    provenance="cognition",
                    permitted=scoped(
                        scope, (e.platform, e.conversation_id),
                        ledger_entry=e,
                    ),
                ))
        except Exception:
            log.exception("shared view: ledger read failed for %s", unit)
            sources.append(("ledger", "unavailable"))

    sends: dict[tuple[str, str], tuple[Any, ...]] = {}
    if record_query is not None:
        try:
            for platform, conversation_id in rooms:
                sends[(platform, conversation_id)] = tuple(await record_query.own_sends(
                    [(platform, conversation_id)],
                    start_ms=now_ms - _SEND_LOOKBACK_MS,
                    end_ms=now_ms, limit=send_limit,
                ))
            total = sum(len(hits) for hits in sends.values())
            sources.append(("sends", "ok" if total else "nothing_found"))
            if any(len(hits) >= send_limit for hits in sends.values()):
                partial = True
            for (platform, conversation_id), hits in sends.items():
                for h in hits:
                    scope = f"{platform}:{conversation_id}"
                    entries.append(SharedEntry(
                        kind="send", id=str(h.row.id), scope=scope,
                        status="sent",
                        subject=str(h.row.body or "").splitlines()[0][:140],
                        at_ms=h.row.ts, provenance="nora",
                        permitted=scoped(scope, (platform, conversation_id)),
                    ))
        except Exception:
            log.exception("shared view: sends read failed for %s", unit)
            sources.append(("sends", "unavailable"))

    if standing is not None:
        try:
            findings = 0
            for platform, conversation_id in rooms:
                position = await standing.read(
                    platform=platform, conversation_id=conversation_id)
                scope = f"{platform}:{conversation_id}"
                allowed = scoped(scope, (platform, conversation_id))
                for item in getattr(position, "working_set", ()) or ():
                    findings += 1
                    detail = item.what
                    if item.bears_on:
                        detail += f" (for {item.bears_on})"
                    entries.append(SharedEntry(
                        kind="finding", id=f"{scope}#{item.found_at}",
                        scope=scope, status="open",
                        subject=detail, at_ms=item.found_at,
                        provenance=f"fold:{item.source}",
                        permitted=allowed,
                    ))
            sources.append(("findings", "ok" if findings else "nothing_found"))
        except Exception:
            log.exception("shared view: findings read failed for %s", unit)
            sources.append(("findings", "unavailable"))

    if reminders is not None:
        try:
            pending = [
                r for r in await reminders.pending_of(ai_peer)
                if (r.platform, r.conversation_id) in room_set
            ]
            since = datetime.fromtimestamp(
                (now_ms - _HISTORY_LOOKBACK_MS) / 1000, UTC)
            recent = await reminders.recent_in(
                rooms, requester_person_id=ai_peer, since=since,
                limit=history_limit,
            )
            human = [
                r
                for platform, conversation_id in rooms
                for r in await reminders.pending_for(conversation_id)
                if r.platform == platform
                and r.requester_person_id != ai_peer
            ]
            sources.append((
                "acts",
                "ok" if (pending or recent or human) else "nothing_found",
            ))
            if len(recent) >= history_limit:
                partial = True

            def act_entry(r: Any, *, status: str, delivery: str) -> SharedEntry:
                payload = r.payload or {}
                subject_ref = ""
                if payload.get("subject_kind") and payload.get("subject_id"):
                    subject_ref = (
                        f"{payload['subject_kind']}:{payload['subject_id']}")
                action = payload.get("action") or payload.get("intent") or ""
                refs = (subject_ref,) if subject_ref else ()
                own = r.requester_person_id == ai_peer
                scope = f"{r.platform}:{r.conversation_id}"
                # A person's reminder never lends its text to the shared
                # block — the scheduled-act FACT is unit knowledge, the words
                # are the person's own.
                text = (
                    str(r.text or "").splitlines()[0][:140]
                    if own else "a person's reminder"
                )
                return SharedEntry(
                    kind="act", id=str(r.id), scope=scope,
                    status=status, subject=text,
                    destination=scope,
                    action=str(action), subject_ref=subject_ref,
                    refs=refs, at_ms=_ms(r.due_at),
                    provenance="nora" if own else "person",
                    delivery=delivery,
                    permitted=scoped(scope, (r.platform, r.conversation_id)),
                )

            sends_read_ok = (
                record_query is not None
                and ("sends", "unavailable") not in sources
            )
            entries.extend(act_entry(r, status="pending", delivery="")
                           for r in pending)
            entries.extend(act_entry(r, status="pending", delivery="")
                           for r in human)
            for r in recent:
                fired_ms = _ms(getattr(r, "fired_at", None))
                room_sends = sends.get((r.platform, r.conversation_id), ())
                created_ms = (
                    int(r.created_at.timestamp() * 1000) if r.created_at else 0
                )
                # Delivery labels are row-and-message facts, never a proximity
                # guess: `sent` when the row says so or carries delivered
                # evidence; `none` only when provable — never fired, or the
                # sends read answered and NOTHING of hers reached that
                # destination since the act was created; `unknown` when the
                # check could not rule a send out (fired then failed, sends
                # source absent, or a send exists that may be this row's).
                if r.outcome == "sent" or (r.payload or {}).get(
                    "delivered_message_ids"
                ):
                    delivery = "sent"
                elif not fired_ms or (sends_read_ok and not any(
                    h.row.ts >= created_ms for h in room_sends
                )):
                    delivery = "none"
                else:
                    delivery = "unknown"
                entries.append(act_entry(
                    r, status=r.outcome or r.state.value, delivery=delivery,
                ))
        except Exception:
            log.exception("shared view: acts read failed for %s", unit)
            sources.append(("acts", "unavailable"))

    if wakes:
        sources.append(("wakes", "ok"))
        for w in wakes:
            entries.append(SharedEntry(
                kind="wake", id=str(w.wake_ref), scope="unit", status="pending",
                subject=" ".join(w.subject_rows)[:140],
                refs=tuple(w.subject_rows), at_ms=w.at_ms or 0,
                provenance="nora",
            ))

    if conclusions:
        sources.append(("conclusions", "ok"))
        for i, line in enumerate(conclusions):
            entries.append(SharedEntry(
                kind="conclusion", id=f"c{i}", scope="unit", status="recent",
                subject=line[:140], provenance="cognition",
            ))

    statuses = [s for _, s in sources]
    if "unavailable" in statuses:
        status = "unavailable"
    elif not entries:
        status = "nothing_found"
    elif partial or "partial" in statuses:
        status = "partial"
    else:
        status = "ok"
    return SharedView(
        unit=unit, status=status, as_of_ms=now_ms,
        entries=tuple(entries), sources=tuple(sources),
        unresolved=tuple(unresolved),
    )


def shared_reader(
    *,
    notes: Any = None,
    ledger: Any = None,
    reminders: Any = None,
    record_query: Any = None,
    standing: Any = None,
    ai_peer: str,
    rooms_of_unit: Callable[[str], Sequence[tuple[str, str]]],
) -> Callable[..., Awaitable[SharedView]]:
    """The collaborator each assembly holds: `read(unit=…, room=…, permit=…,
    wakes=…, now_ms=…)` bound to the canonical stores and the unit→rooms
    lookup, so no assembler threads six stores through its own constructor
    for one block."""

    async def read(
        *, unit: str, now_ms: int,
        room: tuple[str, str] | None = None,
        permit: Callable[[Any], bool] | None = None,
        wakes: Sequence[Any] | None = None,
        conclusions: Sequence[str] = (),
        unresolved: Sequence[str] = (),
    ) -> SharedView:
        return await read_shared(
            unit=unit, rooms=rooms_of_unit(unit), ai_peer=ai_peer,
            now_ms=now_ms, room=room, notes=notes, ledger=ledger,
            reminders=reminders, record_query=record_query,
            standing=standing, wakes=wakes, conclusions=conclusions,
            unresolved=unresolved, permit=permit,
        )

    return read
