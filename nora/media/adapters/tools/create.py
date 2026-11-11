from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal, get_args, get_type_hints

from nora.agent.security import scan_identifiers
from nora.agent.toolkit.files import (
    _COVER_NOTE,
    _KEPT_NOTE,
    _MEDIA_READING_NOTE,
    _handle_hex,
    _is_external_reference,
)
from nora.agent.toolkit.results import HELD, _record, _record_media, _send_status
from nora.domain.media import (
    HANDLE_CHARS,
    MediaBlob,
    MediaObject,
)
from nora.domain.ports import (
    ImageReference,
    ImageSize,
    OutboundAttachment,
)
from nora.domain.video import VideoReference, VideoRequest
from nora.media.adapters.tools.shared import _VIDEO_REF_KINDS, _VIDEO_REF_WORDS
from nora.obs.logging import current_tag_id

log = logging.getLogger("nora.tools")


def _make__serve_media(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics

    def _serve_media(
        sha256: str,
        description: str,
        *,
        cached: bool,
        code: str | None = None,
        cover: bool = False,
        kept: bool = False,
    ) -> dict:
        """The `ok` path, with the untrusted-egress screen run on EVERY serve.

        `code` is the reusable short handle when the media is a BLOB this
        conversation holds (the model can hand it back to `media_fetch`).
        URL-fetched media is described on the fly and NEVER stored, so its
        caller passes `code=None` — a hex code that cannot resolve on a
        later call would be a lie to the model.

        The description is text a vision model wrote about bytes a group member
        sent — the trust class of a fetched web page. It runs through
        `scan_identifiers` here, on every serve INCLUDING a cache hit, so the
        screen can never be bypassed by an earlier describe having populated
        the cache.

        **Honest about what the screen does NOT catch.** `scan_identifiers`
        matches an ACI/UUID, a key, and a `+`-prefixed E.164 — it deliberately
        does NOT match a bare local number (`9000 0101`), because blocking
        free-standing digit runs would mute her in ordinary chat (R-18's standing
        blind spot). So a photo of a business card whose number MiMo transcribes
        as `9000 0101` PASSES this screen. That is a residual risk EQUAL to
        R-18's, not a solved case — stated here rather than papered over.

        `cover` says the handle is a shared item's still picture
        (`MediaBlob.role`), and the result then SAYS so in its `note`. What the
        model read is one frame; without that sentence a correct description of
        it reads as a description of the whole reel.
        """
        if scan_identifiers(description, content_checks=content_checks) is not None:
            # An identifier the model transcribed off the image — dropped with a
            # note, the shape `about` uses for a conclusion that carries one. The
            # status stays `ok` (she DID look), but the words never leave.
            _record_media(metrics, "ok", cached=cached, withheld=True)
            withheld = (
                "I looked, and it has what looks like a phone number or an "
                "ID in it, so I won't repeat what it said. Describe it only "
                "in general terms, without the contact details."
            )
            return {
                "status": "ok",
                "code": code,
                "withheld": True,
                "note": f"{withheld} {_COVER_NOTE}" if cover else withheld,
            }
        _record_media(metrics, "ok", cached=cached, chars=len(description))
        # ADR 0015 §3: the framing rides with the `description` it is about. A vision
        # model wrote it about somebody else's bytes, so it is how it looks, not a fact;
        # and it is notes for her eyes, not a reply to hand back to whoever sent it
        # (`_MEDIA_READING_NOTE`).
        served: dict[str, object] = {
            "status": "ok",
            "code": code,
            "description": description,
            "note": _MEDIA_READING_NOTE,
        }
        if cover:
            served["note"] = f"{served['note']} {_COVER_NOTE}"
        if kept:
            served["note"] = f"{served['note']} {_KEPT_NOTE}"
        return served

    return _serve_media


def _make__canonical_self(deps):
    media_store = deps.media_store

    async def _canonical_self(marker: str, tool: str) -> str | None:
        """DEC-193's narrow custody exemption: the sha of her canonical self
        row, or None. A FULL sha256 only (a short handle is a conversation
        handle and never reaches this), and the row's role must be exactly
        the string "self": no case-folding, no strip, no prefix, no set.
        The marker is written by the operator's ingest alone
        (`scripts/ingest_self_asset.py`); nothing a message reaches writes
        `provenance.role`."""
        if len(marker) != 64 or media_store is None:
            return None
        try:
            row = await media_store.get(sha256=marker)
        except Exception:
            log.exception("%s could not read the media index", tool)
            return None
        if row is None or row.sha256 != marker:
            return None
        role = (row.provenance or {}).get("role")
        if not isinstance(role, str) or role != "self":
            return None
        return marker

    return _canonical_self


def _make__held_image(deps):
    _held_media = _make__held_media(deps)

    async def _held_image(marker: str, tool: str) -> tuple[str, bytes, bool] | None:
        """The custody check, conversation FIRST: a code in THIS
        conversation's own media window resolves to `(sha256, bytes, True)`.
        Only when the window has no match at all may the one exemption apply
        (DEC-193): the FULL sha256 of the row whose `provenance.role` is
        exactly `"self"`, her canonical picture, `(sha256, bytes, False)`.
        Ambiguity in the window is never settled by it. Anything else is None
        (never a fetch). `_held_media` with the media type dropped."""
        held = await _held_media(marker, tool)
        return None if held is None else held[:3]

    return _held_image


def _make__held_media(deps):
    blob_store = deps.blob_store
    conversation_id = deps.conversation_id
    store = deps.store
    _canonical_self = _make__canonical_self(deps)
    _media_window = deps._media_window

    async def _held_media(marker: str, tool: str) -> tuple[str, bytes, bool, str] | None:
        """`_held_image`'s custody check, with the blob's media type as the
        transcript recorded it at ingest (grounded against the bytes there,
        `ground_media_type`). Her canonical picture is a picture: `image/*`."""
        try:
            rows = await store.recent(
                conversation_id=conversation_id,
                limit=_media_window,
            )
        except Exception:
            log.exception("%s could not read the transcript", tool)
            return None
        hex_handle = marker
        matches: dict[str, str] = {}
        for row in rows:
            if not row.media_json:
                continue
            try:
                blobs = json.loads(row.media_json)
            except (ValueError, TypeError):
                continue
            for blob in blobs if isinstance(blobs, list) else ():
                if not isinstance(blob, dict):
                    continue
                sha = str(blob.get("sha256", ""))
                if sha.startswith(hex_handle):
                    matches.setdefault(sha, str(blob.get("media_type", "")))
        if len(matches) > 1:
            # Ambiguous — "not a resolvable reference for this edit"
            # (never pick the first, and never fall through to the
            # canonical row below).
            return None
        from_room = bool(matches)
        if matches:
            sha256, media_type = next(iter(matches.items()))
        else:
            canonical = await _canonical_self(hex_handle, tool)
            if canonical is None:
                return None
            sha256, media_type = canonical, "image/*"
        if blob_store is None:
            return None
        try:
            data = await blob_store.get(key=sha256)
        except Exception:
            log.exception("%s could not open blob %s", tool, sha256[:HANDLE_CHARS])
            return None
        if data is None:
            return None
        return sha256, data, from_room, media_type

    return _held_media


def _make_generate_image(deps):
    async def generate_image(
        prompt: str,
        reference_images: list[str] | None = None,
        size: Literal["small", "medium", "large"] | None = None,
        aspect: Literal["1:1", "16:9", "9:16", "3:2", "2:3"] | None = None,
        quality: Literal["standard", "quality"] | None = None,
    ) -> dict:
        """Make an image from a prompt, optionally from photos in this
        conversation, and post it to this chat. Call this when somebody asks
        you to make, draw or edit a picture.

        MUST: load the image-generation skill before calling this tool. It
        holds the canonical reference and the appearance rules, which this
        tool does not repeat.

        `reference_images` takes the marker codes of photos (`a3f2c1` from
        `[image a3f2c1]`){reference_limit}. Each must already be in this
        conversation, except your own canonical picture, which works in any
        room. With references and no `aspect`, the first one sets the
        picture's shape. With neither `size` nor `aspect` the shape is the
        provider's choice; set one and the other defaults to medium or 1:1.

        MUST: the prompt goes to an outside service, so it never carries a
        name, phone number, address or other identifier.

        `status` is one of:
        - `ok`: made, and posted here unless the result carries a `url`.
        - `held_for_review`: made, and held by review or this room's cooldown.
        - `nothing_found`: the prompt was refused, or a reference is not a photo held here.
        - `unavailable`: it cannot be made or sent right now.
        Every result carries a `note` saying how to report it.

        Args:
            prompt: What the picture shows, written for the image model.
            reference_images: Optional marker codes of photos, or your canonical picture.
            size: Optional size tier.
            aspect: Optional aspect ratio.
            quality: Optional model tier; "quality" is the costlier one.
        """
        c = SimpleNamespace(
            prompt=prompt,
            reference_images=reference_images,
            size=size,
            aspect=aspect,
            quality=quality,
            blob_store=deps.blob_store,
            conversation_id=deps.conversation_id,
            deliverer=deps.deliverer,
            image_generator=deps.image_generator,
            image_refs_allowed=deps.image_refs_allowed,
            media_store=deps.media_store,
            room=deps.room,
            _held_image=_make__held_image(deps),
        )
        k_, _, v_ = await _generate_image__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _generate_image__p2(c)
        if k_:
            return v_
        if c.result.status == "ok":
            k_, _, v_ = await _generate_image__p3(c)
            if k_:
                return v_
            if c.result.raw:
                k_, _, v_ = await _generate_image__p4(c)
                if k_:
                    return v_
                k_, _, v_ = await _generate_image__p5(c)
                if k_:
                    return v_
            k_, _, v_ = await _generate_image__p6(c)
            if k_:
                return v_
        k_, _, v_ = await _generate_image__p7(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return generate_image


def _make_video_create(deps):
    async def video_create(
        prompt: str,
        reference: str | None = None,
        reference_2: str | None = None,
        reference_3: str | None = None,
        first_frame: str | None = None,
    ) -> dict:
        """Make a short video clip, posted here later. Call this when
        somebody asks for a video.

        MUST: load the video-generation skill before calling this tool. It holds the
        judgement about when you appear and which photo is whom.

        `reference`, `reference_2` and `reference_3` each take the marker code of one
        photo, clip or voice note (`a3f2c1` from `[image a3f2c1]`), up to 3: a photo
        keeps a subject or place, a clip its motion, a voice note its voice. Each must
        already be in this conversation, except your own canonical picture, which works in
        any room. `first_frame` takes one photo code instead: that photo becomes the clip's
        opening frame. Never pass it together with a reference.

        MUST: the prompt goes to an outside service, so it never carries a name, phone
        number, address or other identifier.

        `status` is one of:
        - `started`: the clip is rendering and will be posted here later.
        - `refused`: this room's video limit for today is used; nothing was started.
        - `nothing_found`: a code is not a photo, clip or voice note held here.
        - `unavailable`: nothing was started.
        Every result carries a `note` saying how to report it.

        Args:
            prompt: What the clip shows and how it moves, written for the video model.
                With `first_frame`, only what moves and sounds after that frame.
            reference: Optional marker code of a photo, clip or voice note, or your
                canonical picture.
            reference_2: Optional second marker code.
            reference_3: Optional third marker code.
            first_frame: Optional marker code of the photo the clip starts from.
        """
        c = SimpleNamespace(
            prompt=prompt,
            reference=reference,
            reference_2=reference_2,
            reference_3=reference_3,
            first_frame=first_frame,
            conversation_type=deps.conversation_type,
            metrics=deps.metrics,
            room=deps.room,
            video=deps.video,
            video_ref_kinds=deps.video_ref_kinds,
            _held_media=_make__held_media(deps),
            bound_conversation_id=deps.bound_conversation_id,
            video_on_tag=deps.video_on_tag,
        )
        k_, _, v_ = await _video_create__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _video_create__p2(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return video_create


async def _generate_image__p1(c):
    if c.image_generator is None:
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't make images right now. Say "
                    "so, but do not invent one and do not say there is no such thing."
                ),
            },
        )
    if isinstance(c.reference_images, str):
        c.reference_images = [c.reference_images]
    c.wanted_refs = [r for r in c.reference_images or () if r is not None]
    if c.wanted_refs and (not c.image_refs_allowed):
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't use reference photos right "
                    "now: that feature is switched off. I can still make an image from text alone."
                ),
            },
        )
    c.refs: tuple[ImageReference, ...] = ()
    if c.wanted_refs:
        c.markers: list[str] = []
        for _w1 in c.wanted_refs:
            c.code = _w1
            if _is_external_reference(c.code):
                return (
                    True,
                    None,
                    {
                        "status": "nothing_found",
                        "note": (
                            "That is a link, not a "
                            "photo I hold. I can only use photos already sent here, or my own "
                            "picture."
                        ),
                    },
                )
            c.marker = _handle_hex(c.code)
            if len(c.marker) < HANDLE_CHARS:
                return (
                    True,
                    None,
                    {
                        "status": "nothing_found",
                        "note": (
                            "I can't find a photo "
                            "with that code here. Say you can't use it rather than guessing."
                        ),
                    },
                )
            c.markers.append(c.marker)
        c.markers = list(dict.fromkeys(c.markers))
        c.max_refs = c.image_generator.max_reference_images
        if len(c.markers) > c.max_refs:
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        f"That is {len(c.markers)} "
                        f"photos, and I can use at most {c.max_refs} in one picture. Nothing was "
                        f"made. "
                        f"Ask which {c.max_refs} to use, or make it from fewer."
                    ),
                },
            )
        c.refs = tuple(ImageReference(marker_code=m) for m in c.markers)
    c.image_size = None
    if c.size is not None or c.aspect is not None:
        c.tier: Literal["small", "medium", "large"] = "medium"
        for _w2 in get_args(get_type_hints(ImageSize)["tier"]):
            c.option_t = _w2
            if c.size == c.option_t:
                c.tier = c.option_t
        c.ratio: Literal["1:1", "16:9", "9:16", "3:2", "2:3"] = "1:1"
        for _w3 in get_args(get_type_hints(ImageSize)["aspect_ratio"]):
            c.option_r = _w3
            if c.aspect == c.option_r:
                c.ratio = c.option_r
        c.image_size = ImageSize(tier=c.tier, aspect_ratio=c.ratio)
    c.quality_kind: Literal["standard", "quality"] | None = None
    if c.quality == "quality":
        c.quality_kind = "quality"
    c.room_photos: list[str] = []

    def _made_note(what: str) -> str:
        if c.room_photos:
            return f"{what} Say in your reply that it was made from photos sent here."
        return what

    c._made_note = _made_note

    async def resolve(marker: str) -> bytes | None:
        held = await c._held_image(marker, "generate_image")
        if held is None:
            return None
        sha256, data, from_room = held
        if from_room:
            c.room_photos.append(sha256)
        return data

    c.resolve = resolve
    return (False, None, None)


