"""External files: a shared file link becomes a handle.

A link somebody posts to a file Nora could open (a Google Doc, Sheet, Slides
deck or Drive file) is RECOGNISED, never fetched: this module turns the URL
into an `ExternalFileRef` (the provider, the provider's file id, the URL as
posted and, for an older "anyone with the link" file, its resource key) with
no network call. Ingest stores the refs beside the message
(`messages.external_files_json`); the body stays verbatim, because Postgres is
the record.

**What a model reads is rendered, in place.** `render_file_links` replaces
each recognised URL with a LINK handle, `[Google Doc link c41d09]`: words
naming what the URL says it points to, then six hex characters of
`sha256("<provider>:<file_id>")`. It says «link», never «file», because Nora
holds no file, only the link. The words come from the URL alone (`/document/` is a
Doc), from one table per provider (`_GOOGLE_LABELS`), so a second provider
brings its own. So the provider's file id (a key-shaped string R-18 keeps out
of replies) never reaches a prompt and the link cannot be echoed. The same
file posted twice is the same code. Unlike an attachment's placeholder, which
is appended after the text, the handle stands where the link was. The render
works from the TEXT, not from the stored refs, so a row
written before refs were stored renders the same way (migration
`0104_external_files` backfills those rows' refs too, so the code resolves).

**Never cached.** A linked file is live and changes
over time, unlike an attachment, so nothing fetched through a link is kept:
not content, text, bytes or metadata. Ingest keeps the parsed ref only, every
open fetches fresh, and the code hashes `provider:file_id`, never content.
`tests/architecture/test_external_files_are_never_cached.py` enforces it.

**Why the recognisers live in the domain, vendor names and all.** The render
has to run inside `nora.domain.blocks.render_rows`/`render_elements`, the
bottom layer every transcript goes through, so whatever it calls must sit at
that layer; `nora.infra` cannot be imported from here. `instagram_links` and
`linkedin_links` are the precedent: a pure URL parser shared by every reader
of the URL, in the domain, standard library only. The provider ADAPTERS
(describe, read, fetch_bytes) stay in `nora/infra/external_files/` and
call `recognise` from here; nothing above them names a vendor except the
`provider` string a ref carries.

**The host is an exact match**, never a suffix (`docs.google.com.evil.x` and
`evil-google.com` are refused); a port, credentials in the netloc or a scheme
other than http(s) refuse the link. A file id is Google's own alphabet
(letters, digits, `-`, `_`) and at least `_MIN_ID_CHARS` long, which also
refuses a published-to-web `/d/e/<id>/pub` link (its id is not a Drive id).
A folder link is not a file and is not recognised: it stays an ordinary
link.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

from nora.domain.media import HANDLE_CHARS, ROOM_MEDIA_DAYS, ROOM_MEDIA_ROWS, handle_hex

if TYPE_CHECKING:
    from nora.domain.ports import RecordStore

#: The providers a ref can name. One today; a second provider is a new
#: recogniser in `_RECOGNISERS` and a new member here.
Provider = Literal["google_drive"]
PROVIDERS: tuple[str, ...] = ("google_drive",)


@dataclass(frozen=True)
class ExternalFileRef:
    """One recognised file link. `url` is the text as posted (never rendered
    to a model); `resource_key` is the `resourcekey` query parameter an older
    link-shared Drive file needs, or None."""

    provider: str
    file_id: str
    url: str
    resource_key: str | None = None
    #: The handle's words, from the provider's label table: what the URL says
    #: it points to (`Google Sheet link`). Never «file»: she holds a link.
    label: str = "link"

    @property
    def digest(self) -> str:
        return file_digest(self.provider, self.file_id)

    @property
    def handle(self) -> str:
        return self.digest[:HANDLE_CHARS]


#: The one vocabulary every adapter maps its files onto, so a model never
#: sees a vendor's MIME types.
FileKind = Literal["document", "spreadsheet", "presentation", "pdf", "other"]


@dataclass(frozen=True)
class ExternalFile:
    """What the first open learns and ingest never knew: the file's
    name, kind, owner and last-modified time (epoch ms, None when the
    provider gave none)."""

    ref: ExternalFileRef
    name: str
    kind: FileKind
    owner: str = ""
    modified_ms: int | None = None


@dataclass(frozen=True)
class FilePart:
    """One part of a file read as text. `part` counts from 1 and `parts` is
    how many there are: a long file is paged, never truncated silently."""

    text: str
    part: int
    parts: int


@dataclass(frozen=True)
class FileBytes:
    """A file's bytes, fetched afresh for a copy to her computer, and
    `media_type`, the IANA type of THOSE bytes: a native document,
    spreadsheet or presentation is exported to its office format (docx, xlsx,
    pptx), so its type is the export's, never the provider's own."""

    data: bytes
    media_type: str


class ExternalFileError(Exception):
    """The one exception an `ExternalFilesSource` raises. `status` is the
    house vocabulary the open tool answers with: `refused` (not shared
    with her, or too large to read as text), `nothing_found` (deleted, or no
    such file) or `unavailable` (the provider could not be asked: no
    account, a revoked token, a transport failure). Never `ok`. `kind` is the
    file's kind when the refusal knows it (`NOT_ACCEPTED`), else None."""

    def __init__(
        self, status: Literal["refused", "nothing_found", "unavailable"], reason: str,
        *, kind: FileKind | None = None,
    ) -> None:
        super().__init__(f"{status}: {reason}")
        self.status = status
        self.reason = reason
        self.kind = kind


