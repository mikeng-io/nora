"""Files leaving Nora's computer (design/sandbox-runtime §6, §7): the record
of every `computer_export`, and the one rule that types what leaves.

**The VM only produces files; Nora publishes.** `computer_export` fetches the
bytes out of the VM, and everything that decides what they ARE happens on
Nora's side, on the bytes she received: the size, the sha256 and the media
type. Nothing the VM claims about its own output is trusted: not a digest it
sent, not a file extension.

**The record** (`ComputerExport`, table `computer_exports`, migration
`0083_computer_exports`): one row per export that reached its destination,
`link`, `chat` or `keep`, insert-only. It is the manifest §7 names ("room,
path, sha256, artifact_id, url, turn"), and it is what makes a revision
safe: `computer_export(..., artifact_id=X)` revises the Drop artifact X only
when X's FIRST row belongs to the same room (`for_artifact`). An artifact
this table has never seen (one Smith made through `make_artifact`, or one
another room made) is refused, not revised.

**The type rule** (`sniff_export_type`): read off the BYTES, never the
extension. The binary formats come from their magic numbers
(`nora.domain.media.sniff_media_type` for the image four, `%PDF-` for PDF);
a text file is HTML when it opens the way the WHATWG MIME sniffing standard
says an HTML document opens (so a `.png` holding a page is typed as the page
it is), SVG when its root element is `<svg`, JSON when it parses as an object
or an array. Only a text file that is none of those, plain UTF-8 with no
markup the standard recognises, takes its subtype (`csv`, `md`, `txt`) from
the file name, and every one of those three is inert text. Anything else is
not exportable (`None`).

Every datetime crossing this port is UTC-aware.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from nora.domain.computer_events import ensure_utc_event
from nora.domain.media import sniff_media_type

__all__ = [
    "DESTINATIONS",
    "EXPORT_MEDIA_TYPES",
    "ComputerExport",
    "ComputerExportStore",
    "normalise_export",
    "sniff_export_type",
]

#: Where an export goes (`ComputerExport.destination`): a Drop link, an
#: attachment in the room, or Nora's own blob store.
DESTINATIONS: frozenset[str] = frozenset({"link", "chat", "keep"})

#: The allowlist: the media types a file may leave the computer as, by the
#: short name the tool's result and the audit row use.
EXPORT_MEDIA_TYPES: dict[str, str] = {
    "html": "text/html",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "csv": "text/csv",
    "md": "text/markdown",
    "txt": "text/plain",
    "json": "application/json",
}
_ALLOWED: frozenset[str] = frozenset(EXPORT_MEDIA_TYPES.values())

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


def sniff_export_type(data: bytes, path: str) -> str | None:
    """The media type `data` leaves the computer as, or None when it may not
    leave at all. `path` is consulted only to name plain text (see the
    module docstring); it never overrules what the bytes are."""
    binary = sniff_media_type(data)
    if binary:
        return binary if binary in _ALLOWED else None
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    text = text.removeprefix("﻿")
    head = text.lstrip(" \t\n\r\f")[:_HEAD_CHARS]
    if _looks_like_html(head):
        return "text/html"
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
    Drop `artifact_id` and `url` (`revised` when this row revised an
    artifact the room had exported before); for `keep` and `chat`, the
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
        if self.media_type not in _ALLOWED:
            raise ValueError(f"media type {self.media_type!r} is not exportable")
        if self.destination == "link" and not (self.artifact_id and self.url):
            raise ValueError("a link export carries its artifact_id and url")
        if self.destination != "link" and (self.artifact_id or self.revised):
            raise ValueError("only a link export carries an artifact_id")
        if self.destination in ("keep", "chat") and not self.blob_key:
            raise ValueError("a kept or sent export carries its blob key")


def normalise_export(export: ComputerExport) -> ComputerExport:
    return replace(export, created_at=ensure_utc_event(export.created_at, field="created_at"))


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
