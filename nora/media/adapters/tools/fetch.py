from __future__ import annotations

import hashlib
import logging
import urllib.parse
from types import SimpleNamespace

from nora.agent.security import looks_like_coordinate as _looks_like_coordinate
from nora.agent.security import scan_identifiers
from nora.agent.toolkit.files import (
    _FILE_LOOK_NOTES,
    _KEEPABLE_IMAGE_TYPES,
    _TRUNCATED_CODE_NOTE,
    _handle_hex,
    _shared_page_link,
)
from nora.agent.toolkit.media import _MEDIA_READ, _download_media, _modality_of, _stored_reading
from nora.agent.toolkit.results import _QUERY_FRAME, _record_media
from nora.domain.external_files import (
    ExternalFileRef,
    quoted_handle_code,
    resolve_file_code,
)
from nora.domain.external_files import recognise as _recognise_file_link
from nora.domain.media import (
    HANDLE_CHARS,
    MEDIA_FETCH_CALLERS,
    Sighting,
    VideoReadingKey,
    code_matches,
    resolve_room_media,
    room_sightings,
)
from nora.infra.vision.client import DESCRIBE_PROMPTS
from nora.infra.vision.comprehend import describe_coverage, read_video_windows
from nora.infra.vision.preprocess import (
    cut_video,
    preprocess_media,
    video_encode_signature,
)

log = logging.getLogger("nora.tools")


def _make_media_fetch(deps):
    async def media_fetch(code: str = "", url: str = "", question: str = "") -> dict:
        """Look at an image or video and say what it shows. Call this when
        answering means knowing what is IN a picture or a video.

        Pass exactly one: `code`, from an `[image ...]` or `[video ...]` marker
        or a shared picture's or video's link handle in this chat, or `url`, a
        link to one. A voice note gives back its words.

        For chat media, leave `question` empty for the stored reading, free and
        instant; a shared file is looked at afresh every call. Ask one only
        when that cannot answer it; each costs a look.

        Returns `status`: ok, nothing_found, ambiguous, refused, not_an_image
        or unavailable, with a `note` on how to report it.

        Args:
            code: The short code from a media marker, or from a shared
                picture's or video's link handle.
            url: An http(s) link to a picture or video.
            question: What you want to know about it, when the stored
                reading does not answer it. Leave empty otherwise.
        """
        from nora.media.adapters.tools.create import _make__serve_media

        c = SimpleNamespace(
            code=code,
            url=url,
            question=question,
            blob_store=deps.blob_store,
            content_checks=deps.content_checks,
            describe_max_bytes=deps.describe_max_bytes,
            describe_models=deps.describe_models,
            describe_providers=deps.describe_providers,
            external_files=deps.external_files,
            media_store=deps.media_store,
            metrics=deps.metrics,
            room=deps.room,
            store=deps.store,
            video_chunk_seconds=deps.video_chunk_seconds,
            video_max_chunks=deps.video_max_chunks,
            video_readings=deps.video_readings,
            vision_cache=deps.vision_cache,
            vision_source=deps.vision_source,
            _instagram_story_urls=deps._instagram_story_urls,
            _keep_fetched_image=deps._keep_fetched_image,
            _media_window=deps._media_window,
            _now_ms=deps._now_ms,
            _serve_media=_make__serve_media(deps),
            _shared_file_by_code=deps._shared_file_by_code,
            _shared_file_bytes=deps._shared_file_bytes,
            bound_conversation_id=deps.bound_conversation_id,
        )
        k_, _, v_ = await _media_fetch__p1(c)
        if k_:
            return v_
        if c.url:
            k_, _, v_ = await _media_fetch__p2(c)
            if k_:
                return v_
        elif c.shared is None:
            k_, _, v_ = await _media_fetch__p3(c)
            if k_:
                return v_
            k_, _, v_ = await _media_fetch__p4(c)
            if k_:
                return v_
        k_, _, v_ = await _media_fetch__p5(c)
        if k_:
            return v_
        k_, _, v_ = await _media_fetch__p6(c)
        if k_:
            return v_
        k_, _, v_ = await _media_fetch__p7(c)
        if k_:
            return v_
        k_, _, v_ = await _media_fetch__p8(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return media_fetch


async def _media_fetch__p1(c):
    c.question = c.question.strip()
    if c.question:
        c.kind = scan_identifiers(c.question, content_checks=c.content_checks)
        if c.kind is not None:
            _record_media(c.metrics, "nothing_found", reason="identifier")
            c.metrics.inc("nora_egress_blocks_total", kind=c.kind)
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    "note": (
                        "I won't put something that "
                        "looks like a phone number or ID into a question about a picture. Ask it "
                        "in plain "
                        "words and I'll look."
                    ),
                },
            )
    if c.blob_store is None or c.vision_source is None:
        _record_media(c.metrics, "unavailable", reason="not_configured")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't look at media right now. "
                    "Say so, but do not say there is no media and do not guess what it showed."
                ),
            },
        )
    if bool(c.code) == bool(c.url):
        _record_media(c.metrics, "nothing_found", reason="no_target")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "Give me either the short media "
                    "code from this conversation or the link to a picture or a video."
                ),
            },
        )
    c.shared: ExternalFileRef | None = None
    c.made_up = quoted_handle_code(c.code or c.url)
    if c.made_up is not None:
        c.named = await c._shared_file_by_code(c.made_up)
        if isinstance(c.named, dict):
            return (True, None, c.named)
        c.shared, c.code, c.url = (c.named, "", "")
    elif (
        _recognise_file_link(c.__dict__.__setitem__("pasted", c.url or c.code) or c.pasted)
        is not None
    ):
        c.shared = (
            c.external_files.recognise(url=c.pasted)
            if c.external_files is not None
            else _recognise_file_link(c.pasted)
        )
        if c.shared is None:
            _record_media(c.metrics, "unavailable", reason="files_unrecognised")
            return (True, None, {"status": "unavailable", "note": _FILE_LOOK_NOTES["unavailable"]})
        c.code = c.url = ""
    return (False, None, None)