#: The stable codes an adapter puts in `ExternalFileError.reason` when it has
#: one, so the open tool picks its note by code and never parses an adapter's
#: wording. Any other reason is free text for the log.
NOT_SHARED = "not_shared"      # refused: not shared with her (or no longer there)
TOO_LARGE = "too_large"        # refused: too large to read as text
NOT_TEXT = "not_text"          # refused: a kind that is not read as text
NO_DOWNLOAD = "no_download"    # refused: the owner turned off download and copy
DELETED = "deleted"            # nothing_found: the file is in the bin, or no such file
NO_PART = "no_part"            # nothing_found: the file has fewer parts than asked
TOO_SLOW = "too_slow"          # refused: reading it as text took past the deadline
NOT_ACCEPTED = "not_accepted"  # refused: its bytes are not a type the caller accepts


def accepts_media_type(media_type: str, accept: tuple[str, ...]) -> bool:
    """Whether `media_type` starts with one of `accept`'s prefixes (`image/`,
    `video/`); an empty `accept` takes every type. Case-insensitive."""
    mime = media_type.split(";", 1)[0].strip().lower()
    return not accept or mime.startswith(tuple(a.lower() for a in accept))


def file_digest(provider: str, file_id: str) -> str:
    """The full hex digest a file's handle is the head of."""
    return hashlib.sha256(f"{provider}:{file_id}".encode()).hexdigest()


def render_handle(ref: ExternalFileRef) -> str:
    return f"[{ref.label} {ref.handle}]"


# --- Google -----------------------------------------------------------------

_GOOGLE_HOSTS = frozenset({
    "docs.google.com", "drive.google.com", "drive.usercontent.google.com",
})
#: The Google adapter's words, keyed by what the URL names: the docs.google.com
#: kind segment, or `drive` for drive.google.com/file|open|uc. The ONE place
#: they are written; a second provider adds its own table to `LINK_LABELS`.
_GOOGLE_LABELS = {
    "document": "Google Doc link",
    "spreadsheets": "Google Sheet link",
    "presentation": "Google Slides link",
    "drive": "Google Drive link",
}
_DOCS_KINDS = frozenset(k for k in _GOOGLE_LABELS if k != "drive")
_MIN_ID_CHARS = 16
#: A file id is the LEADING run of Google's id alphabet in its segment or
#: parameter, at least `_MIN_ID_CHARS` long: whatever chat glued after it
#: (`'s`, `/`, `%2Fedit`) is not part of the id.
_ID_RUN = re.compile(rf"[A-Za-z0-9_-]{{{_MIN_ID_CHARS},}}")
_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
_ACCOUNT_RE = re.compile(r"[0-9]{1,2}")


