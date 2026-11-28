from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Literal

from nora.agent.rollup_read import iso as rollup_iso
from nora.agent.rollup_read import parse_interval, render_row, visible_rows
from nora.agent.toolkit.results import _ROLLUP_TIME, _record
from nora.agent.toolkit.rooms import _ambiguous_person
from nora.agent.turn_recording import record_requirement
from nora.domain.models import (
    PersonRef,
)
from nora.domain.rollup_read import ROLLUP_READ_KINDS, RollupRead, RollupReadQuery
from nora.domain.self_presence import platform_name
from nora.domain.turn_record import DisclosureRequirement
from nora.memory.adapters.tools.shared import (
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
