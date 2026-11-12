"""Shared session-verb plumbing: room resolution, time parsing and the
refusal shape every write verb returns. The Cognition session's write verbs
live in their owning areas (`nora.memory.adapters.tools.session`,
`nora.agenda.adapters.tools.session`) and reach these helpers from there and
from `nora.app.cognition_session` alike.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from nora.domain.cognition_pace import PaceAnswer
from nora.language.cognition_render import room_key

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DAY_MS = 24 * 3600 * 1000
_ISO_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

#: The live note store's compare-and-swap refused (`StaleNoteHeadError`):
#: `unavailable`, the tag path's own answer, never `refused`.
_STALE_NOTE: dict[str, object] = {
    "status": "unavailable", "reason": "stale_head",
    "detail": "This note changed under you, so read it again and redo your change "
              "against what it says now. Nothing has been written.",
}

_room_key = room_key


def _room_of(room: str, state: Any)-> tuple[str, str] | dict[str, object]:

    """Explicit rooms come from the canonical unit; the agenda owns the default."""
    if not state.allowed_rooms:
        return {"status": "unavailable", "reason": "unit_scope_unavailable",
                "detail": "the unit's room set could not be established"}
    if room:
        found: tuple[str, str] | None = state.allowed_rooms.get(room)
        if found is None:
            return {"status": "refused", "reason": "unknown_room",
                    "detail": f"{room!r} is not a room in this unit"}
        return found
    if len(state.rooms) == 1:
        key = next(iter(state.rooms))
        return _room_of(key, state)
    return {
        "status": "refused", "reason": "room_required",
        "detail": f"this agenda spans {len(state.rooms)} rooms; name the one this "
                  f"write is about as `room`, one of {sorted(state.allowed_rooms)}",
    }


def _write_target(
    room: str, item: int | None, state: Any,
) -> tuple[tuple[str, str], int | None] | dict[str, object]:
    """The room a write is about plus the agenda item it is for."""
    if item is None:
        resolved = _room_of(room, state)
        return resolved if isinstance(resolved, dict) else (resolved, None)
    found = state.items_by_index.get(item)
    if found is None:
        return {"status": "refused", "reason": "no_such_item",
                "detail": f"{item} is not an item of this agenda"}
    key = _room_key(found.room)
    if room and room != key:
        return {"status": "refused", "reason": "item_room_mismatch",
                "detail": f"item {item} is in {key}, not {room}"}
    resolved = _room_of(key, state)
    return resolved if isinstance(resolved, dict) else (resolved, item)


def _outside_unit(state: Any)-> dict[str, object]:

    """A reminder outside the unit is not this session's to move or cancel."""
    return {
        "status": "refused", "reason": "unknown_room",
        "detail": "that reminder's room is not in this unit",
    }


def _parse_when(text: str)-> datetime | None:

    """ISO 8601 with an offset, or None."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _when_ms(text: str)-> int | None:

    """Epoch ms of an ISO 8601 time WITH an offset, or None."""
    parsed = _parse_when(text)
    # Integer arithmetic: `timestamp() * 1000` can land a millisecond short.
    return None if parsed is None else (parsed - _EPOCH) // timedelta(milliseconds=1)


def _bad_time(field_name: str)-> dict[str, object]:

    return {
        "status": "refused", "reason": "bad_time",
        "detail": f"{field_name} must be ISO 8601 with an offset, such as "
                  "2026-10-03T19:40+08:00; a time with no offset is refused",
    }


def _day_number(text: str)-> int | None:

    """`YYYYMMDD` of a real ISO day, or None."""
    if not _ISO_DAY.match(text):
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return day.year * 10_000 + day.month * 100 + day.day


def _refused(reason: str, detail: str | None)-> dict[str, object]:

    return {"status": "refused", "reason": reason, "detail": detail}


def _answer(answer: PaceAnswer, **ok: object)-> dict[str, object]:

    """A recorded pace call as the tool's answer: the refusal with its reason."""
    if answer.refusal_reason is not None:
        return _refused(answer.refusal_reason, answer.refusal_detail)
    return {"status": "ok", **ok}


def now_utc(ms: int)-> datetime:

    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@dataclass(frozen=True)
class SessionDeps:
    """The collaborators a Cognition session's write verbs close over: the
    runner (its stores and ports), this call's unit and run state, and the
    shown/written/cited plumbing `_build_tools` builds per call. One bundle
    so every area's verb builders share one shape."""
    runner: Any
    unit: str
    state: Any
    established_by: str
    shown_for: Callable[..., Any]
    wrote: Callable[..., Any]
    act_receipt: Callable[..., Any]
    associations: Callable[..., Any]
    object_refs: Callable[..., Any]
    #: The session's resolved instruction hash: the `instruction_hash` a
    #: provenance-bearing write records (the entry store's, e.g. a topic
    #: account). None when the session carries no doctrine.
    doctrine_sha256: str | None = None