async def _media_fetch__p2(c):
    c.decoded_url = urllib.parse.unquote(c.url)
    if scan_identifiers(c.decoded_url, content_checks=c.content_checks) is not None:
        _record_media(c.metrics, "nothing_found", reason="identifier")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "That link has what looks like a phone number or ID in it, so I won't open it."
                ),
            },
        )
    if c.content_checks and _looks_like_coordinate(c.decoded_url):
        _record_media(c.metrics, "nothing_found", reason="coordinate")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "That link has what looks like a raw coordinate in it, so I won't open it."
                ),
            },
        )
    if not c.url.startswith(("http://", "https://")):
        _record_media(c.metrics, "nothing_found", reason="not_a_url")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": (
                    "That doesn't look like a link I "
                    "can open. Ask for the picture's or video's URL."
                ),
            },
        )
    c.data, c.media_type = await _download_media(c.url)
    if not c.data:
        _record_media(c.metrics, "unavailable", reason="download")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I couldn't fetch that media just "
                    "now. Say so, and do not describe what it might have shown."
                ),
            },
        )
    c.sha256 = hashlib.sha256(c.data).hexdigest()
    c.serve_code: str | None = None
    c.caller, c.persist_look = _MEDIA_READ.get()
    if (
        c.media_type in _KEEPABLE_IMAGE_TYPES
        and c.caller == MEDIA_FETCH_CALLERS[0]
        and c.persist_look
        and (c.url not in c._instagram_story_urls)
    ):
        c.serve_code = await c._keep_fetched_image(c.url, c.data, c.media_type)
    c.is_cover = False
    return (False, None, None)


