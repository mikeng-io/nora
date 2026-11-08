"""Typed inputs shared by context occasions. No rendered strings or store access."""

from __future__ import annotations

from dataclasses import dataclass

from nora.domain.models import PersonRef, TranscriptRow
from nora.domain.self_presence import KnownRoom


@dataclass(frozen=True)
class MembersView:
    room: str
    people: tuple[PersonRef, ...] = ()
    unavailable: bool = False
    unnamed: int = 0


@dataclass(frozen=True)
class RoomLine:
    room: KnownRoom
    last_ms: int | None = None
    activity_unavailable: bool = False


@dataclass(frozen=True)
class TranscriptLine:
    row: TranscriptRow
    room: str
    item: int | None = None
    platform: str = ""
    conversation_id: str = ""
