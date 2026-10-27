"""The tool items of one retained exchange, with read-time disclosure facts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from nora.domain.lineage import (
    KINDS,
    PERCEPTION,
    PRESENCE,
    ROW,
    Lineage,
    LineageEntry,
    message,
    room_key,
)
from nora.domain.note_body import NoteBodyError, citations_in
from nora.domain.tag_trace import TagTrace


@dataclass
class TurnCapture:
    trace: TagTrace | None = None


_CAPTURE: ContextVar[TurnCapture | None] = ContextVar("turn_exchange_capture", default=None)


@contextmanager
def capture_turn() -> Iterator[TurnCapture]:
    capture = TurnCapture()
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


def retain_exchange(trace: TagTrace) -> None:
    capture = _CAPTURE.get()
    if capture is not None:
        capture.trace = trace


@dataclass(frozen=True)
class DisclosureRequirement:
    origin: str | None
    entries: tuple[LineageEntry, ...]
    exclude_persons: tuple[str, ...] = ()
    location: bool = False
    location_subjects: tuple[str, ...] = ()
    resolved: bool = True

    @property
    def known(self) -> bool:
        def known_entry(e: LineageEntry) -> bool:
            return (
                e.kind in KINDS
                and (e.kind != "message" or bool(e.origin))
                and (e.kind not in {"presence", "face", "social", "play"} or bool(e.subject))
                and (e.kind != PERCEPTION or bool(e.reads))
                and all(known_entry(r) for r in e.reads)
            )
        return bool(self.resolved and self.entries and all(known_entry(e) for e in self.entries)
                    and (not self.location or self.location_subjects))

    def entry(self) -> LineageEntry:
        """Keep the full requirement in the existing recursive lineage path."""
        return LineageEntry(
            PERCEPTION, origin=self.origin, exclude_persons=self.exclude_persons,
            reads=(*self.entries, *(LineageEntry(PRESENCE, p, self.origin)
                                   for p in self.location_subjects if self.location)),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "origin": self.origin, "entries": [e.to_json() for e in self.entries],
            "exclude_persons": list(self.exclude_persons), "location": self.location,
            "location_subjects": list(self.location_subjects), "resolved": self.resolved,
        }

    @classmethod
    def from_json(cls, raw: Any) -> DisclosureRequirement:
        required = {"origin", "entries", "exclude_persons", "location",
                    "location_subjects", "resolved"}
        if not isinstance(raw, dict) or not required <= raw.keys():
            return cls(None, (), resolved=False)
        if (
            not isinstance(raw["entries"], list)
            or not all(isinstance(raw[k], list) and all(isinstance(p, str) for p in raw[k])
                       for k in ("exclude_persons", "location_subjects"))
            or not isinstance(raw["location"], bool)
            or not isinstance(raw["resolved"], bool)
            or (raw["origin"] is not None and not isinstance(raw["origin"], str))
        ):
            return cls(None, (), resolved=False)
        return cls(
            raw["origin"], tuple(LineageEntry.from_json(e) for e in raw["entries"]),
            tuple(raw["exclude_persons"]), raw["location"],
            tuple(raw["location_subjects"]), raw["resolved"],
        )


@dataclass(frozen=True)
class TurnItem:
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    requirements: tuple[DisclosureRequirement, ...]


def turn_items(
    messages: tuple[dict[str, Any], ...], requirements: dict[str, Any],
) -> tuple[TurnItem, ...]:
    """A view of tool calls and results only; narration and reasoning never enter it."""
    calls, results = [], {}
    for exchange_message in messages:
        for block in exchange_message.get("content") or ():
            if "toolUse" in block:
                calls.append(block["toolUse"])
            if "toolResult" in block:
                result = block["toolResult"]
                results[result.get("toolUseId")] = result
    return tuple(TurnItem(
        call_id=c["toolUseId"], name=c["name"], arguments=c.get("input", {}),
        result=results.get(c["toolUseId"]),
        requirements=tuple(DisclosureRequirement.from_json(r)
                           for r in requirements.get(c["toolUseId"], ())),
    ) for c in calls)


def turn_handles_in(body: str) -> frozenset[str]:
    return frozenset(c[5:-1] for c in citations_in(body) if c.startswith("turn("))


def cited_turn_lineage(trace: TagTrace | None, *, unit: str) -> Lineage:
    """A citation reaches every item, never just the subset one room could open."""
    if trace is None or not unit or trace.unit != unit:
        raise NoteBodyError("unknown_turn", "the cited turn cannot be resolved in this unit")
    origins = ((room_key(trace.platform, trace.conversation_id),)
               if trace.platform else trace.bound_rooms)
    if not origins:
        raise NoteBodyError("unknown_turn", "the cited turn has no resolved room")
    entries = [message(origin) for origin in origins]
    for item in turn_items(trace.messages, trace.requirements):
        if not item.requirements or not all(r.known for r in item.requirements):
            raise NoteBodyError("unknown_turn", "the cited turn's requirements are unresolved")
        entries.extend(r.entry() for r in item.requirements)
    return Lineage.of({ROW: entries})
