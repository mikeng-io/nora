from __future__ import annotations

import logging
from datetime import UTC, datetime

from nora.agent.lineage import entry_permits
from nora.agent.rollup_read import parse_interval
from nora.agent.room import find_by_name
from nora.agent.social_read import SOCIAL_READ_MAX, location_shown, render_items
from nora.agent.toolkit.clock import _HKT
from nora.agent.toolkit.results import _memory_result, _record
from nora.agent.toolkit.rooms import _ambiguous_person
from nora.domain.lineage import (
    SOCIAL,
    LineageEntry,
)
from nora.social.adapters.tools.shared import (
    _SOCIAL_COLLAB,
    _SOCIAL_ITEM_CUT,
    _SOCIAL_MORE,
    _SOCIAL_NO_PERSON,
    _SOCIAL_NONE,
    _SOCIAL_OFF,
    _SOCIAL_OK,
    _SOCIAL_TIME,
    _SOCIAL_WITHHELD,
)

log = logging.getLogger("nora.tools")


def _make_social_read(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics
    persons = deps.persons
    social_items = deps.social_items
    _lineage_target = deps._lineage_target
    by_id = deps.by_id
    now_fn = deps.now_fn

    async def social_read(person: str, interval: str) -> dict:
        """Read one person's stories, posts and reels from an interval, with what
        each showed. Call this when what someone posted bears on what you judge.

        SHOULD: name the person as they are named here. Items come newest first,
        each saying what it is and when it was posted. Statuses: ok,
        nothing_found, ambiguous, unavailable.

        Args:
            person: The person's name as used in this conversation.
            interval: ISO 8601, a date or start/end; an end date includes that
                whole day.
        """
        if social_items is None:
            _record(metrics, "social_read", "unavailable", reason="unwired")
            return {"status": "unavailable", "note": _SOCIAL_OFF}
        try:
            start_ms, end_ms = parse_interval(interval)
        except (ValueError, OverflowError):
            _record(metrics, "social_read", "unavailable", reason="time")
            return {"status": "unavailable", "note": _SOCIAL_TIME}
        bound = await _lineage_target("social_read")
        if bound is None:
            return {"status": "unavailable", "note": _SOCIAL_OFF}
        target = bound[0]
        # A social item is readable only in a room that lists its author (the
        # `social` lineage predicate), so the name resolves among exactly
        # those people, by the one matching rule (`find_by_name`: name, first
        # and last names, aliases) or by person id: anyone else answers like a
        # name nobody uses, and is never read.
        listed = [
            ref
            for pid, ref in persons.by_person.items()
            if entry_permits(LineageEntry(SOCIAL, subject=pid), target, origin=None)
        ]
        matches = find_by_name(listed, str(person)) or tuple(
            ref for ref in listed if by_id and ref.person_id == str(person).strip()
        )
        if not matches:
            _record(metrics, "social_read", "nothing_found", reason="person")
            return {"status": "nothing_found", "note": _SOCIAL_NO_PERSON}
        if len(matches) > 1:
            _record(metrics, "social_read", "ambiguous")
            return _ambiguous_person(matches, listed, by_id=by_id)
        who = matches[0]
        # Every id their items may be recorded under: an item recorded before
        # a rename carries the retired id.
        current = persons.subject_id_for(who.person_id)
        ids = tuple(dict.fromkeys((who.person_id, current, *persons.former_ids_for(current))))
        try:
            # One more than is shown, so a full page says there are more.
            got = await social_items.items_of(
                persons=ids,
                start=datetime.fromtimestamp(start_ms / 1000, tz=UTC),
                end=datetime.fromtimestamp(end_ms / 1000, tz=UTC),
                limit=SOCIAL_READ_MAX + 1,
            )
        except Exception:
            log.exception("social_read failed")
            _record(metrics, "social_read", "unavailable", reason="store")
            return {"status": "unavailable", "note": _SOCIAL_OFF}
        more = len(got) > SOCIAL_READ_MAX
        shown, withheld, cut = render_items(
            got[:SOCIAL_READ_MAX],
            by=who.name,
            place=location_shown(who.person_id, target),
            now=now_fn().astimezone(_HKT),
            content_checks=content_checks,
        )
        if not shown:
            _record(metrics, "social_read", "nothing_found", withheld=withheld)
            return {
                "status": "nothing_found",
                "note": _SOCIAL_WITHHELD if withheld else _SOCIAL_NONE,
            }
        notes = [_SOCIAL_OK]
        if any(", co-authored" in i["kind"] for i in shown):
            notes.append(_SOCIAL_COLLAB)
        if more or cut:
            notes.append(_SOCIAL_MORE)
        if any("more_seen" in i for i in shown):
            notes.append(_SOCIAL_ITEM_CUT)
        if withheld:
            notes.append(_SOCIAL_WITHHELD)
        _record(
            metrics,
            "social_read",
            "ok",
            items=len(shown),
            withheld=withheld,
            more=more or bool(cut),
        )
        result: dict = {"status": "ok", "items": shown, "note": " ".join(notes)}
        if more or cut:
            result["truncated"] = True
        return _memory_result(result)

    return social_read
