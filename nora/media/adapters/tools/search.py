from __future__ import annotations

import logging
import mimetypes
from typing import Literal

from nora.agent.security import scan_identifiers
from nora.agent.toolkit.files import _handle_hex
from nora.agent.toolkit.media import (
    _MEDIA_SEARCH_MAX_DAYS,
    _MS_PER_DAY,
    _ROOM_EXCERPT_CHARS,
    _stored_reading,
)
from nora.agent.toolkit.results import HELD, _send_status
from nora.domain.media import (
    HANDLE_CHARS,
    MediaObject,
    Sighting,
    label_from_media_type,
    reading_matches,
    room_photos,
    room_sightings,
)
from nora.domain.ports import (
    MediaObjectStore,
    OutboundAttachment,
)

log = logging.getLogger("nora.tools")


def _make_media_search(deps):
    media_store = deps.media_store
    _in_library = deps._in_library
    _media_search_room = _make__media_search_room(deps)
    _now_ms = deps._now_ms

    async def media_search(
        query: str = "",
        scope: Literal["library", "room"] = "library",
        days: int = 0,
    ) -> dict:
        """Find stored media. Use this when somebody asks you to resend an
        image you made or fetched, or to find photos sent in this chat that
        are out of view.

        It reaches back days, and nothing is regenerated.

        Returns `status`: ok or unavailable, with a `note` on how to report it.

        Args:
            query: Words from the prompt, title or description. Empty returns
                the most recent media.
            scope: `library` is media you made or fetched. `room` is photos and
                clips people sent in this chat, never another chat's.
            days: How far back to look, 1 to 90. Empty is no limit for `library`
                and 90 for `room`.
        """
        if media_store is None:
            return {
                "status": "unavailable",
                "note": "I can't look up my stored media right now. Say "
                "so plainly, and do not invent an image.",
            }
        span = min(days, _MEDIA_SEARCH_MAX_DAYS) if days > 0 else 0
        if scope == "room":
            return await _media_search_room(
                media_store,
                query,
                span or _MEDIA_SEARCH_MAX_DAYS,
            )
        try:
            # Custody-scoped (DEC-138): Nora's OWN media only — generated or fetched.
            # Photos people sent are the other scope, and only ever this room's.
            found = await media_store.lookup_search(
                query=query,
                limit=40,
                sources=("generated", "fetched"),
                since_ms=_now_ms() - span * _MS_PER_DAY if span else 0,
            )
        except Exception:
            log.exception("media_search: store search failed")
            return {
                "status": "unavailable",
                "note": "I can't look up my media right now. Say so plainly.",
            }
        if not found.available:
            # An index that could not be read is not an empty library.
            return {
                "status": "unavailable",
                "note": "I can't look up my media right now. Say so plainly, "
                "and do not say there is nothing.",
            }
        results = [
            {
                "handle": obj.handle,
                "title": obj.title or obj.description[:80],
                "prompt": obj.prompt,
                "created_at_ms": obj.created_at_ms,
            }
            for obj in [o for o in found.objects.values() if _in_library(o)][:10]
        ]
        # ADR 0015 §3: the reporting rule rides with the answer, split on
        # whether anything matched.
        return {
            "status": "ok",
            "results": results,
            "note": (
                "Say plainly what each result is, and never claim an image the list does not hold."
                if results
                else "Nothing in the library matched. Say so, and never claim an "
                "image exists that the search did not return."
            ),
        }

    return media_search


def _make__media_search_room(deps):
    content_checks = deps.content_checks
    room = deps.room
    store = deps.store
    _now_ms = deps._now_ms
    _room_result = _make__room_result(deps)
    bound_conversation_id = deps.bound_conversation_id

    async def _media_search_room(
        index: MediaObjectStore,
        query: str,
        span: int,
    ) -> dict:
        """`media_search`'s `room` scope: pictures and clips PEOPLE SENT in"""
        unreadable = {
            "status": "unavailable",
            "note": "I can't look through the photos sent here right now. Say "
            "so plainly, and do not say there are none.",
        }
        if not room.platform:
            # A background run with no room (a Dreamer unit keyed by name): its
            # conversation slot is a key, not a chat, so there is nothing to
            # look through and an empty answer would be a false «none».
            return {
                "status": "unavailable",
                "note": "There is no chat in this run to look through. Say so "
                "plainly, and do not say there are no photos.",
            }
        try:
            reach = await room_sightings(
                store,
                conversation_id=bound_conversation_id,
                now_ms=_now_ms(),
                days=span,
            )
            photos = room_photos(reach.sightings)
            look = await index.lookup(
                sha256s=tuple(p.sha256 for p in photos),
            )
        except Exception:
            log.exception("media_search: room media read failed")
            return unreadable
        if not look.available:
            return unreadable
        results: list[dict[str, object]] = []
        unread = 0
        asked = bool(query.strip())
        for photo in photos:
            obj = look.objects.get(photo.sha256)
            read = obj is not None and obj.comprehended
            # The SAME screen `media_fetch` serves through, over the whole reading
            # (title, description, content, caveat) at once. A screened reading is
            # never matched on either: matching it would let a model confirm
            hidden = (
                obj is not None
                and read
                and (
                    scan_identifiers(_stored_reading(obj), content_checks=content_checks)
                    is not None
                )
            )
            if not asked:
                results.append(_room_result(photo, obj, hidden=hidden))
            elif read and not hidden and obj is not None and reading_matches(obj, query):
                results.append(_room_result(photo, obj, hidden=False))
            elif not read:
                # A photo nothing has read cannot match words; count it so
                # «no match» is not heard as «no such photo».
                unread += 1
        shown = results[:10]
        note = (
            "Photos people sent in this chat, newest first. Each description is "
            "a vision model's notes, how it looks and not a fact. Say what you "
            "found and never claim a photo this list does not hold. Do not name "
            "who sent one."
            if shown
            else "No photo sent in this chat matched. Say so, and do not say a photo "
            "exists that the search did not return."
        )
        if unread:
            note += (
                f" {unread} photo(s) in this period have not been read yet, so "
                "a word search could not match them. Say so if it matters."
            )
        out: dict[str, object] = {
            "status": "ok",
            "scope": "room",
            "days": span,
            "results": shown,
            "note": note,
        }
        if reach.truncated:
            # The row cap cut the read short: the newest items were searched
            # and older ones were not, so an empty or short list is not «none».
            out["truncated"] = True
            out["note"] = (
                f"{note} This chat has more sent media than one search reads, so "
                "only the newest was searched and older media was NOT. Do not "
                "say a photo does not exist; say the search did not reach that "
                "far back."
            )
        return out

    return _media_search_room