def _id_in(raw: str) -> str | None:
    found = _ID_RUN.match(raw)
    return found.group(0) if found else None


def _strip_prefixes(segments: list[str], at: int) -> list[str]:
    """Drop a multi-account `u/<n>` pair, or a legacy Workspace `a/<domain>`
    pair, at `at` (`/document/u/1/d/<id>`, `/a/example.com/document/d/<id>`)."""
    if len(segments) > at + 1 and (
        (segments[at] == "u" and _ACCOUNT_RE.fullmatch(segments[at + 1]))
        or segments[at] == "a"
    ):
        return segments[:at] + segments[at + 2:]
    return segments


def _query_id(query: dict[str, list[str]]) -> str | None:
    ids = query.get("id", [])
    return _id_in(ids[0]) if len(ids) == 1 else None


def _google_file_id(
    host: str, path: str, query: dict[str, list[str]],
) -> tuple[str, str] | None:
    """`(label key, file id)`, or None. Path words compare case-blind; the id
    keeps its case."""
    raw = [s for s in path.split("/") if s]
    low = [s.lower() for s in raw]
    if host == "drive.usercontent.google.com":
        found = _query_id(query) if low == ["download"] else None
        return ("drive", found) if found else None
    # A legacy Workspace prefix comes first: /a/<domain>/...
    if low[:1] == ["a"] and len(low) > 1:
        raw, low = raw[2:], low[2:]
    if host == "docs.google.com":
        if low in (["uc"],):
            found = _query_id(query)
            return ("drive", found) if found else None
        # /document|spreadsheets|presentation/[u/<n>/]d/<id>/...
        low, raw = _strip_prefixes(low, 1), _strip_prefixes(raw, 1)
        if len(low) >= 3 and low[0] in _DOCS_KINDS and low[1] == "d":
            found = _id_in(raw[2])
            return (low[0], found) if found else None
        return None
    # drive.google.com: /[u/<n>/]file/[u/<n>/]d/<id>/..., /[u/<n>/]open|uc?id=
    low = _strip_prefixes(_strip_prefixes(low, 0), 1)
    raw = _strip_prefixes(_strip_prefixes(raw, 0), 1)
    if len(low) >= 3 and low[0] == "file" and low[1] == "d":
        found = _id_in(raw[2])
        return ("drive", found) if found else None
    if low in (["open"], ["uc"]):
        found = _query_id(query)
        return ("drive", found) if found else None
    return None


def _google(url: str) -> ExternalFileRef | None:
    text = url if "://" in url else f"https://{url}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    if parts.username is not None or parts.password is not None or port is not None:
        return None
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query, keep_blank_values=False)
    if host not in _GOOGLE_HOSTS:
        return None
    found = _google_file_id(host, parts.path, query)
    if found is None:
        return None
    kind, file_id = found
    keys = query.get("resourcekey", [])
    key = keys[0] if len(keys) == 1 and _KEY_RE.fullmatch(keys[0]) else None
    return ExternalFileRef(
        provider="google_drive", file_id=file_id, url=url, resource_key=key,
        label=_GOOGLE_LABELS[kind],
    )


#: One pure recogniser per provider, tried in order.
_RECOGNISERS: tuple[Callable[[str], ExternalFileRef | None], ...] = (_google,)


#: Every handle's words, across providers: what `link_handles_in` reads.
LINK_LABELS: frozenset[str] = frozenset(_GOOGLE_LABELS.values())

_LINK_HANDLE_RE = re.compile(
    r"\[(" + "|".join(re.escape(x) for x in sorted(LINK_LABELS, key=len, reverse=True))
    + r") ([0-9a-f]{" + str(HANDLE_CHARS) + r"})\]"
)


def link_handles_in(text: str) -> tuple[tuple[str, str], ...]:
    """`(label, handle)` for every link handle in rendered text, in order: the
    reader's half of `render_handle`, `nora.domain.media.placeholders_in`'s
    shape over the multi-word link labels. `handle_hex` reads any one of them
    (`[Google Doc link c41d09]` -> `c41d09`) without a change."""
    return tuple((m.group(1), m.group(2)) for m in _LINK_HANDLE_RE.finditer(text))


