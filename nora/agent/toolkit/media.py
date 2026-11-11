from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from nora.domain.media import (
    MEDIA_FETCH_CALLERS,
    ROOM_MEDIA_DAYS,
    resolve_media_type,
    sniff_media_type,
)
from nora.infra.social.readings import MAX_READ_VIDEO_SECONDS, TurnLook
from nora.infra.vision.preprocess import (
    download_media,
)

_MODALITIES = ("image", "audio", "video")

_CONCLUSION_MAX_CHARS = 65535


def _modality_of(media_type: str) -> str | None:
    """Which describer a blob belongs to, or `None` if nothing here reads it."""
    top, _, _ = media_type.partition("/")
    return top if top in _MODALITIES else None


async def _download_media(url: str) -> tuple[bytes, str]:
    """Fetch a remote picture/video under hard ceilings (DEC-56)."""
    data, declared = await download_media(url)
    if not data:
        return b"", ""
    media_type = resolve_media_type(declared=declared, filename=url)
    if not media_type:
        media_type = sniff_media_type(data)
    return data, media_type


_MEDIA_READ: ContextVar[tuple[str, bool]] = ContextVar(
    "nora_media_read",
    default=(MEDIA_FETCH_CALLERS[0], True),
)


def media_look(
    fetch: Callable[..., Awaitable[Any]],
    *,
    caller: str,
    persist: bool,
    max_video_seconds: float = MAX_READ_VIDEO_SECONDS,
    slots: asyncio.Semaphore | None = None,
) -> TurnLook:
    """A `TurnLook` whose every look is `fetch` — `media_fetch` itself, from
    the turn's own tool set — called with the url alone, labelled `caller`
    on `nora_media_fetch_total`, and writing the content caches only when
    `persist`. The ONE way an automatic reading reaches `media_fetch`:
    `instagram_read`'s items and the summons' post both come through here,
    so neither has a copy of the label-and-persist wrapper to drift.

    `caller` must be one of `MEDIA_FETCH_CALLERS` (a label outside the
    vocabulary would be a series nothing declares); `slots` defaults to a
    fresh `READ_CONCURRENCY` semaphore for a caller that owns the whole read."""
    if caller not in MEDIA_FETCH_CALLERS:
        raise ValueError(f"media_look caller {caller!r} is not in MEDIA_FETCH_CALLERS")

    async def look(*, url: str) -> Any:
        token = _MEDIA_READ.set((caller, persist))
        try:
            return await fetch(url=url)
        finally:
            _MEDIA_READ.reset(token)

    if slots is None:
        return TurnLook(fetch=look, max_video_seconds=float(max_video_seconds))
    return TurnLook(fetch=look, slots=slots, max_video_seconds=float(max_video_seconds))


def _stored_reading(obj) -> str:
    """The stored comprehension, as one string for `media_fetch` to serve."""
    parts = []
    if obj.title:
        parts.append(obj.title)
    if obj.description:
        parts.append(obj.description)
    if obj.content and obj.content != obj.description:
        parts.append(obj.content)
    # LAST, and unconditional: what a read did NOT see is the one line that
    # must survive every composition of it (`MediaObject.caveat`).
    if obj.caveat:
        parts.append(obj.caveat)
    return "\n".join(parts)


_MEDIA_SEARCH_MAX_DAYS = ROOM_MEDIA_DAYS

_ROOM_EXCERPT_CHARS = 240

_MS_PER_DAY = 86_400_000