def _make__room_result(deps):
    import nora.location as _w_location

    _been_iso = _w_location.wire.been_iso_impl(deps)

    def _room_result(
        photo: Sighting,
        obj: MediaObject | None,
        *,
        hidden: bool,
    ) -> dict[str, object]:
        """One room photo for the model: its marker code, kind, when it was"""
        entry: dict[str, object] = {
            "handle": photo.handle,
            "kind": label_from_media_type(photo.media_type),
            "sent": _been_iso(photo.sent_ms),
        }
        if obj is None or not obj.comprehended:
            entry["read"] = False
        elif hidden:
            entry["withheld"] = True
        else:
            entry["title"] = obj.title
            # A video's reading lives in `content` (timecoded) with an empty
            # `description`, so the excerpt falls back to it: without it the
            # model would see a clip that matched a word and nothing saying why.
            text = obj.description or obj.content[:_ROOM_EXCERPT_CHARS]
            if text:
                entry["description"] = text
        return entry

    return _room_result


def _make_media_send(deps):
    blob_store = deps.blob_store
    deliverer = deps.deliverer
    media_store = deps.media_store
    _in_library = deps._in_library

    async def media_send(handle: str) -> dict:
        """Send a previously stored media object (from `media_search`) to this
        chat. Call this when the room wants the media itself, not just its
        description.

        Returns `status`: ok, held_for_review, nothing_found, ambiguous or
        unavailable, with a `note` on how to report it.

        Args:
            handle: the handle of your own stored media from `media_search`
                (library scope). A photo somebody sent cannot be sent.
        """
        if media_store is None or blob_store is None:
            return {
                "status": "unavailable",
                "note": "I can't send stored media right now. Say so plainly.",
            }
        hex_handle = _handle_hex(handle)
        if len(hex_handle) < HANDLE_CHARS:
            return {
                "status": "nothing_found",
                "note": "That doesn't look like a media handle I have. Say "
                "you can't find it rather than guessing.",
            }
        try:
            rows = await media_store.by_prefix(handle=hex_handle)
            ours = [o for o in rows if _in_library(o)]
            meta = next(iter(ours), None)
            if len(ours) > 1:
                # Collision — NEVER pick the first silently (the case `media_fetch`'s
                # `ambiguous` already guards against).
                return {
                    "status": "ambiguous",
                    "code": hex_handle,
                    "could_be": sorted(o.sha256[:HANDLE_CHARS] for o in ours),
                    "note": "More than one media object has that code. Ask which one they mean.",
                }
        except Exception:
            log.exception("media_send: store by_prefix failed")
            return {
                "status": "unavailable",
                "note": "I can't look up my media right now. Say so plainly.",
            }
        if meta is None:
            return {
                "status": "nothing_found",
                "note": "I don't have an image with that handle. Say you "
                "can't find it rather than guessing.",
            }
        try:
            raw = await blob_store.get(key=meta.sha256)
        except Exception:
            log.exception("media_send: blob get failed")
            raw = None
        if raw is None:
            return {
                "status": "unavailable",
                "note": "I found the record but not the image bytes. Say so plainly.",
            }
        media_type = meta.media_type or "image/png"
        # The extension follows the bytes: a stored clip sent as `.png` is shown by Discord as a
        # broken picture rather than a video.
        filename = f"nora-media-{meta.handle}{mimetypes.guess_extension(media_type) or '.bin'}"
        # The transcript label follows the modality of the bytes (DEC-138: media is
        # ONE index — a fetched video must record `[video ...]`, not `[image ...]`).
        # Derived the same way `_BY_TOP_LEVEL` derives `label_for`, so a future media
        top, _, _ = media_type.partition("/")
        modality = top if top in ("image", "video", "audio") else "file"
        held_before = deliverer.held_count
        sent = await deliverer.deliver(
            "",
            source="tool",
            attachments=(
                OutboundAttachment(
                    content=raw,
                    media_type=media_type,
                    filename=filename,
                ),
            ),
            record_text=f"[{modality} {meta.handle}] (resent)",
            record_metadata={"media": {"type": modality, "sha256": meta.sha256, "resent": True}},
        )
        status = _send_status(deliverer, sent=sent, held_before=held_before)
        if status == HELD:
            return {
                "status": "held_for_review",
                "note": "I found it and my part is DONE. A review mode or "
                "this room's cooldown held the send. Do not resend.",
            }
        if not sent:
            return {
                "status": "unavailable",
                "note": "I found the image but couldn't send it to this chat. Say so plainly.",
            }
        return {"status": "ok", "handle": meta.handle}

    return media_send