async def _media_fetch__p3(c):
    c.hex_handle = _handle_hex(c.code)
    if len(c.hex_handle) < HANDLE_CHARS:
        _record_media(c.metrics, "nothing_found", reason="no_handle")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "code": c.hex_handle,
                "note": (
                    "I don't see media with that code here. Say you can't find it rather than "
                    "guessing "
                    "what it was."
                ),
            },
        )
    try:
        c.reach = await room_sightings(
            c.store,
            conversation_id=c.bound_conversation_id,
            now_ms=c._now_ms(),
            window=c._media_window,
        )
        c.seen = c.reach.sightings
    except Exception:
        log.exception("media_fetch failed to read the transcript")
        _record_media(c.metrics, "unavailable", reason="store")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "code": c.hex_handle,
                "note": (
                    "I can't look at media right now. Say so, but do not say there is no media "
                    "and do not "
                    "guess what it showed."
                ),
            },
        )
    c.matches = code_matches(c.seen, c.hex_handle)
    if not c.matches and c.media_store is not None:
        try:
            c.kept = await resolve_room_media(
                c.media_store,
                code=c.hex_handle,
                platform=c.room.platform,
                conversation_id=c.bound_conversation_id,
            )
        except Exception:
            log.exception("media_fetch could not read kept pictures")
            c.kept = None
        if c.kept is not None and c.kept.available:
            c.matches = {
                sha: Sighting(
                    sha256=sha,
                    media_type=obj.media_type,
                    filename="",
                    sent_ms=obj.created_at_ms,
                    cover=False,
                )
                for sha, obj in c.kept.objects.items()
                if obj.source == ("fetched")
            }
    c.links = await resolve_file_code(
        c.store, code=c.hex_handle, conversation_id=c.bound_conversation_id, now_ms=c._now_ms()
    )
    if c.links.status == "unavailable" and (not c.matches):
        _record_media(c.metrics, "unavailable", reason="store")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "code": c.hex_handle,
                "note": (
                    "I can't look at media right now. Say so, but do not say there is no media "
                    "and do not "
                    "guess what it showed."
                ),
            },
        )
    if c.links.refs and (c.matches or len(c.links.refs) > 1):
        _record_media(c.metrics, "ambiguous", candidates=len(c.matches) + len(c.links.refs))
        return (
            True,
            None,
            {
                "status": "ambiguous",
                "code": c.hex_handle,
                "note": (
                    "More than one thing here has that code (a picture or video sent here and a "
                    "shared "
                    "file's link, or two links). Ask which one they mean, or for the link."
                ),
            },
        )
    return (False, None, None)


async def _media_fetch__p4(c):
    if c.links.refs:
        c.shared = c.links.refs[0]
    else:
        c.covers = {sha: sg.cover for sha, sg in c.matches.items()}
        c.cover_links = {
            sha: c.link
            for sha, sg in c.matches.items()
            if sg.cover
            and sg.cover_body
            and (c.__dict__.__setitem__(("link"), _shared_page_link(sg.cover_body)) or c.link)
        }
        if not c.matches and (c.reach.truncated or c.links.truncated):
            _record_media(c.metrics, "unavailable", reason="truncated")
            return (
                True,
                None,
                {"status": "unavailable", "code": c.hex_handle, "note": _TRUNCATED_CODE_NOTE},
            )
        if not c.matches:
            _record_media(c.metrics, "nothing_found", candidates=0)
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    "code": c.hex_handle,
                    "note": (
                        "I don't see media sent in this chat in the last 90 days with that code. "
                        "Say you "
                        "can't find it rather than guessing what it was."
                    ),
                },
            )
        if len(c.matches) > 1:
            _record_media(c.metrics, "ambiguous", candidates=len(c.matches))
            return (
                True,
                None,
                {
                    "status": "ambiguous",
                    "code": c.hex_handle,
                    "could_be": sorted(sha[:HANDLE_CHARS] for sha in c.matches),
                    ("note"): "More than one item here has that code. Ask which one they mean.",
                },
            )
        c.sha256 = next(iter(c.matches))
        c.media_type = c.matches[c.sha256].media_type
        c.serve_code = c.sha256[:HANDLE_CHARS]
        c.is_cover = c.covers.get(c.sha256, False)
        c.stale_link = c.cover_links.get(c.sha256, "") if c.is_cover else ""
        if c.stale_link:
            _record_media(c.metrics, "nothing_found", reason="stale_cover")
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    "code": c.serve_code,
                    "note": (
                        f"That code is only the cover picture of the item at {c.stale_link}. If "
                        f"that is "
                        f"the item being asked about, open it with instagram_read."
                    ),
                },
            )
    return (False, None, None)