async def _generate_image__p2(c):
    try:
        c.result = await c.image_generator.generate(
            prompt=c.prompt,
            reference_images=c.refs if c.refs else None,
            size=c.image_size,
            quality=c.quality_kind,
            reference_resolver=c.resolve if c.refs else None,
        )
    except Exception:
        log.exception("generate_image raised")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I couldn't make that image just "
                    "now. Say so, and do not describe an image that wasn't made."
                ),
            },
        )
    return (False, None, None)


async def _generate_image__p3(c):
    log.info("image_gen: generated via %s", c.result.provider or "?")
    return (False, None, None)


async def _generate_image__p4(c):
    c.raw: bytes = c.result.raw
    c.sha256 = ""
    try:
        if c.blob_store is not None:
            c.sha256 = await c.blob_store.put(data=c.raw)
    except Exception:
        log.exception("generate_image: blob store put failed")
    try:
        if c.sha256 and c.media_store is not None:
            await c.media_store.save(
                obj=MediaObject(
                    sha256=c.sha256,
                    media_type=c.result.media_type or ("image/png"),
                    size=len(c.raw),
                    source="generated",
                    description=c.prompt,
                    title=c.prompt[:80] or "generated image",
                    provenance={
                        "prompt": c.prompt,
                        "title": c.prompt[:80] or "generated image",
                        "description": c.prompt,
                        "provider": c.result.provider or "",
                        "model": "",
                        "aspect_ratio": c.image_size.aspect_ratio if c.image_size else None,
                        "platform": c.room.platform,
                        "conversation_id": c.conversation_id,
                    },
                    created_at_ms=int(datetime.now(UTC).timestamp() * 1000),
                )
            )
    except Exception:
        log.exception("generate_image: media_store save failed")
    c.caption = ""
    c.media_type = c.result.media_type or "image/png"
    c.filename = f"nora-generated-{int(datetime.now(UTC).timestamp())}.png"
    c.media_blobs: tuple[MediaBlob, ...] = ()
    if c.sha256:
        c.media_blobs = (
            MediaBlob(
                sha256=c.sha256, media_type=c.media_type, size=len(c.raw), filename=c.filename
            ),
        )
    c.held_before = c.deliverer.held_count
    c.sent = await c.deliverer.deliver(
        c.caption,
        source="tool",
        attachments=(
            OutboundAttachment(content=c.raw, media_type=c.media_type, filename=c.filename),
        ),
        record_text=(f"[image {c.sha256[:HANDLE_CHARS]}]") if c.sha256 else "[generated image]",
        record_metadata={
            "media": {"provider": c.result.provider, "type": "image", "sha256": c.sha256}
        },
        media_blobs=c.media_blobs,
    )
    c.status = _send_status(c.deliverer, sent=c.sent, held_before=c.held_before)
    if c.status == HELD:
        return (
            True,
            None,
            {
                "status": "held_for_review",
                "note": (
                    "I made the image and my part "
                    "is DONE. A review mode or this room's cooldown held the send. Do not make it "
                    "again."
                ),
            },
        )
    if not c.sent:
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I made the image but couldn't send "
                    "it to this chat. Say so plainly, and do not pretend it arrived."
                ),
            },
        )
    return (False, None, None)


