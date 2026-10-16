"""Files leaving Nora's computer: the record
of every `computer_export`, and the one rule that types what leaves.

**Any file, never a web page.**
What leaves is a file of any kind: a picture, a chart, a PDF, data, audio,
video, an archive. The one exception is a page people open as a document,
which is the document path's (ADR 0020: a JSON spec the portal renders), so
HTML never leaves here at all, and an SVG, which can carry script, may be
attached or kept but never published as a link.

**The VM only produces files; Nora publishes.** `computer_export` fetches the
bytes out of the VM, and everything that decides what they ARE happens on
Nora's side, on the bytes she received: the size, the sha256 and the media
type. Nothing the VM claims about its own output is trusted: not a digest it
sent, not a file extension.

**The record** (`ComputerExport`, table `computer_exports`, migration
`0083_computer_exports`): one row per export that reached its destination,
`link`, `chat` or `keep`, insert-only. It is the manifest (room,
path, sha256, artifact_id, url, turn), and it is what makes a revision
safe: `computer_export(..., artifact_id=X)` revises the artifact X only
when X's FIRST row belongs to the same room (`for_artifact`). An artifact
this table has never seen (one made before the computer existed, or one
another room made) is refused, not revised.

**Artifacts** (ADR 0023): every export holds its bytes in Nora's blob store
(`blob_key`), a link included. The portal's Artifacts section lists the
`chat` and `link` rows of the rooms that name the viewer (`list_shared`),
one line per link (its newest revision) and one per attachment; `keep` rows
are Nora's private record and are never listed. A link is addressed by its
`artifact_id`, a 12-hex handle Nora mints (`mint_artifact_id`); an
attachment, which has none, by its row `id`. The room rule is applied in the
same statement that reads the row, at read time, so leaving a room revokes
access to its files.

**The type rule** (`sniff_export_type`): read off the BYTES, never the
extension. A binary file is typed by its magic number
(`nora.domain.media.sniff_media_type` for the image four and ISO MP4, the
signatures in `_BINARY_SIGNATURES` for the rest), and a binary nothing
recognises leaves as `application/octet-stream`, which the portal serves as
a download. A text file is HTML when it opens the way the WHATWG MIME sniffing
standard says an HTML document opens (so a `.png` holding a page is refused
as the page it is, never typed as an image), SVG when its root element is
`<svg`, JSON when it parses as an object or an array. Only a text file that
is none of those, plain UTF-8 with no markup the standard recognises, takes
its subtype (`csv`, `md`, `txt`) from the file name, and every one of those
three is inert text. HTML is the only thing that is not exportable (`None`).

Every datetime crossing this port is UTC-aware.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from nora.domain.computer_events import ensure_utc_event
from nora.domain.documents import RoomKey
from nora.domain.media import sniff_media_type

__all__ = [
    "ARTIFACT_ID_CHARS",
    "DESTINATIONS",
    "NEVER_EXPORTED",
    "NOT_AS_LINK",
    "OPAQUE",
    "SHARED_DESTINATIONS",
    "ComputerExport",
    "ComputerExportStore",
    "SharedPage",
    "is_artifact_id",
    "mint_artifact_id",
    "normalise_export",
    "sniff_export_type",
]

#: Where an export goes (`ComputerExport.destination`): a portal link, an
#: attachment in the room, or Nora's own blob store.
DESTINATIONS: frozenset[str] = frozenset({"link", "chat", "keep"})

#: What the portal's Artifacts section lists (ADR 0023 decision 1): what was
#: sent to a room. `keep` is "stores it privately and sends nothing", so it
#: is never among them.
SHARED_DESTINATIONS: tuple[str, ...] = ("chat", "link")

#: A link's `artifact_id`: 12 lower-case hex characters, the document
#: handle's shape (`nora.domain.documents.DOCUMENT_HANDLE_CHARS`), random.
ARTIFACT_ID_CHARS = 12
_ARTIFACT_ID = re.compile(r"[0-9a-f]{12}")

#: What a file nothing else recognises leaves as: the portal serves it with
#: `nosniff` as a download (`nora.web.artifacts`), so it is inert there.
OPAQUE = "application/octet-stream"

#: The one media type that never leaves: a page is the document path's.
NEVER_EXPORTED: frozenset[str] = frozenset({"text/html"})

#: Magic numbers past the ones `sniff_media_type` reads, checked in order.
#: Each is a format definition, matched on the leading bytes only.
_BINARY_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"OggS", "audio/ogg"),
    (b"fLaC", "audio/flac"),
    (b"\x1aE\xdf\xa3", "video/webm"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
)

#: Allowed out, but never as a link: an SVG can run script. It may be
#: attached or kept.
# ponytail: made when a link served it inline on Drop's public origin. The
# portal serves every SVG as an attachment (ADR 0023 decision 3), so this
# could lift; it stays until someone asks for an SVG link.
NOT_AS_LINK: frozenset[str] = frozenset({"image/svg+xml"})


def mint_artifact_id() -> str:
    """A new link's `artifact_id`: 48 random bits as 12 hex characters. The
    caller checks `for_artifact` is None before using it, so a collision is a
    second draw, never two rooms sharing one id."""
    return uuid.uuid4().hex[:ARTIFACT_ID_CHARS]


def is_artifact_id(text: str) -> bool:
    """Whether `text` is exactly an `artifact_id` this build mints. The one
    check a portal handle passes before it reaches a query."""
    return _ARTIFACT_ID.fullmatch(text) is not None

#: How far into a text file the markup checks look: the standard's own
#: "resource header" is 1445 bytes; this reads a little more, in characters.
_HEAD_CHARS = 2048

#: WHATWG MIME Sniffing §7.1, "identifying a resource with an unknown MIME
#: type": after leading whitespace, a document that opens with one of these
#: (case-insensitive) and then a space or `>` is HTML. `<!--` needs no
#: terminator.
_HTML_OPENINGS: tuple[str, ...] = (
    "<!doctype html", "<html", "<head", "<script", "<iframe", "<h1", "<div",
    "<font", "<table", "<a", "<style", "<title", "<b", "<body", "<br", "<p",
)

#: The text subtypes the NAME may choose between, and only once the bytes
#: said plain text: all three are inert.
_TEXT_BY_SUFFIX: dict[str, str] = {
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


def _looks_like_html(head: str) -> bool:
    lowered = head.lower()
    if lowered.startswith("<!--"):
        return True
    for opening in _HTML_OPENINGS:
        if lowered.startswith(opening):
            after = lowered[len(opening):len(opening) + 1]
            if after in (" ", ">", "\t", "\n", "\r", "\f"):
                return True
    return False


def _looks_like_svg(head: str) -> bool:
    lowered = head.lower()
    if lowered.startswith("<svg"):
        return True
    if lowered.startswith("<?xml"):
        # The root element, past the declaration and any comment or doctype.
        return "<svg" in lowered
    return False


def _as_text(data: bytes) -> str | None:
    """`data` as text: UTF-8, or UTF-16 when it opens with a byte-order mark
    (Excel's "Unicode Text", PowerShell's output). None when it is neither,
    or holds a NUL, which no text file does."""
    codec = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    try:
        text = data.decode(codec)
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def _is_mpeg_frame(data: bytes) -> bool:
    """An MPEG audio frame header: the 11-bit sync, a defined layer, and a
    bitrate index that is neither free (0) nor bad (15). Checked after the
    text decode, so a UTF-16 byte-order mark (`FF FE`) never reaches it."""
    return (len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
            and data[1] & 0x06 != 0 and (data[2] >> 4) not in (0x0, 0xF))


def sniff_export_type(data: bytes, path: str) -> str | None:
    """The media type `data` leaves the computer as, or None when it is a web
    page, the one kind of file that may not leave. `path` is consulted only
    to name plain text (see the module docstring); it never overrules what
    the bytes are."""
    binary = sniff_media_type(data)
    if binary:
        return binary
    for magic, media_type in _BINARY_SIGNATURES:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" and data[3:4] in (b"\x02", b"\x03", b"\x04"):
        return "audio/mpeg"  # an ID3v2 tag names its version; "ID3" alone is text
    text = _as_text(data)
    if text is None:
        # Not text in any encoding a page is read in, but a page in latin-1
        # or another 8-bit charset still opens like one: refused as a page.
        if _looks_like_html(data[:_HEAD_CHARS].decode("latin-1").lstrip(" \t\n\r\f")):
            return None
        if _is_mpeg_frame(data):
            return "audio/mpeg"  # an MP3 with no ID3 tag
        return OPAQUE
    text = text.removeprefix("﻿")
    head = text.lstrip(" \t\n\r\f")[:_HEAD_CHARS]
    if _looks_like_html(head):
        return None  # a page: the document path's, never an export
    if _looks_like_svg(head):
        return "image/svg+xml"
    if head[:1] in ("{", "["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return "application/json"
    name = path.rsplit("/", 1)[-1].lower()
    for suffix, media_type in _TEXT_BY_SUFFIX.items():
        if name.endswith(suffix):
            return media_type
    return "text/plain"


@dataclass(frozen=True, slots=True)
class ComputerExport:
    """One file that left the computer.

    WHERE FROM: the room (`platform`, `conversation_id`), its workspace
    (`scope`) and the `path` in it. WHAT: `sha256` and `size_bytes` of the
    bytes Nora received (computed on her side), `media_type` from
    `sniff_export_type`, `title`. WHERE TO: `destination`; for `link`, the
    `artifact_id` and portal `url` (`revised` when this row revised an
    artifact the room had exported before); for every destination, the
    `blob_key` the bytes are held under in Nora's blob store. PROVENANCE:
    the turn (`tag_id`), the computer session and the audit operation
    (`operation_id`, the two `computer_events` rows of this call).
    """

    id: UUID
    created_at: datetime
    platform: str
    conversation_id: str
    scope: str
    path: str
    destination: str
    sha256: str
    size_bytes: int
    media_type: str
    title: str
    tag_id: str
    operation_id: UUID
    artifact_id: str | None = None
    url: str | None = None
    revised: bool = False
    blob_key: str | None = None
    session_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.destination not in DESTINATIONS:
            raise ValueError(f"unknown export destination {self.destination!r}")
        if not self.media_type or self.media_type in NEVER_EXPORTED:
            raise ValueError(f"media type {self.media_type!r} is not exportable")
        if self.destination == "link" and self.media_type in NOT_AS_LINK:
            raise ValueError(f"{self.media_type} is never published as a link")
        if self.destination == "link" and not (self.artifact_id and self.url):
            raise ValueError("a link export carries its artifact_id and url")
        if self.destination != "link" and (self.artifact_id or self.revised):
            raise ValueError("only a link export carries an artifact_id")
        if not self.blob_key:
            raise ValueError("every export carries its blob key")


def normalise_export(export: ComputerExport) -> ComputerExport:
    return replace(export, created_at=ensure_utc_event(export.created_at, field="created_at"))


@dataclass(frozen=True, slots=True)
class SharedPage:
    """One page of the Artifacts list: its rows, newest first, and how many
    rows the whole list has."""

    items: tuple[ComputerExport, ...]
    total: int


@runtime_checkable
class ComputerExportStore(Protocol):
    async def record(self, export: ComputerExport) -> None:
        """Insert-only. MAY raise on a storage failure; the tool counts it and
        still answers, because the export already happened."""
        ...

    async def for_artifact(self, *, artifact_id: str) -> ComputerExport | None:
        """The FIRST export that produced `artifact_id`, or None when no room
        ever exported it through the computer. Its room is the artifact's
        owner: the only room that may revise it. MAY raise."""
        ...

    async def list_shared(
        self, *, rooms: frozenset[RoomKey], limit: int, offset: int, query: str = "",
    ) -> SharedPage:
        """The Artifacts list for a viewer in `rooms`: the `chat` rows and,
        per `artifact_id`, the newest `link` row, of those rooms only; never a
        `keep` row. Newest first. `query` (already `normalize_query`d) keeps
        the rows whose title contains it, ignoring case, as text and never as
        a pattern. No rooms is an empty page. MAY raise."""
        ...

    async def shared_link(
        self, *, artifact_id: str, rooms: frozenset[RoomKey],
    ) -> ComputerExport | None:
        """The newest `link` row of `artifact_id` made in one of `rooms`, or
        None: an artifact in no room of the viewer's is the same None as one
        that does not exist. MAY raise."""
        ...

    async def shared_attachment(
        self, *, export_id: UUID, rooms: frozenset[RoomKey],
    ) -> ComputerExport | None:
        """The `chat` row `export_id` when it was sent in one of `rooms`,
        else None (a `keep` or `link` row by its id is None too). MAY raise."""
        ...
