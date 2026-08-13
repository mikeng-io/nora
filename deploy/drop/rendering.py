"""Content-Disposition / inline-vs-attachment policy for `GET /d/<slug>`
(Nora Drop spec §3/§6 — task 4's "real artifact rendering"). Kept as its own
module, not inlined in `server.py`, for the same one-stage-one-module reason
`slugs.py`/`auth.py` already are: this is one decision (how a browser should
handle a stored `media_type`), with its own unit tests, that
`server.py::render_artifact` merely calls.

**Inline vs. attachment.** A browser already knows how to *display* images,
PDF, text, audio, and video without any help from this app — those get
`Content-Disposition: inline` so opening the link shows the artifact instead
of triggering a download. Everything else (zip, Word, octet-stream, and any
`media_type` this app has never seen before) gets `attachment`: a browser has
no native, safe way to render an arbitrary MIME type, and a download prompt
is the unsurprising default. This is also the conservative read of the risk
§7a already names — `drop` never inspects what a Compute job's bytes
actually contain, so defaulting unknown types to a download rather than an
inline render is the fail-closed choice, not a guess either way.

**Filename.** RFC 6266 governs `filename=`; a `title` containing a raw
double quote, backslash, or control character could break the header (or,
worse, be read as a second parameter) if interpolated unescaped — this
strips exactly those before quoting, never trusts `title` verbatim.
`mimetypes.guess_extension` fills in an extension the stored `title` doesn't
already have, so an artifact titled "My Report" with `media_type
application/pdf` downloads as "My Report.pdf" rather than an extensionless
file the browser/OS can't hand to an app.

Also restricted to ASCII: Starlette's `Response` encodes header VALUES as
latin-1 (`starlette.responses.Response.init_headers`), so any title outside
that range — CJK text, an emoji, anything beyond Latin-1 accents — would
raise `UnicodeEncodeError` on every future render of that artifact, not at
registration time. Registration would still succeed (200), so the artifact
looks fine when minted and only breaks the first time anyone opens its link
— an availability bug, not a security one, but a real one, found in review.
Folding to ASCII here (rather than RFC 6266's `filename*=UTF-8''...` form)
keeps this simple; a non-ASCII title still produces a usable, if blander,
filename instead of a permanently broken URL.
"""

from __future__ import annotations

import mimetypes

_INLINE_PREFIXES = ("image/", "text/", "audio/", "video/")
_INLINE_EXACT = frozenset({"application/pdf"})

# Characters stripped from `title` before it becomes a `filename=` value —
# a raw quote or backslash could break out of the quoted-string the header
# value is wrapped in; CR/LF/TAB are outright header-injection vectors.
_UNSAFE_FILENAME_CHARS = frozenset('"\\/\r\n\t')

_MAX_FILENAME_LEN = 200


def is_inline(media_type: str) -> bool:
    """Whether a browser should be trusted to display `media_type` itself
    rather than being told to download it."""
    base = _base_type(media_type)
    return base in _INLINE_EXACT or base.startswith(_INLINE_PREFIXES)


def content_disposition(media_type: str, title: str) -> str:
    """The `Content-Disposition` header value for an artifact: `inline` for
    a `media_type` a browser natively renders, `attachment` (download
    prompt) for everything else — either way with a sanitised,
    extension-complete filename derived from the artifact's own `title`."""
    disposition = "inline" if is_inline(media_type) else "attachment"
    return f'{disposition}; filename="{_safe_filename(media_type, title)}"'


def _base_type(media_type: str) -> str:
    """`media_type` with any `; charset=...`-style parameter dropped and
    normalised to lowercase — `is_inline`/`_safe_filename` only ever need
    the bare MIME type."""
    return media_type.split(";", 1)[0].strip().lower()


def _safe_filename(media_type: str, title: str) -> str:
    cleaned = "".join(
        ch
        for ch in title
        if ch not in _UNSAFE_FILENAME_CHARS and ch.isascii() and ch.isprintable()
    )
    cleaned = cleaned.strip() or "artifact"
    ext = mimetypes.guess_extension(_base_type(media_type))
    if ext and not cleaned.lower().endswith(ext.lower()):
        # Truncate the base name BEFORE appending the extension, not after —
        # truncating a "base + ext" string can slice off part of the
        # extension, leaving e.g. `filename="report.p"` for a would-be
        # `.pdf`. Reserve room for `ext` up front instead.
        cleaned = cleaned[: _MAX_FILENAME_LEN - len(ext)] + ext
    return cleaned[:_MAX_FILENAME_LEN]


__all__ = ["content_disposition", "is_inline"]
