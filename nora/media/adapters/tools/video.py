from __future__ import annotations

import logging
from types import SimpleNamespace

from nora.agent.toolkit.files import (
    _handle_hex,
    _is_external_reference,
)
from nora.agent.toolkit.results import _record
from nora.domain.media import HANDLE_CHARS
from nora.domain.video import VideoReference, VideoRequest
from nora.media.adapters.tools.create import (
    _make__held_media,
)
from nora.media.adapters.tools.shared import _VIDEO_REF_KINDS, _VIDEO_REF_WORDS
from nora.obs.logging import current_tag_id

log = logging.getLogger("nora.tools")


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
        k_, _, v_ = await _video_create__prepare(c)
        if k_:
            return v_
        k_, _, v_ = await _video_create__started(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return video_create


async def _video_create__prepare(c):
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
    return await _video_create__resolve_references(c)


async def _video_create__resolve_references(c):
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


async def _video_create__started(c):
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