async def _generate_image__p5(c):
    return (
        True,
        None,
        {
            "status": "ok",
            "handle": c.sha256[:HANDLE_CHARS] if c.sha256 else "",
            ("sent"): True,
            "note": c._made_note(
                "The picture is already in this chat. The handle is "
                "your own record of it: never quote it, and paste no link, including one from an "
                "earlier message."
            ),
        },
    )
    return (False, None, None)


async def _generate_image__p6(c):
    c.artifact = c.result.artifact or ""
    if c.artifact.startswith("data:image/"):
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I made the image but couldn't host "
                    "it. Say so plainly rather than describing it."
                ),
            },
        )
    return (
        True,
        None,
        {
            "status": "ok",
            "url": c.artifact,
            "note": c._made_note(
                "The picture was not attached here; this link is the picture, so share it."
            ),
        },
    )
    return (False, None, None)


async def _generate_image__p7(c):
    if c.result.status == "nothing_found":
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "I couldn't make that image: the "
                    "provider turned the prompt away. Say it could not be made, without guessing "
                    "why, or "
                    "try changing the wording."
                ),
            },
        )
    c.reason = c.result.reason or "unavailable"
    c.note = {
        "quota": "I've used up today's image quota. Say so plainly.",
        "auth": (
            "My image generation login has expired. Miles needs to re-authorise before I can make "
            "more."
        ),
        "rate_limited": "I'm making too many right now; try again in a bit.",
        "down": "The image service is having trouble right now; try again in a bit.",
    }.get(c.reason, "I can't make images right now. Say so plainly.")
    return (True, None, {"status": "unavailable", "note": c.note})
    return (False, None, None)


