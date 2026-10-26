"""Bounded person-source snapshots, not another evidence store (45 §4a)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nora.domain.culture import Episode, RoomMessage
from nora.domain.profile import Said
from nora.domain.social_stream import PersonSocialItem, RecordedCounts


@dataclass(frozen=True)
class PersonSourceRevision:
    """One owning-store snapshot. Only a rendered, settled snapshot is acked.

    Register metadata is context only. Its prose is deliberately absent: these
    stores have no typed non-location prose projection. Human Record roots
    retain their own authorship, time and citations instead.
    """

    domain: Literal["message", "social", "register", "rollup"]
    source: str
    revision: str
    state: str
    recorded_at: str
    roots: tuple[RoomMessage, ...] = ()
    social: PersonSocialItem | None = None
    counts: RecordedCounts | None = None
    effective_from: int | None = None
    effective_until: int | None = None
    properties: tuple[tuple[str, str], ...] = ()
    citable: str | None = None
    as_of: int | None = None
    links: tuple[str, ...] = ()
    cache_ready: bool = False
    media_state: str = "not_applicable"
    ready_handles: tuple[str, ...] = ()
    subject: str | None = None
    episode: Episode | None = None
    evidence: tuple[tuple[str, Said], ...] = ()
