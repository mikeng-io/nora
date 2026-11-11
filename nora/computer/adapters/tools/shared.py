from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from nora.domain.computer import (
    SandboxStatus,
)

_COMPUTER_UNRECORDED: Mapping[str, object] = MappingProxyType(
    {
        "status": "unavailable",
        "reason": "the call could not be recorded, so it was not made",
        "note": "I couldn't use my computer just now, and nothing was done. Say "
        "so; don't describe a result you never got.",
    }
)

_EXPORT_TITLE_CHARS = 200

_SESSION_LIST_READ = 50

_SESSION_EVENTS_READ = 500

_COMPUTER_BUSY_LIVE: Mapping[str, object] = MappingProxyType(
    {
        "status": "unavailable",
        "reason": "busy",
        "note": "Your computer is busy with another request in this room, and "
        "nothing was done. Say so, or try again in a moment.",
    }
)

_COMPUTER_BUSY_BACKGROUND: Mapping[str, object] = MappingProxyType(
    {
        "status": "unavailable",
        "reason": "busy, deferred",
        "note": "The computer is busy with a live request in this room; nothing "
        "was done. Leave it for your next pass.",
    }
)

_COMPUTER_OFF: Mapping[str, object] = MappingProxyType(
    {
        "status": "unavailable",
        "reason": "the computer is off here",
        "note": "Your computer isn't switched on here. Say you can't do this now, "
        "and don't describe a result you never got.",
    }
)


def _computer_status(status: SandboxStatus, *, found: bool) -> str:
    """The port's status in the words the MODEL is told: `denied` is"""
    mapped = {
        SandboxStatus.OK: "ok",
        SandboxStatus.NOTHING_FOUND: "nothing_found" if found else "unavailable",
        SandboxStatus.DENIED: "refused",
    }
    return mapped.get(status, "unavailable")


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


@dataclass(frozen=True)
class _Inbound:
    data: bytes
    sha256: str
    media_type: str
    filename: str
    suffix: str | None = None


@dataclass(frozen=True)
class _Stopped:
    outcome: str
    row: dict[str, object]
    answer: dict[str, object]