async def _video_create__p1(c):
    c.tool = "video_create"
    if c.video is None:
        _record(c.metrics, c.tool, "unavailable", reason="not_wired")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": ("I can't make videos right now. Say so; nothing was started."),
            },
        )
    c.tagger = getattr(c.room, "tagger", None) if c.room is not None else None
    if not c.video_on_tag or c.tagger is None:
        _record(c.metrics, c.tool, "unavailable", reason="no_requester")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "A video is made for the person who "
                    "asked, in a turn they started. Nothing was started."
                ),
            },
        )
    c.wanted = [r for r in (c.reference, c.reference_2, c.reference_3) if r is not None]
    if (c.wanted or c.first_frame is not None) and (not c.video_ref_kinds):
        _record(c.metrics, c.tool, "unavailable", reason="references_off")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't use photos, clips or voice "
                    "notes for videos right now: that feature is switched off. Nothing was "
                    "started; I can "
                    "still make one from words alone."
                ),
            },
        )
    if c.first_frame is not None and c.wanted:
        _record(c.metrics, c.tool, "unavailable", reason="first_frame_with_reference")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "A first frame and reference photos "
                    "cannot go into one clip, so nothing was started. Call again with one of them: "
                    "`first_frame` to make that photo itself move, a reference to put its subject "
                    "in "
                    "a new scene."
                ),
            },
        )
    c.references: list[VideoReference] = []
    c.frame: VideoReference | None = None
    c.codes = [*c.wanted, *([c.first_frame] if c.first_frame is not None else [])]
    for _w1 in enumerate(c.codes):
        c.index, c.code = _w1
        if _is_external_reference(c.code):
            _record(c.metrics, c.tool, "nothing_found", reason="external_reference")
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    "note": (
                        "That is a link, not a photo "
                        "I hold. I can only use photos already sent here, or my own picture. "
                        "Nothing was "
                        "started."
                    ),
                },
            )
        c.marker = _handle_hex(c.code)
        c.held = await c._held_media(c.marker, c.tool) if len(c.marker) >= HANDLE_CHARS else None
        if c.held is None:
            _record(c.metrics, c.tool, "nothing_found", reason="reference_not_held")
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    "note": (
                        "I can't find a photo, clip "
                        "or voice note with that code here. Say you can't use it rather than "
                        "guessing. "
                        "Nothing was started."
                    ),
                },
            )
        c.kind = _VIDEO_REF_KINDS.get(c.held[3].partition("/")[0].strip().lower())
        if c.kind is None:
            _record(c.metrics, c.tool, "nothing_found", reason="reference_not_media")
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    "note": (
                        "That code is a file, not a "
                        "photo, clip or voice note, so it cannot go into a video. Nothing was "
                        "started."
                    ),
                },
            )
        if c.index >= len(c.wanted) and c.kind != "image":
            _record(c.metrics, c.tool, "unavailable", reason="first_frame_not_photo")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        "A first frame must be a photo, "
                        "so nothing was started. A clip or voice note goes in as a reference "
                        "instead."
                    ),
                },
            )
        if c.kind not in c.video_ref_kinds:
            _record(c.metrics, c.tool, "unavailable", reason=f"{c.kind}_references_off")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "note": (
                        f"I can't use "
                        f"{_VIDEO_REF_WORDS[c.kind]} for videos right now: that is switched off. "
                        f"Nothing "
                        f"was started; I can still make one without it."
                    ),
                },
            )
        c.held_ref = VideoReference(
            sha256=c.held[0],
            data=c.held[1],
            kind=c.kind,
            sound=c.kind == ("video") and "audio" in c.video_ref_kinds,
        )
        if c.index < len(c.wanted):
            c.references.append(c.held_ref)
        else:
            c.frame = c.held_ref
    c.out = await c.video.create(
        platform=c.room.platform if c.room is not None else "",
        conversation_id=c.bound_conversation_id,
        conversation_type=c.conversation_type,
        requester_person_id=c.tagger.person_id,
        tag_id=current_tag_id() or None,
        request=VideoRequest(
            prompt=str(c.prompt or ("")), references=tuple(c.references), first_frame=c.frame
        ),
    )
    if c.out.status == "at_cap":
        _record(c.metrics, c.tool, "refused", reason="daily_cap", used=c.out.used, cap=c.out.cap)
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    f"This room's video limit for today is "
                    f"used ({c.out.used} of {c.out.cap}), so nothing was started. Say so "
                    f"plainly; it "
                    f"resets tomorrow."
                ),
            },
        )
    return (False, None, None)


