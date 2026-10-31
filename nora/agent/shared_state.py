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

from nora.domain.lineage import BODY, HOLD_OPEN, I_OWE, WORKING_SET
from nora.domain.shared_state import SharedEntry, SharedView, ViewStatus
from nora.domain.standing import WORKING_SET_TTL_HOURS

log = logging.getLogger("nora.agent.shared_state")

#: An ungated section map — the unit read and the lineage-off answer alike.
_ALL_SECTIONS = {BODY: True, HOLD_OPEN: True, I_OWE: True, WORKING_SET: True}

#: How far back the "said lately" and act-history reads reach.
_SEND_LOOKBACK_MS = 24 * 3600_000
_HISTORY_LOOKBACK_MS = 7 * 24 * 3600_000

#: The tool names whose toolUse/toolResult pairs the view reads as facts —
#: producer conclusions and act attempts.
_TURN_FACT_TOOLS = ("remind",)


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
    ledger: Any = None,
    reminders: Any = None,
    record_query: Any = None,
    standing: Any = None,
    traces: Any = None,
    wakes: Sequence[Any] | None = None,
    permit: Callable[[Any], bool] | None = None,
    permit_req: Callable[[Any], bool] | None = None,
    standing_permits: Callable[[Any], dict[str, bool]] | None = None,
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

    def scoped(entry_scope: str, entry_room: tuple[str, str] | None = None,
               *, ledger_entry: Any = None) -> bool:
        """Whether THIS read may show the entry's content to its audience:
        the unit read sees everything; a destination read sees unit-scoped
        entries, its own room's, and ledger entries the permit admits."""
        if room is None:
            return True
        # DEC-231: the unit is the read boundary — a destination inside it
        # sees the unit's whole internal state. The ONE kind with a narrower
        # canonical rule is the ledger, whose `permit` (lineage's own
        # `ledger_permits`) decides entry by entry; every other kind is unit
        # knowledge the room already shares.
        if ledger_entry is not None:
            return True if permit is None else permit(ledger_entry)
        return True

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
                        subject=" ".join(str(h.row.body or "").split())[:140]
                        or "message without text",
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
                # Standing is ROOM-scoped state, not unit knowledge: a
                # destination read takes only its own room's position and
                # working set — another room's is not even listed (the
                # legacy blocks never showed it either). The unit read
                # takes every room, unchanged.
                if room is not None and (platform, conversation_id) != room:
                    continue
                position = await standing.read(
                    platform=platform, conversation_id=conversation_id)
                scope = f"{platform}:{conversation_id}"
                if position is None:
                    continue
                # The SAME section gate the legacy blocks run
                # (`visible_sections` over the destination's RenderRoom):
                # a withheld section is dropped whole — never an existence
                # hint, the parity `derived_view`/`render_standing` keeps.
                # `room is None` is the unit read, which sees everything;
                # `standing_permits` absent means lineage off, same answer.
                allowed_sections = (
                    standing_permits(position)
                    if room is not None and standing_permits is not None
                    else _ALL_SECTIONS
                )
                sections_present = (
                    position.hold_open and allowed_sections[HOLD_OPEN],
                    position.i_owe and allowed_sections[I_OWE],
                )
                if allowed_sections[BODY] or any(sections_present):
                    detail = " · ".join(
                        part for part in (
                            position.hold_open and allowed_sections[HOLD_OPEN]
                            and f"open: {position.hold_open}",
                            position.i_owe and allowed_sections[I_OWE]
                            and f"owe: {position.i_owe}",
                        ) if part
                    )
                    entries.append(SharedEntry(
                        kind="standing", id=f"pos#{scope}", scope=scope,
                        status="open",
                        # A withheld BODY never swallows permitted sibling
                        # sections — each renders on its own gate, exactly as
                        # `render_standing` dropped them section-wise.
                        subject=(
                            position.body if allowed_sections[BODY]
                            else "a position is held, body withheld"
                        ),
                        detail=detail, at_ms=position.updated_at,
                        provenance="fold",
                    ))
                if allowed_sections[WORKING_SET]:
                    ttl_ms = int(WORKING_SET_TTL_HOURS * 3_600_000)
                    for item in getattr(position, "working_set", ()) or ():
                        # The 48-hour TTL `render_standing_working_set`
                        # applies: an expired finding is not knowledge.
                        if now_ms - item.found_at > ttl_ms:
                            continue
                        findings += 1
                        detail = item.source
                        if item.bears_on:
                            detail += f" (for {item.bears_on})"
                        entries.append(SharedEntry(
                            kind="finding", id=f"{scope}#{item.found_at}",
                            scope=scope, status="open",
                            subject=item.what, detail=detail,
                            at_ms=item.found_at,
                            provenance=f"fold:{item.source}",
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
                elif not fired_ms or (
                    sends_read_ok
                    and created_ms >= now_ms - _SEND_LOOKBACK_MS
                    and not any(h.row.ts >= created_ms for h in room_sends)
                ):
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

    unresolved_lines: list[str] = []
    if traces is not None:
        try:
            # The turn Record is the carrier: `conclude` toolUses are the
            # producer's explicit conclusions, and a `remind` result that
            # answered `unresolved` is the durable unresolved evidence —
            # both read back across restarts and across processes, newest
            # first. An unresolved act is discharged only by a NEWER `ok`
            # result for the same (subject, action): the record retains the
            # attempt until something valid resolved it.
            # The FACT read is the store's structural projection: toolUse/
            # toolResult pairs extracted by `toolUseId`, latest result per
            # (name, subject, action) decides — a newer `ok` closes only its
            # own key, a same-trace later result wins by block ordinal, and
            # the cap bounds CURRENT open identities, never closed history.
            facts = await traces.turn_facts(
                unit=unit, tool_names=_TURN_FACT_TOOLS)
            if facts.capped:
                partial = True
                sources.append(("turns", "partial"))
            else:
                sources.append(("turns", "ok"))
            for item in facts.open_items:
                allowed = (
                    room is None
                    or (permit_req is not None
                        and permit_req(item.requirements))
                )
                if not allowed:
                    continue
                args = item.arguments
                provenance = f"{item.occasion}:{item.tag_id}"
                record_ref = f"turn({item.handle})" if item.handle else provenance
                recorded_at = datetime.fromtimestamp(item.created_at / 1000, UTC).isoformat()
                evidence = f"source={record_ref}; recorded={recorded_at}"
                if item.name == "remind":
                    unresolved_lines.append(
                        f"remind {str(args.get('text'))[:80]!r} for "
                        f"{args.get('room') or 'the room'}: "
                        f"{str(args.get('subject') or '')[:80]!r} unresolved; "
                        f"action={args.get('action') or '?'}; "
                        f"requested_at={args.get('at') or '?'}; {evidence}"
                    )
                    continue
        except Exception:
            log.exception("shared view: turn records read failed for %s", unit)
            sources.append(("turns", "unavailable"))

    statuses = [s for _, s in sources]
    status: ViewStatus
    if "unavailable" in statuses:
        status = "unavailable"
    elif partial or "partial" in statuses:
        # Coverage comes before emptiness: a source that could not be fully
        # read is «unproven», never read as «nothing there».
        status = "partial"
    elif not entries and not unresolved_lines:
        status = "nothing_found"
    else:
        status = "ok"
    return SharedView(
        unit=unit, status=status, as_of_ms=now_ms,
        entries=tuple(entries), sources=tuple(sources),
        unresolved=tuple(unresolved_lines),
    )


def shared_reader(
    *,
    ledger: Any = None,
    reminders: Any = None,
    record_query: Any = None,
    standing: Any = None,
    traces: Any = None,
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
        permit_req: Callable[[Any], bool] | None = None,
        standing_permits: Callable[[Any], dict[str, bool]] | None = None,
        wakes: Sequence[Any] | None = None,
    ) -> SharedView:
        return await read_shared(
            unit=unit, rooms=rooms_of_unit(unit), ai_peer=ai_peer,
            now_ms=now_ms, room=room, ledger=ledger,
            reminders=reminders, record_query=record_query,
            standing=standing, traces=traces, wakes=wakes,
            permit=permit, permit_req=permit_req,
            standing_permits=standing_permits,
        )

    return read