_QUOTED_HANDLE_RE = re.compile(
    r"\[?\s*(?:" + "|".join(re.escape(x) for x in sorted(LINK_LABELS, key=len, reverse=True))
    + r")\s+([0-9a-f]{" + str(HANDLE_CHARS) + r"})\s*\]?",
    re.IGNORECASE,
)


def quoted_handle_code(text: str) -> str | None:
    """The code of a link handle a model passed back as a tool argument, with
    or without its brackets (`[Google Doc link c41d09]`, `Google Doc link
    c41d09`), or `None`. A handle is never a URL, so a fetcher that gets one
    points at `file_fetch` instead of asking for "the actual URL" the model
    was never shown."""
    m = _QUOTED_HANDLE_RE.fullmatch(text.strip())
    if m:
        return m.group(1).lower()
    return _code_in_made_up_link(text.strip())


_CODE_SEGMENT = re.compile(r"[0-9a-fA-F]{" + str(HANDLE_CHARS) + r"}")


def _code_in_made_up_link(text: str) -> str | None:
    """The code in a provider link the MODEL built around a handle's code
    (`https://drive.google.com/file/d/491db6`), or `None`. Shown
    `[Google Drive link 491db6]`, a model may read the code as the file id,
    build the URL and send it to `web_fetch` or `file_fetch(link=...)`. A real file id is at
    least `_MIN_ID_CHARS` long, so a link `recognise` accepts is never read
    here, and exactly one six-hex segment is required."""
    try:
        parts = urlsplit(text if "://" in text else f"https://{text}")
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme.lower() not in ("http", "https") or port is not None
        or parts.username is not None or host not in _GOOGLE_HOSTS
        or recognise(text) is not None
    ):
        return None
    # Only where a file id goes: after `d` or `folders` in the path, or the
    # `id` parameter. Any other six-hex value (`gid=123456`, a word like
    # `decade`) belongs to the link, not to a handle.
    segments = [unquote(x) for x in parts.path.split("/") if x]
    slots = [b for a, b in itertools.pairwise(segments) if a in ("d", "folders")]
    slots += [v for k, v in parse_qsl(parts.query) if k == "id"]
    codes = {x.lower() for x in slots if _CODE_SEGMENT.fullmatch(x)}
    return codes.pop() if len(codes) == 1 else None


#: How deep a link may sit inside wrappers (a redirect inside a redirect
#: inside SafeLinks) and still be found. Bounds the work per URL.
_NEST_DEPTH = 4
_NEST_FANOUT = 4
_EMBEDDED_SCHEME = re.compile(r"https?(?::|%3A)(?://|%2F%2F)", re.IGNORECASE)


def _inner_urls(url: str) -> list[str]:
    """The URLs a wrapper carries: every query value (percent-decoded by
    `parse_qs`) and every tail that starts at an embedded scheme, raw or
    percent-encoded. Google's `/url?q=`, Outlook SafeLinks' `?url=`,
    Facebook's `l.php?u=`, a `?next=` and a path-embedded link all reduce to
    one of these, so no wrapper is named."""
    # Bounded: at most `_NEST_FANOUT` candidates per level, taken lazily, so a
    # crafted URL with thousands of embedded schemes costs a fixed number of
    # parses. # ponytail: a link hidden behind more than that many decoys is
    # not unwrapped; the poster chose to hide it.
    out: list[str] = []
    text = url if "://" in url else f"https://{url}"
    try:
        query = urlsplit(text).query
    except ValueError:
        query = ""
    for _key, value in parse_qsl(query, keep_blank_values=False, max_num_fields=None):
        if "/" in value:
            out.append(value.strip())
            if len(out) >= _NEST_FANOUT:
                return out
    for match in itertools.islice(_EMBEDDED_SCHEME.finditer(url, 1), _NEST_FANOUT - len(out)):
        tail = url[match.start():]
        out.append(unquote(tail) if "%" in tail else tail)
    return out