async def _video_create__p2(c):
    if c.out.status != "started" and c.out.reason in ("uncertain", "row_not_written"):
        _record(c.metrics, c.tool, "unavailable", reason=c.out.reason)
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "Something broke while starting the "
                    "video, and it may or may not be rendering; if it is, it will not be posted "
                    "here. Say "
                    "so plainly, and do not try again today."
                ),
            },
        )
    if c.out.status == "refused" and c.out.reason:
        _record(c.metrics, c.tool, "unavailable", reason="refused")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    f"I couldn't start the video ("
                    f"{c.out.reason}), and nothing is rendering. Say so plainly; don't say you "
                    f"are on it."
                ),
            },
        )
    if c.out.status != "started":
        _record(c.metrics, c.tool, "unavailable", reason=c.out.reason or c.out.status)
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I couldn't start the video, and "
                    "nothing is rendering. Say so; don't say you are on it."
                ),
            },
        )
    if c.out.repeated:
        _record(c.metrics, c.tool, "started", reason="repeated_in_turn")
        return (
            True,
            None,
            {
                "status": "started",
                "note": (
                    "This clip was already started earlier "
                    "in this turn, and nothing new was started. Tell the room once that you are "
                    "on it."
                ),
            },
        )
    _record(c.metrics, c.tool, "started", used=c.out.used, cap=c.out.cap)
    return (
        True,
        None,
        {
            "status": "started",
            "note": (
                "It is rendering. Tell them you are on it "
                "and that the clip will be posted here in a few minutes. Do not wait for it, and "
                "do not "
                "describe a clip you have not seen."
            ),
        },
    )
    return (False, None, None)
