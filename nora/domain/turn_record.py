"""The tool items of one retained exchange, with read-time disclosure facts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from nora.domain.lineage import LineageEntry
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
    for message in messages:
        for block in message.get("content") or ():
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