async def _media_fetch__p5(c):
    if c.shared is not None:
        c.got = await c._shared_file_bytes(c.shared)
        if isinstance(c.got, dict):
            return (True, None, c.got)
        c.data, c.media_type = (c.got.data, c.got.media_type)
        c.sha256 = hashlib.sha256(c.data).hexdigest()
        c.serve_code = c.shared.handle
        c.is_cover = False
    c.kept_image = bool(c.url and c.serve_code)
    c.answer_code = c.shared.handle if c.shared is not None else c.sha256[:HANDLE_CHARS]
    if not c.url and c.shared is None and (not c.question) and (c.media_store is not None):
        try:
            c.stored = await c.media_store.get(sha256=c.sha256)
        except Exception:
            log.exception("media_fetch could not read the media index")
            c.stored = None
        if c.stored is not None and c.stored.comprehended:
            return (
                True,
                None,
                c._serve_media(
                    c.sha256,
                    _stored_reading(c.stored),
                    cached=True,
                    code=c.serve_code,
                    cover=c.is_cover,
                    kept=c.kept_image,
                ),
            )
    c.modality = _modality_of(c.media_type)
    if c.modality is None:
        _record_media(c.metrics, "not_an_image")
        return (
            True,
            None,
            {
                "status": "not_an_image",
                "code": c.answer_code,
                "note": ("That's a file, not something you can look at. Say you can't look at it."),
            },
        )
    c.describe_model = (c.describe_models or {}).get(c.modality, "")
    if not c.describe_model:
        _record_media(c.metrics, "unavailable", reason=f"no_{c.modality}_describer")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "code": c.answer_code,
                "note": (
                    f"I can't take in {c.modality}. Say you couldn't check it. Do not say it was "
                    f"empty "
                    f"and do not guess what was in it."
                ),
            },
        )
    c.describe_provider = (c.describe_providers or {}).get(c.modality, "omniroute")
    c.describe_ceiling = (c.describe_max_bytes or {}).get(c.modality, 0) or None
    c.cacheable_modality = c.modality != "video" and c.shared is None
    c.cached_description = (
        await c.vision_cache.aget(c.sha256)
        if c.vision_cache is not None and (not c.question) and c.cacheable_modality
        else None
    )
    if c.cached_description is not None:
        return (
            True,
            None,
            c._serve_media(
                c.sha256,
                c.cached_description,
                cached=True,
                code=c.serve_code,
                kept=c.kept_image,
                cover=c.is_cover,
            ),
        )
    c.video_key: VideoReadingKey | None = None
    if (
        c.modality == "video"
        and (not c.question)
        and (c.video_readings is not None)
        and (c.shared is None)
    ):
        c.view_inputs = "\x1f".join(
            (
                DESCRIBE_PROMPTS["video"],
                c.media_type,
                str(getattr(c.vision_source, ("reasoning_effort"), "")),
                video_encode_signature(),
            )
        )
        c.video_key = VideoReadingKey(
            sha256=c.sha256,
            provider=c.describe_provider,
            model=c.describe_model,
            chunk_seconds=c.video_chunk_seconds,
            chunk_max=c.video_max_chunks,
            reading_digest=hashlib.sha256(c.view_inputs.encode("utf-8")).hexdigest()[:32],
        )
        try:
            c.cached_reading = await c.video_readings.get(key=c.video_key)
        except Exception:
            log.exception("media_fetch could not read the video reading cache")
            c.cached_reading = None
        if c.cached_reading:
            return (
                True,
                None,
                c._serve_media(
                    c.sha256,
                    c.cached_reading,
                    cached=True,
                    code=c.serve_code,
                    kept=c.kept_image,
                    cover=c.is_cover,
                ),
            )
    return (False, None, None)