def _recognise(url: str, depth: int) -> ExternalFileRef | None:
    for recogniser in _RECOGNISERS:
        ref = recogniser(url)
        if ref is not None:
            return ref
    if depth >= _NEST_DEPTH:
        return None
    for inner in _inner_urls(url):
        ref = _recognise(inner, depth + 1)
        if ref is not None:
            # The ref names the file inside; its URL is the one posted.
            return dataclasses.replace(ref, url=url)
    return None


def recognise(url: str) -> ExternalFileRef | None:
    """The ref one URL names, or None. Pure: no network, never raises.

    A URL that is not itself a file link is looked INSIDE: a file link carried
    in its query or its tail (a redirect, a SafeLinks wrapper, `?next=`) is
    the ref, and the WHOLE outer URL is what the render replaces, since the
    record keeps the body verbatim and a wrapper left standing would carry the
    file id through to the model."""
    return _recognise(url.strip(), 0)


# --- Finding links in text --------------------------------------------------

# An ASCII URL run. Two alternatives, because they need different starts:
#
# * WITH a scheme it may start anywhere, glued to whatever came before
#   («link:https://…», «see...https://…», «@https://…», «2https://…»): the
#   scheme is where it starts, and only an `http` can begin one, so the scan
#   stays linear.
# * WITHOUT one it may not start inside a host-like run (`[A-Za-z0-9.-]`), or
#   `evil-docs.google.com` would match from `docs`. ASCII on purpose: `\w`
#   would match a CJK character, and in a Cantonese room a link is often
#   written straight after one («睇下docs.google.com/…»).
#
# The run stops at whitespace, at anything outside the URL alphabet (so a CJK
# character right after the link ends it), and at `<>()[]"'*~` and backtick,
# so Discord's `<url>`, Markdown's `(url)`, `**url**`, `~~url~~` and «url's»
# keep their punctuation outside the link.
_URL_RUN = re.compile(
    r"(?:https?://|(?<![A-Za-z0-9.\-]))"
    r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::[0-9]+)?/"
    r"[A-Za-z0-9\-._:/?#@!$&+,;=%]*",
    re.IGNORECASE,
)
#: Trailing characters that end a sentence or a chat format, not a link. `_`
#: is NOT here: a Drive id is base64url and may end in one, so a trailing `_`
#: is stripped only when the link also opened with one (`_url_` emphasis).
_TRAILING = ".,;:!?+="
#: Where a SECOND link glued straight onto the first starts: an embedded
#: scheme NOT preceded by a character that makes it a parameter or a path of
#: the first (`?next=https://…`, `/https://…`, `%2Fhttps…`).
_GLUED = re.compile(r"(?<![=/?&#%:(])https?://", re.IGNORECASE)


def _trim(text: str, start: int, end: int) -> int:
    """`end` with sentence and chat punctuation dropped from the link's tail."""
    while end > start:
        last = text[end - 1]
        if last in _TRAILING or (last == "_" and start > 0 and text[start - 1] == "_"):
            end -= 1
        else:
            break
    return end


def _links(text: str) -> Iterable[tuple[int, int, ExternalFileRef]]:
    for match in _URL_RUN.finditer(text):
        start, end = match.span()
        # Two links glued together (`<url>https://<url>`) are two links.
        cuts = [start] + [
            start + g.start() for g in _GLUED.finditer(text[start:end], 1)
        ] + [end]
        for a, b in itertools.pairwise(cuts):
            b = _trim(text, a, b)
            ref = recognise(text[a:b])
            if ref is not None:
                yield a, b, ref


def file_refs_in(text: str | None) -> tuple[ExternalFileRef, ...]:
    """Every file link in `text`, in order, one per file (the first posting
    of a file wins; a later one with a resource key fills it in)."""
    if not text:
        return ()
    seen: dict[tuple[str, str], ExternalFileRef] = {}
    for _start, _end, ref in _links(text):
        key = (ref.provider, ref.file_id)
        old = seen.get(key)
        if old is None:
            seen[key] = ref
        elif old.resource_key is None and ref.resource_key is not None:
            seen[key] = dataclasses.replace(old, resource_key=ref.resource_key)
    return tuple(seen.values())


