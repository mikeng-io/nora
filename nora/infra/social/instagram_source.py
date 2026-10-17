"""`InstagramSocialSource` — Instagram's `SocialSource`, on the
instagram-bridge's read routes.

Four reads, every one a GET (nothing here can mark a story seen):

* `probe` → `GET /accounts/{igid}`: the top-level `latest_reel_media` and the
  `social` half's `media_count` and `total_clips_count`. When the bridge's
  second upstream call failed (`social_available: false`) the counts are absent
  and `counts_available` is False; a `social_error` starting `429` sets
  `rate_limited`. Two upstream calls, so `probe_cost` is 2.
* `story` → `GET /accounts/{igid}/story`: `found` and the frames, each with its
  own `taken_at` and `expiring_at`.
* `list_items` → `GET /accounts/{igid}/items?after=&username=`: posts and
  reels, newest first, `collab` marking a post another account owns. Each item
  carries its own media (`items`, the shape `/posts/{shortcode}` answers) and
  alt text, so a listed item is recorded as listed.
* `read_item` → `GET /posts/{shortcode}`: the media of one post by its
  shortcode; the poller does not use it for listed items.

`healthy` → `GET /health`: True only for a 200 whose `session` is
`authenticated`. The bridge answers it from its own state, with no upstream
call; a checkpointed session still answers 200, so the status alone says
nothing.

**Failures are typed, not raised** (`Failed`, by the bridge's error envelope,
`deploy/instagram-bridge/internal/transport/http/errors.go`): 409 `needs_human`
halts the login; 502 `upstream_rejected` and 429 back it off; 501 `stale_query`
(a rotated query document) halts that read only; any other 501 is unsupported;
the handler's own 404 is not found; 503, a body that is not the documented
shape, and every status nobody mapped are unavailable. The envelope's
`message` is never read.

**What is checked before it is believed.** The igid and the shortcode go into a
URL path, so each is shape-checked first and an odd one is never sent. A media
URL must be on a known Instagram CDN host (parsed, not substring-matched); a
frame or media row on any other host is dropped.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, TypeVar

import aiohttp

from ...domain.instagram_links import SHORTCODE_RE
from ...domain.social_stream import (
    STORY_LIFETIME_SECONDS,
    Account,
    Cap,
    Failed,
    FailureKind,
    ItemRef,
    MediaItem,
    Page,
    Probe,
    SocialItem,
    StoryFrame,
    StorySnapshot,
    published_location,
)
from ..trace_propagation import propagation_headers
from .instagram_client import _IGID_RE, _USERNAME_RE, _error_code, _is_cdn_url

log = logging.getLogger("nora.social.instagram_source")

_UNAVAILABLE = Failed(FailureKind.UNAVAILABLE)

#: The one `/health` session state in which the login can read
#: (`nora.platform.instagram.self_id.AUTHENTICATED`, which this layer may not
#: import). Matched positively: a state never heard of is not healthy.
_AUTHENTICATED = "authenticated"

_T = TypeVar("_T")


class InstagramSocialSource:
    """The bridge's account, story, items and post routes as a `SocialSource`.
    NEVER raises. Collaborators through `__init__` only; `base_url` reaches the
    bridge as a co-networked container, and every request carries W3C trace
    context."""

    network = "instagram"
    capabilities = frozenset({Cap.PROBE, Cap.STORY, Cap.LIST_ITEMS, Cap.READ_ITEM})
    probe_cost = 2

    def __init__(self, *, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def probe(self, *, account: Account) -> Probe | Failed:
        return await self._guarded("probe", self._probe(account))

    async def story(self, *, account: Account) -> StorySnapshot | Failed:
        return await self._guarded("story", self._story(account))

    async def list_items(self, *, account: Account, after: str | None) -> Page | Failed:
        return await self._guarded("items", self._list_items(account, after))

    async def read_item(self, *, ref: ItemRef) -> SocialItem | Failed:
        return await self._guarded("post", self._read_item(ref))

    async def healthy(self) -> bool:
        return await self._guarded("health", self._healthy()) is True

    async def _guarded(self, what: str, call: Awaitable[_T]) -> _T | Failed:
        """The never-raises backstop: a transport error or a contract violation
        degrades to `unavailable`."""
        try:
            return await call
        except Exception:
            log.exception("instagram social source %s read failed", what)
            return _UNAVAILABLE

    async def _healthy(self) -> bool:
        status, payload = await self._get("/health")
        return status == 200 and isinstance(payload, dict) \
            and payload.get("session") == _AUTHENTICATED

    async def _probe(self, account: Account) -> Probe | Failed:
        if not _IGID_RE.fullmatch(account.id):
            return _bad_target()
        status, payload = await self._get(f"/accounts/{account.id}")
        if status != 200 or not isinstance(payload, dict):
            return _failure(status, payload)
        marker = payload.get("latest_reel_media")
        if not _is_int(marker):
            # Always sent by the handler: a body without it is not the
            # documented shape, and no marker is not "no story".
            log.warning("instagram bridge profile body carried no latest_reel_media")
            return _UNAVAILABLE
        social = payload.get("social")
        media_count = clips = None
        if payload.get("social_available") is True and isinstance(social, dict):
            media_count = _count(social.get("media_count"))
            clips = _count(social.get("total_clips_count"))
        error = payload.get("social_error")
        return Probe(
            latest_reel_media=marker, media_count=media_count, total_clips_count=clips,
            counts_available=media_count is not None and clips is not None,
            rate_limited=isinstance(error, str) and error.startswith("429"),
        )

    async def _story(self, account: Account) -> StorySnapshot | Failed:
        if not _IGID_RE.fullmatch(account.id):
            return _bad_target()
        status, payload = await self._get(f"/accounts/{account.id}/story")
        if status != 200 or not isinstance(payload, dict):
            return _failure(status, payload)
        found = payload.get("found")
        if found is False:
            return StorySnapshot(found=False)
        if found is not True:
            log.warning("instagram bridge story body carried no boolean `found`")
            return _UNAVAILABLE
        raw = payload.get("items")
        frames = tuple(
            frame for row in (raw if isinstance(raw, list) else [])
            if (frame := _frame(row)) is not None
        )
        if not frames:
            # `found: true` with nothing usable (the bridge drops a frame with
            # no URL, and this drops one off the CDN) is still a completed
            # read: it advances the marker, so the same probe value never
            # sends the story read again.
            log.warning("instagram bridge found a story with no usable frames")
        return StorySnapshot(found=True, frames=frames)

    async def _list_items(self, account: Account, after: str | None) -> Page | Failed:
        if not _IGID_RE.fullmatch(account.id):
            return _bad_target()
        params: dict[str, str] = {}
        if after:
            params["after"] = after
        if account.username and _USERNAME_RE.fullmatch(account.username):
            params["username"] = account.username
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        status, payload = await self._get(f"/accounts/{account.id}/items{query}")
        if status != 200 or not isinstance(payload, dict):
            return _failure(status, payload)
        raw = payload.get("items")
        cursor = payload.get("next_cursor")
        if not isinstance(raw, list) or not (cursor is None or isinstance(cursor, str)):
            log.warning("instagram bridge items body is not the documented shape")
            return _UNAVAILABLE
        listed: list[SocialItem] = []
        for row in raw:
            item = self._listed(row, account.id)
            if item is None:
                # One unusable row means the page is not the documented shape;
                # skipping it would hide a hole in the listing.
                log.warning("instagram bridge items body carried an unusable item")
                return _UNAVAILABLE
            listed.append(item)
        return Page(items=tuple(listed), next_cursor=cursor or None)

    async def _read_item(self, ref: ItemRef) -> SocialItem | Failed:
        if not SHORTCODE_RE.fullmatch(ref.shortcode):
            return _bad_target()
        status, payload = await self._get(f"/posts/{ref.shortcode}")
        if status != 200 or not isinstance(payload, dict):
            return _failure(status, payload)
        media = _media(payload.get("items"))
        if not media:
            # Recorded without media rather than failed: the post exists, and
            # a failure here would keep the list read incomplete and re-read
            # the same post on every probe.
            log.warning("instagram bridge post carried no usable media")
        caption = payload.get("caption")
        taken = _when(payload.get("taken_at"))
        return SocialItem(
            network=self.network, item_id=ref.item_id, account=ref.account, kind=ref.kind,
            taken_at=taken or ref.taken_at, expires_at=None,
            caption=caption if isinstance(caption, str) and caption else None,
            alt_text=None, media=media, collab=ref.collab,
        )

    def _listed(self, row: Any, account: str) -> SocialItem | None:
        """One timeline item → the item it is recorded as, or None when it is
        not the documented shape. An item whose media is all off the CDN (or
        that carries none) is still an item, recorded without media."""
        if not isinstance(row, dict):
            return None
        code, pk, kind = row.get("code"), row.get("pk"), row.get("kind")
        taken = _when(row.get("taken_at"))
        collab = row.get("collab")
        caption = row.get("caption")
        if not (isinstance(code, str) and SHORTCODE_RE.fullmatch(code)) or not _text(pk) \
                or kind not in ("post", "reel") or taken is None \
                or not isinstance(collab, bool) or not isinstance(caption, str) \
                or not isinstance(row.get("items"), list):
            return None
        media = _media(row["items"])
        if row["items"] and not media:
            log.warning("instagram bridge listed an item with no usable media")
        return SocialItem(
            network=self.network, item_id=str(pk), account=account, kind=kind,
            taken_at=taken, expires_at=None, caption=caption or None,
            alt_text=_text(row.get("alt_text")), media=media, collab=collab,
            **_place(row),
        )

    async def _get(self, path: str) -> tuple[int, Any]:
        """One GET on the bridge → `(status, parsed JSON or None)`. The body is
        parsed whatever the status: the error envelope's `code` tells the
        handler's answer from the mux's. A non-JSON body is `None`."""
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(f"{self._base_url}{path}", headers=propagation_headers()) as response,
        ):
            status = response.status
            text = await response.text()
        if status != 200:
            # The route, not the igid: a person's identifier stays out of
            # this log line.
            log.warning("instagram bridge answered %s", status)
        try:
            return status, json.loads(text)
        except ValueError:
            return status, None


def _bad_target() -> Failed:
    """An id that is not the shape its URL path takes: a wiring gap, never
    sent."""
    log.warning("instagram social source given a target that is not an id")
    return Failed(FailureKind.UNAVAILABLE, "bad_target")


def _failure(status: int, payload: Any) -> Failed:
    """A non-200 answer (or a 200 that is not an object) → the typed failure."""
    code = _error_code(payload) if isinstance(payload, dict) else None
    if status == 409 and code == "needs_human":
        return Failed(FailureKind.HALT, code)
    if status == 429 or (status == 502 and code == "upstream_rejected"):
        return Failed(FailureKind.BACK_OFF, code or "rate_limited")
    if status == 501:
        if code == "stale_query":
            return Failed(FailureKind.STALE_QUERY, code)
        return Failed(FailureKind.UNSUPPORTED, code or "")
    if status == 404 and code == "not_found":
        # The handler's own 404; Go's mux answers an unmatched path in plain
        # text, and a wrong base URL is not a fact about a person.
        return Failed(FailureKind.NOT_FOUND, code)
    return Failed(FailureKind.UNAVAILABLE, code or str(status))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _count(value: Any) -> int | None:
    return value if _is_int(value) and value >= 0 else None


def _when(value: Any) -> datetime | None:
    """Unix seconds → an aware datetime, or None for anything else."""
    if not _is_int(value) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _place(row: dict[str, Any]) -> dict[str, Any]:
    """A listed item's tagged place: `location` (None: no tag) when the
    bridge says it read it (`location_known: true`), else unknown. A bridge
    that does not say is unknown too, never "no tag"."""
    if row.get("location_known") is not True:
        return {"location": None, "location_known": False}
    return {"location": published_location(row.get("location")), "location_known": True}


def _frame(row: Any) -> StoryFrame | None:
    """One story item → a frame, or None when it is not usable: no id, no time,
    a kind that is neither image nor video, or a media URL off the CDN. A frame
    without an expiry of its own takes the platform's 24 hours from its own
    `taken_at`, never the reel's: the bridge fills that with the newest
    frame's expiry, which would date an older frame too late."""
    if not isinstance(row, dict):
        return None
    item_id = _text(row.get("id"))
    taken = _when(row.get("taken_at"))
    kind = row.get("kind")
    url = row.get("url")
    if item_id is None or taken is None or kind not in ("image", "video") \
            or not _is_cdn_url(url):
        return None
    expires = _when(row.get("expiring_at")) \
        or datetime.fromtimestamp(int(taken.timestamp()) + STORY_LIFETIME_SECONDS, UTC)
    thumbnail = row.get("thumbnail_url")
    return StoryFrame(
        item_id=item_id, taken_at=taken, expires_at=expires, kind=kind, url=url,
        thumbnail_url=thumbnail if _is_cdn_url(thumbnail) else None,
        caption=_text(row.get("caption")), alt_text=_text(row.get("alt_text")),
        **_place(row),
    )


def _seconds(value: Any) -> float | None:
    """A positive number of seconds, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


def _media(raw: Any) -> tuple[MediaItem, ...]:
    """A bridge `items` list (a post's, or one listed item's) → its image and
    video rows whose URL is on a known CDN host, with a video's length when
    the bridge sent one; every other row is dropped."""
    return tuple(
        MediaItem(kind=row["kind"], url=row["url"],
                  duration_seconds=_seconds(row.get("duration_seconds")))
        for row in (raw if isinstance(raw, list) else [])
        if isinstance(row, dict) and row.get("kind") in ("image", "video")
        and _is_cdn_url(row.get("url"))
    )