async def _media_fetch__p6(c):
    if c.url or c.shared is not None:
        c.data_for_vision = c.data
    else:
        try:
            c.data = await c.blob_store.get(key=c.sha256)
        except Exception:
            log.exception("media_fetch could not open blob %s", c.sha256[:HANDLE_CHARS])
            c.data = b""
        if not c.data:
            _record_media(c.metrics, "unavailable", reason="blob")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "code": c.answer_code,
                    "note": (
                        "I can't open that media right now. Say so, but do not say there is no "
                        "media and "
                        "do not guess what it showed."
                    ),
                },
            )
        c.data_for_vision = c.data
    c.prompt = f"{_QUERY_FRAME}\n\n{c.question}" if c.question else ""

    async def _ask(payload: bytes, windows: int):
        """One describer call on this modality's model, provider and
        ceiling. Never raises: `VisionClient.describe` turns every failure
        into an unavailable result itself, and this is the belt for those
        braces (`test_a_tool_never_raises_into_the_agent_loop`).

        `windows` is how many windows THIS read covers — the serial factor
        the per-call timeout divides by (`vision.derive_vision_timeout`).
        It is passed through, never used to compute a timeout here: that
        derivation lives in the client and nowhere else."""
        try:
            return await c.vision_source.describe(
                data=payload,
                media_type=c.media_type,
                model=c.describe_model,
                provider=c.describe_provider,
                max_bytes=c.describe_ceiling,
                prompt=c.prompt,
                windows=windows,
            )
        except Exception:
            log.exception("media_fetch vision call raised")
            return None

    c._ask = _ask
    c.coverage = ""
    c.video_read_whole = False
    return (False, None, None)


async def _media_fetch__p7(c):
    if c.modality == "video":
        c.cut = await cut_video(
            c.data_for_vision, chunk_seconds=c.video_chunk_seconds, max_chunks=c.video_max_chunks
        )
        if not c.cut.windows:
            c.view = await preprocess_media(c.data_for_vision, c.media_type)
            c.one = await c._ask(c.view, 1)
            if c.one is None or not getattr(c.one, "available", False):
                _record_media(
                    c.metrics,
                    "unavailable",
                    reason=getattr(c.one, "reason", "") or c.cut.reason or ("ffmpeg"),
                )
                return (
                    True,
                    None,
                    {
                        "status": "unavailable",
                        "code": c.answer_code,
                        "note": (
                            "I couldn't get a look at that clip just now. Say so, and do not "
                            "describe it "
                            "anyway."
                        ),
                    },
                )
            c.description = c.one.description
            c.coverage = (
                "(I could not tell how long this clip is, so I may not have watched all of it.)"
            )
            c.result = c.one
        else:
            c.segments, c.read_windows = await read_video_windows(
                cut=c.cut, media_type=c.media_type, ask=c._ask
            )
            if not c.read_windows:
                _record_media(c.metrics, "unavailable", reason="vision")
                return (
                    True,
                    None,
                    {
                        "status": "unavailable",
                        "code": c.answer_code,
                        "note": (
                            "I couldn't get a look at that clip just now. Say so, and do not "
                            "describe it "
                            "anyway."
                        ),
                    },
                )
            c.description = "\n".join(c.segments)
            c.coverage = describe_coverage(cut=c.cut, read=c.read_windows)
            c.result = None
            c.video_read_whole = not c.cut.degraded and len(c.read_windows) == len(c.cut.windows)
    else:
        c.result = await c._ask(await preprocess_media(c.data_for_vision, c.media_type), 1)
        if c.result is None or not getattr(c.result, "available", False):
            _record_media(
                c.metrics, "unavailable", reason=getattr(c.result, "reason", "") or ("vision")
            )
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "code": c.answer_code,
                    "note": (
                        "I couldn't get a look at that media just now. Say so, but do not say "
                        "there is no "
                        "media and do not describe it anyway."
                    ),
                },
            )
        c.description = c.result.description
    if c.coverage:
        c.description = f"{c.description}\n{c.coverage}"
    return (False, None, None)


async def _media_fetch__p8(c):
    if c.question:
        return (
            True,
            None,
            c._serve_media(
                c.sha256,
                c.description,
                cached=False,
                code=c.serve_code,
                kept=c.kept_image,
                cover=c.is_cover,
            ),
        )
    c._caller, c.persist = _MEDIA_READ.get()
    c.persist = c.persist and (not (c.url and c.url in c._instagram_story_urls))
    if c.video_key is not None and c.video_read_whole and c.persist:
        try:
            await c.video_readings.put(key=c.video_key, reading=c.description)
        except Exception:
            log.exception("media_fetch could not write the video reading cache")
    if c.vision_cache is not None and c.cacheable_modality and c.persist:
        await c.vision_cache.aput(c.sha256, c.description, model=c.describe_model)
    return (
        True,
        None,
        c._serve_media(
            c.sha256,
            c.description,
            cached=False,
            code=c.serve_code,
            kept=c.kept_image,
            cover=c.is_cover,
        ),
    )
    return (False, None, None)
