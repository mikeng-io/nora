from __future__ import annotations

import mimetypes
import re
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from nora.agent.link_owners import LinkClaim, LinkOwner
from nora.agent.link_owners import owner_of as _owner_of
from nora.agent.toolkit.transcript import _INBOX_SUFFIX
from nora.domain.external_files import (
    DELETED,
    NO_DOWNLOAD,
    NO_PART,
    NOT_SHARED,
    NOT_TEXT,
    TOO_LARGE,
    TOO_SLOW,
    render_file_links,
)
from nora.domain.instagram_links import parse_link as _parse_instagram_link
from nora.domain.media import (
    handle_hex,
)


def _file_handle_pointer(file_code: str) -> dict:
    """The answer `web_fetch` gives a shared-file link handle passed to it: the"""
    return {
        "status": "nothing_found",
        "note": "That is a shared file, not a web page, and its code is not part "
        f"of any link. Read it with file_fetch using code {file_code}; if "
        "it is a picture or a video, look at it with media_fetch and that "
        "code (or, if your computer is on, copy it there with "
        "computer_import and that code).",
    }


def _handle_hex(raw: str) -> str:
    """`nora.domain.media.handle_hex`: one parse for every tool that takes a"""
    return handle_hex(raw)


def _is_external_reference(raw: str) -> bool:
    """A reference code that names a LOCATION rather than a held blob: a URL or"""
    text = raw.strip().lower()
    return "://" in text or text.startswith("data:")


_COVER_NOTE = (
    "This is only the cover image of a shared reel, post or story: one still "
    "picture, not the item itself. What the item shows or says is behind the "
    "item's link, if that line carries one."
)

_TRUNCATED_CODE_NOTE = (
    "This chat has more sent media than one lookup reads, so that code may be "
    "older than what was searched. Say you could not check that far back, and "
    "do not say it does not exist. Ask for a more recent photo."
)

_MEDIA_READING_NOTE = (
    "A vision model's notes on media somebody sent or linked, so you can see "
    "it too; whoever sent it has usually seen the thing itself. Take them as "
    "how it looks, not as fact you can vouch for."
)

_KEEPABLE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

_KEPT_NOTE = (
    "The picture is kept, and `code` names it: put it in a document's Image to "
    "show it there, and credit the page it came from under Sources."
)

_SHARED_PAGE_LINE_RE = re.compile(
    r"\[shared (post|reel)(?: by @[A-Za-z0-9._]+)? (https://www\.instagram\.com/[^\s\]]+)"
)


def _shared_page_link(body: str) -> str:
    """The validated post or reel link of the shared line in `body`, or `""`."""
    for kind, link in reversed(_SHARED_PAGE_LINE_RE.findall(body or "")):
        parsed = _parse_instagram_link(link)
        if parsed is not None and parsed.kind == kind:
            return parsed.url
    return ""


def _other_link_owner(
    text: str,
    this_tool: str,
    unwired: frozenset[str] = frozenset(),
) -> tuple[LinkOwner, LinkClaim] | None:
    """`owner_of(text)`, filtered to a claim by a DIFFERENT tool than"""
    found = _owner_of(text, unwired=unwired)
    if found is None or found[0].tool == this_tool:
        return None
    return found


_OFFICE_SUFFIXES: Mapping[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}

_OCTET_STREAM = "application/octet-stream"


def _copy_suffix(media_type: str, name: str) -> str:
    """A shared file's suffix in `inbox/`: the one its media type (as the"""
    mime = media_type.split(";", 1)[0].strip().lower()
    if mime in _OFFICE_SUFFIXES:
        return _OFFICE_SUFFIXES[mime]
    if mime and mime != _OCTET_STREAM:
        guessed = mimetypes.guess_extension(mime) or ""
        return guessed if _INBOX_SUFFIX.fullmatch(guessed) else ""
    dot = name.rfind(".")
    if dot > 0 and _INBOX_SUFFIX.fullmatch(name[dot:]):
        return name[dot:].lower()
    return ""


_FILE_MORE_NOTE = " This is not the whole file: read the next part only if the answer needs it."


def _file_note(reason: str, account: str) -> str | None:
    """The note for one shared-file `reason`. `NOT_SHARED` names Nora's own
    Google account (`[google] account`, empty → a generic phrasing); the rest
    are fixed strings."""
    if reason == NOT_SHARED:
        who = account or "Nora's Google account"
        return (
            "That file isn't shared with me, or it no longer exists. Ask the owner to "
            f"share it with {who} as a Viewer, then ask again."
        )
    return _FILE_NOTES.get(reason)


_FILE_NOTES: Mapping[str, str] = {
    TOO_LARGE: "That file is too large for me to read as text. Say so.",
    TOO_SLOW: "That file took too long to read as text, so I stopped. Say so.",
    NOT_TEXT: (
        "That kind of file can't be read as text (a picture, a video, an archive). "
        "If it is a picture or a video, look at it with media_fetch and the same "
        "code; otherwise say so, and don't guess what is in it."
    ),
    NO_DOWNLOAD: (
        "The owner has turned off downloading for that file, so I can't read it. Say so."
    ),
    DELETED: "That file has been deleted. Say so.",
    NO_PART: "That file has fewer parts than that. Read part 1 to see how many it has.",
}

_FILE_COPY_NOTES: Mapping[str, str] = {
    TOO_LARGE: "That file is too large to copy onto your computer. Say so.",
    NOT_TEXT: "That file has nothing to download, so it can't be copied. Say so.",
    NO_DOWNLOAD: (
        "The owner has turned off downloading for that file, so I can't copy it. Say so."
    ),
    "refused": "I can't copy that file. Say so; don't claim it is on your computer.",
}

_LOOKABLE: tuple[str, ...] = ("image/", "video/")

_TEXT_KINDS = frozenset({"document", "spreadsheet", "presentation", "pdf"})

_FILE_LOOK_NOTES: Mapping[str, str] = {
    TOO_LARGE: "That file is too large for me to look at. Say so.",
    NOT_TEXT: "That file has nothing I can look at. Say so; don't guess what it shows.",
    NO_DOWNLOAD: (
        "The owner has turned off downloading for that file, so I can't look at it. Say so."
    ),
    "refused": "I can't look at that file. Say so; don't guess what it shows.",
    "unavailable": (
        "I can't open shared files right now. Say so; don't guess what the file shows."
    ),
}

_FILE_STATUS_NOTES: Mapping[str, str] = {
    "refused": "I can't read that file. Say so; don't guess what it says.",
    "nothing_found": "That file isn't there any more. Say so.",
    "unavailable": "I can't open shared files right now. Say so; don't guess what the file says.",
}

_FILE_NAME_CHARS = 200


def _file_name(name: str) -> str:
    """A file's name as a result field: links rendered as handles (R-18),"""
    flat = " ".join(render_file_links(name).split())
    return flat if len(flat) <= _FILE_NAME_CHARS else flat[: _FILE_NAME_CHARS - 1] + "…"


def _file_modified(ms: int | None) -> str:
    """When the file was last changed, as the provider reported it at this"""
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo("Asia/Hong_Kong")).isoformat(
        timespec="minutes",
    )
