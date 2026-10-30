"""Reads the unit's shared cognitive state, ONCE, from the canonical stores.

`design/notebook-memory-types/20-shared-cognitive-state.md`: one typed
projection over the existing stores — open notes, open ledger judgements,
pending and recently resolved act rows, self-wakes, and her own delivered
sends — with every source's coverage named, so a failed read is
`unavailable`, never `nothing_found` (R-27). The destination paths (tag,
fire) pass `permit` so a ledger entry the room may not read comes back
`permitted=False`; the unit session asks unfiltered, the unit being the read
boundary (DEC-231).

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
#: A fired act's send either landed inside this window or the row went down
#: without one. Wider than the tick's own latency, narrower than a tick apart.
_DELIVERY_WINDOW_BEFORE_MS = 60_000
_DELIVERY_WINDOW_AFTER_MS = 180_000


def _ms(dt: datetime | None) -> int:
    return 0 if dt is None else int(dt.timestamp() * 1000)


async def read_shared(
    *,
    unit: str,
    rooms: Sequence[tuple[str, str]],
    ai_peer: str,
    now_ms: int,
    notes: Any = None,
    ledger: Any = None,
    reminders: Any = None,
    record_query: Any = None,
    wakes: Sequence[Any] | None = None,
    unresolved: Sequence[str] = (),
    permit: Callable[[Any], bool] | None = None,
    send_limit: int = 6,
    history_limit: int = 50,
) -> SharedView:
    """One view, assembled per source so a failed read is named, not absent.

    `permit`, when given, is the destination's ledger-disclosure predicate —
    the same `ledger_permits` the ledger read applies — and marks entries it
    refuses `permitted=False` rather than dropping them. `wakes` and
    `unresolved` are read by the caller that owns them (the session) and
    passed in, because the store they live in is already that caller's.
    """
    entries: list[SharedEntry] = []
    sources: list[tuple[str, str]] = []
    partial = False
    room_set = set(rooms)

    if notes is not None:
        try:
            open_notes = await notes.standing(unit=unit)
        except Exception:
            log.exception("shared view: notes read failed for %s", unit)
            sources.append(("notes", "unavailable"))
        else:
            sources.append(("notes", "ok" if open_notes else "nothing_found"))
            for n in open_notes:
                entries.append(SharedEntry(
                    kind="note", id=n.id, scope="unit", status="open",
                    subject=n.title, refs=(f"note:{n.id}",),
                ))

    if ledger is not None:
        raw: dict[str, Any] = {}
        failed = False
        for platform, conversation_id in rooms:
            try:
                for e in await ledger.read(
                    unit=unit, platform=platform, conversation_id=conversation_id,
                ):
                    raw.setdefault(e.id, e)
            except Exception:
                log.exception(
                    "shared view: ledger read failed for %s %s:%s",
                    unit, platform, conversation_id,
                )
                failed = True
        if failed:
            sources.append(("ledger", "unavailable"))
        else:
            sources.append(("ledger", "ok" if raw else "nothing_found"))
            for e in sorted(raw.values(), key=lambda e: e.last_revised_at,
                            reverse=True):
                entries.append(SharedEntry(
                    kind=e.kind, id=e.id,
                    scope=(
                        "unit" if e.scope == "unit"
                        else f"{e.platform}:{e.conversation_id}"
                    ),
                    status=e.status, subject=e.subject,
                    revision=str(e.revision),
                    refs=(e.id, *(f"note:{i}" for i in e.note_ids)),
                    at_ms=e.last_revised_at,
                    provenance="cognition",
                    permitted=True if permit is None else permit(e),
                ))

    sends: dict[tuple[str, str], tuple[Any, ...]] = {}
    if record_query is not None:
        try:
            for platform, conversation_id in rooms:
                sends[(platform, conversation_id)] = tuple(await record_query.own_sends(
                    [(platform, conversation_id)],
                    start_ms=now_ms - _SEND_LOOKBACK_MS,
                    end_ms=now_ms, limit=send_limit,
                ))
        except Exception:
            log.exception("shared view: sends read failed for %s", unit)
            sources.append(("sends", "unavailable"))
        else:
            total = sum(len(hits) for hits in sends.values())
            sources.append(("sends", "ok" if total else "nothing_found"))
            if any(len(hits) >= send_limit for hits in sends.values()):
                partial = True
            for (platform, conversation_id), hits in sends.items():
                for h in hits:
                    entries.append(SharedEntry(
                        kind="send", id=str(h.message_id),
                        scope=f"{platform}:{conversation_id}", status="sent",
                        subject=str(h.text or "").splitlines()[0][:140],
                        at_ms=h.at_ms, provenance="nora",
                    ))

    if reminders is not None:
        try:
            pending = [
                r for r in await reminders.pending_of(ai_peer)
                if (r.platform, r.conversation_id) in room_set
            ]
            since = datetime.fromtimestamp(
                (now_ms - _HISTORY_LOOKBACK_MS) / 1000, UTC)
            recent = [
                r for r in await reminders.history(
                    requester_person_id=ai_peer, since=since, limit=history_limit,
                )
                if (r.platform, r.conversation_id) in room_set
            ]
            human = [
                r
                for platform, conversation_id in rooms
                for r in await reminders.pending_for(conversation_id)
                if r.platform == platform
                and r.requester_person_id != ai_peer
            ]
        except Exception:
            log.exception("shared view: acts read failed for %s", unit)
            sources.append(("acts", "unavailable"))
        else:
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
                return SharedEntry(
                    kind="act", id=str(r.id),
                    scope=f"{r.platform}:{r.conversation_id}",
                    status=status, subject=str(r.text or "").splitlines()[0][:140],
                    destination=f"{r.platform}:{r.conversation_id}",
                    action=str(action), subject_ref=subject_ref,
                    refs=refs, at_ms=_ms(r.due_at),
                    provenance=(
                        "nora" if r.requester_person_id == ai_peer
                        else f"person:{r.requester_person_id}"
                    ),
                    delivery=delivery,
                )

            entries.extend(act_entry(r, status="pending", delivery="")
                           for r in pending)
            entries.extend(act_entry(r, status="pending", delivery="")
                           for r in human)
            for r in recent:
                fired_ms = _ms(getattr(r, "fired_at", None))
                delivery = ""
                if r.outcome == "sent":
                    delivery = "sent"
                elif fired_ms and r.state.value == "failed":
                    delivered = any(
                        fired_ms - _DELIVERY_WINDOW_BEFORE_MS
                        <= h.at_ms
                        <= fired_ms + _DELIVERY_WINDOW_AFTER_MS
                        for h in sends.get((r.platform, r.conversation_id), ())
                    )
                    # `sent` rows are all `own_sends` returns; a send in the
                    # window is evidence of a delivery the row could not
                    # record. It says `unknown`, never `sent` — the row that
                    # would prove it is the one that was lost.
                    delivery = "unknown" if delivered else "none"
                entries.append(act_entry(
                    r, status=r.outcome or r.state.value, delivery=delivery,
                ))

    if wakes:
        sources.append(("wakes", "ok"))
        for w in wakes:
            entries.append(SharedEntry(
                kind="wake", id=str(w.wake_ref), scope="unit", status="pending",
                subject=" ".join(w.subject_rows)[:140],
                refs=tuple(w.subject_rows), at_ms=w.at_ms or 0,
                provenance="nora",
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
    ai_peer: str,
    rooms_of_unit: Callable[[str], Sequence[tuple[str, str]]],
) -> Callable[..., Awaitable[SharedView]]:
    """The collaborator each assembly holds: `read(unit=…, permit=…,
    wakes=…, now_ms=…)` bound to the canonical stores and the unit→rooms
    lookup, so no assembler threads five stores through its own constructor
    for one block."""

    async def read(
        *, unit: str, now_ms: int,
        permit: Callable[[Any], bool] | None = None,
        wakes: Sequence[Any] | None = None,
        unresolved: Sequence[str] = (),
    ) -> SharedView:
        return await read_shared(
            unit=unit, rooms=rooms_of_unit(unit), ai_peer=ai_peer,
            now_ms=now_ms, notes=notes, ledger=ledger, reminders=reminders,
            record_query=record_query, wakes=wakes, unresolved=unresolved,
            permit=permit,
        )

    return read