def render_file_links(text: str) -> str:
    """`text` with every recognised file link replaced, in place, by its
    handle: what a MODEL reads. Everything else is left byte for byte. The
    one function every model-facing renderer calls (R-18)."""
    if not text or "google." not in text.lower():
        return text
    out: list[str] = []
    last = 0
    for start, end, ref in _links(text):
        out.append(text[last:start])
        out.append(render_handle(ref))
        last = end
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)


# --- The stored form --------------------------------------------------------

def external_files_json(refs: tuple[ExternalFileRef, ...]) -> str | None:
    """The `messages.external_files_json` text, or None for no refs. Here, not
    in the SQL store, for `nora.domain.media.media_json`'s reason: the fake
    record store must write the identical string."""
    if not refs:
        return None
    return json.dumps([
        {
            "provider": ref.provider, "file_id": ref.file_id, "url": ref.url,
            "label": ref.label,
            **({"resource_key": ref.resource_key} if ref.resource_key else {}),
        }
        for ref in refs
    ])


def refs_from_external_files_json(text: str | None) -> tuple[ExternalFileRef, ...]:
    """`external_files_json`'s inverse, total: NULL or malformed is `()`."""
    if not text:
        return ()
    try:
        items = json.loads(text)
    except (TypeError, ValueError):
        return ()
    if not isinstance(items, list):
        return ()
    out = []
    for item in items:
        if not (
            isinstance(item, dict)
            and isinstance(item.get("provider"), str)
            and isinstance(item.get("file_id"), str)
        ):
            return ()
        key = item.get("resource_key")
        out.append(ExternalFileRef(
            provider=item["provider"], file_id=item["file_id"],
            url=str(item.get("url") or ""),
            resource_key=key if isinstance(key, str) and key else None,
            label=str(item.get("label") or "link"),
        ))
    return tuple(out)


# --- Resolving a code -------------------------------------------------------

FileCodeStatus = Literal["ok", "nothing_found", "ambiguous", "unavailable"]


@dataclass(frozen=True)
class FileCodeLookup:
    """What a code names among ONE room's recorded file links. `ok` is one
    file, `ambiguous` more than one (refuse with the candidates, never pick),
    `nothing_found` none, and `unavailable` the record could not be read
    («I couldn't check», R-27). `truncated` says the row cap cut the read
    short, so a `nothing_found` then means «not that far back»."""

    status: FileCodeStatus
    refs: tuple[ExternalFileRef, ...] = ()
    truncated: bool = False


async def resolve_file_code(
    store: RecordStore, *, code: str, conversation_id: str, now_ms: int,
    days: int = ROOM_MEDIA_DAYS,
) -> FileCodeLookup:
    """The files whose code is `code` among this room's own message rows from
    the last `days`, newest posting first. Mirrors the media resolver
    (`room_sightings` + `code_matches`): the conversation is the caller's
    own, never a model argument, so a code from another room resolves to
    nothing here. Never raises."""
    hex_code = handle_hex(code)
    if len(hex_code) < HANDLE_CHARS:
        return FileCodeLookup("nothing_found")
    days = max(1, min(days, ROOM_MEDIA_DAYS))
    try:
        rows = await store.external_file_rows(
            conversation_id=conversation_id,
            since_ts=now_ms - days * 86_400_000, limit=ROOM_MEDIA_ROWS + 1,
        )
    except Exception:  # noqa: BLE001 — an unreadable record is `unavailable` (R-27)
        return FileCodeLookup("unavailable")
    truncated = len(rows) > ROOM_MEDIA_ROWS
    found: dict[tuple[str, str], ExternalFileRef] = {}
    for row in sorted(rows[:ROOM_MEDIA_ROWS], key=lambda r: (r.ts, r.id), reverse=True):
        for ref in refs_from_external_files_json(row.external_files_json):
            if ref.digest.startswith(hex_code):
                found.setdefault((ref.provider, ref.file_id), ref)
    refs = tuple(found.values())
    status: FileCodeStatus = (
        "nothing_found" if not refs else "ok" if len(refs) == 1 else "ambiguous"
    )
    return FileCodeLookup(status, refs, truncated)
