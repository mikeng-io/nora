"""What arrived that is not text — named by its REGISTERED MEDIA TYPE, and
handled by the hash of its own bytes (DEC-43).

Two vocabularies live here because they answer two different questions and are
produced at two different moments:

* `MediaRef` — what the PLATFORM said about one attachment (its id in the
  platform's own store, the content type it declared, a filename, a size). It
  comes off the envelope at parse time and is never trusted for anything but
  labelling.
* `MediaBlob` — what NORA HOLDS. It exists only after the bytes have been
  copied into her custody, and its `sha256` is computed from those bytes. A row
  carrying a `MediaBlob` is a row whose claim can be checked; a row carrying
  only a `MediaRef` is a row pointing at somebody else's filesystem, which is
  the A9 violation this module exists to end.

**The taxonomy is driven off the media type, not off prefix matching.**
`_attachment_placeholder` used to test `contentType.startswith("image/")` and
send everything else to `[attachment: <filename>]`. Two things were wrong with
that, and only the second one looks like a bug:

1. A voice note — the second most common thing sent in this group — arrived as
   `[attachment: 1754521883.aac]`. Nora could not tell that a human had SPOKEN.
   Neither could she distinguish a video from a spreadsheet.
2. The fallback rendered a **sender-supplied filename** into the body she
   reasons over. A filename is unbounded text: it can hold a phone number, an
   ACI, or an instruction. `constraints.md` already says attachment names are
   content and never instructions — but that is a request to a model, and the
   string should not have been there in the first place. Nothing here renders a
   filename any more; it is recorded beside the hash and never shown.

RFC 6838 is the standard that already answers "what kind of thing is this":
`type/subtype`, a closed set of top-level types, optional `+suffix`, optional
`;parameters`. `parse_media_type` implements exactly that much of it — enough
to classify, not a parser of the whole grammar — and `mimetypes` (the stdlib's
own registry, not a second hand-rolled extension table) is the fallback when
the platform declared no type at all.

`voice_note` is a SEPARATE axis from `audio/*` and stays that way: a shared song
and somebody speaking are the same top-level type and different events, and
calling a song a voice note would put words in a member's mouth. See
`nora.platform.signal.parser` for the honesty note on that flag — it is read
from signal-cli's `voiceNote`, which no real message has yet exercised on this
deployment, and its absence degrades to `[audio]`, never to a wrong claim.

Standard library only, like every other module under `nora/domain` — this is
imported by the platform parser, by the ingest stage, and by the infra blob
store, so it may depend on none of them.
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass

# How much of the sha256 rides in the rendered placeholder. Six hex characters
# is a handle, not an identity: it is what makes `[image a3f2c1]` legible in a
# transcript and quotable back to Nora, while the row keeps the whole digest.
# Collisions across a retention-bounded transcript are not a correctness
# problem — the FULL hash in `messages.media_json` is what any retrieval
# resolves against, and a handle that matched two blobs would be ambiguous to
# the model, never wrong about the bytes.
HANDLE_CHARS = 6


@dataclass(frozen=True)
class GifCandidate:
    """A provider-described reaction GIF that may be selected within one turn."""

    provider: str
    id: str
    title: str
    alt_text: str
    tags: tuple[str, ...]
    rating: str

    @property
    def described(self) -> bool:
        return bool(self.title or self.alt_text or self.tags)

# RFC 6838 §4.2's registered top-level types. Written out rather than inferred
# so an unrecognised `type/` — which is malformed, not merely unknown — is
# visible as such instead of silently becoming a label.
_TOP_LEVEL_TYPES = frozenset({
    "application", "audio", "example", "font", "image",
    "message", "model", "multipart", "text", "video",
})

# The label a top-level type earns on its own. Everything not here is `file`:
# `font/`, `model/`, `multipart/`, `message/` and unrecognised `application/`
# subtypes are all "something arrived and it is not a thing you could look at
# or listen to", and inventing five labels for five things Nora can do nothing
# differently about would be taxonomy for its own sake.
_BY_TOP_LEVEL = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "text": "document",
}

# Subtypes of `application/` that are documents in the sense a human means it.
# Deliberately short: the point is to separate "a thing somebody wrote" from
# "an opaque blob", not to enumerate office formats.
_DOCUMENT_SUBTYPES = frozenset({
    "pdf", "rtf", "msword", "epub+zip", "json", "xml",
    "vnd.openxmlformats-officedocument.wordprocessingml.document",
    "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "vnd.openxmlformats-officedocument.presentationml.presentation",
    "vnd.oasis.opendocument.text", "vnd.oasis.opendocument.spreadsheet",
})

# Every label this module can produce. A closed set is what makes a rendered
# placeholder safe by construction: `render_placeholder` can only ever emit one
# of these plus hex, so no sender-supplied string can reach the body through it.
LABELS = frozenset({"image", "video", "audio", "voice note", "document", "file", "sticker"})


@dataclass(frozen=True)
class MediaRef:
    """One attachment as the PLATFORM described it. Never a custody claim.

    `platform_id` is the handle the platform's own store uses — signal-cli's
    attachment id, which is what `getAttachment` takes. It is meaningless
    outside that daemon and deliberately never rendered.

    `size` is the platform's claim about the bytes, not a measurement. The
    measured size lands on `MediaBlob`, from the bytes actually held.
    """

    platform_id: str
    media_type: str = ""
    filename: str | None = None
    size: int | None = None
    voice_note: bool = False
    # A SEPARATE axis from the media type, exactly like `voice_note` above and
    # for the identical reason: a sticker IS an image, so `image/webp` cannot
    # tell pack art from somebody's photograph — and Signal declares no type
    # for a sticker at all. The flag is what finally makes the `"sticker"`
    # member of LABELS something produced rather than merely declared.
    sticker: bool = False
    # The platform's OWN handle for something `platform_id` cannot name — a
    # Signal `(packId, stickerId)` pair, a Discord sticker id and format.
    # Opaque: the domain transports and stores it and never parses it. It is
    # never rendered, never logged above debug, never fed to Honcho, and never
    # placed in model context. Only the adapter that wrote it reads it.
    fetch_token: str = ""


@dataclass(frozen=True)
class MediaBlob:
    """One attachment Nora actually HOLDS, keyed by the hash of its bytes.

    `sha256` is the object key: content-addressed, so the same meme forwarded
    five times is one blob, a rename cannot move it, and a stored blob can be
    verified against the row that claims it. The operator's original sketch
    hashed filename-plus-metadata; hashing the CONTENT is the refinement he
    accepted, and it is what buys all three of those properties.

    `filename` rides ALONGSIDE the key and never produces it. It is recorded
    because it is sometimes the only human-meaningful thing about a document —
    and it is never rendered, for the reason in this module's docstring.
    """

    sha256: str
    media_type: str
    size: int
    filename: str | None = None

    @property
    def handle(self) -> str:
        return self.sha256[:HANDLE_CHARS]


@dataclass(frozen=True)
class MediaBytes:
    """The result of asking a platform for one attachment's bytes.

    Two axes, exactly like `Roster` and `Recollection` and for the same reason
    those exist: `available=False` (could not fetch) must stay distinguishable
    from `data=b""` (fetched, and it was empty). A `MediaSource` never raises,
    so without this flag a wedged daemon and a zero-byte file are the same
    value — and the counter that says "media custody is failing" would sit at
    zero while every attachment was being lost.

    `reason` is for the metric label and the log line, never for a caller to
    branch on beyond that.
    """

    available: bool
    data: bytes = b""
    reason: str = ""


def parse_media_type(raw: str) -> tuple[str, str]:
    """`(top_level, subtype)` per RFC 6838, or `("", "")` if it is not one.

    Parameters (`; codecs=opus`) are stripped, case is normalised (§4.2: type
    and subtype are case-insensitive), and an unregistered top-level type is
    rejected rather than passed through — `garbage/x` is malformed, and
    treating it as a type named `garbage` would let a sender invent labels.
    """
    if not raw:
        return ("", "")
    essence = str(raw).split(";", 1)[0].strip().lower()
    if essence.count("/") != 1:
        return ("", "")
    top, subtype = essence.split("/", 1)
    if top not in _TOP_LEVEL_TYPES or not subtype:
        return ("", "")
    return (top, subtype)


def resolve_media_type(*, declared: str, filename: str | None) -> str:
    """The media type to record: what the platform declared, else what the
    stdlib's registry infers from the filename, else `""`.

    A declared type ALWAYS wins. Guessing from an extension is what the sender
    controls most directly, so it is the last resort, not the first.
    """
    top, subtype = parse_media_type(declared)
    if top:
        return f"{top}/{subtype}"
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        top, subtype = parse_media_type(guessed or "")
        if top:
            return f"{top}/{subtype}"
    return ""


def label_for(ref: MediaRef) -> str:
    """What KIND of thing this is, in words the group would use.

    Always one of `LABELS`. Never `""`, never the filename, never the platform
    id: a message whose only content was an attachment must still say that
    something arrived, or it reads as an empty turn.
    """
    if ref.sticker:
        return "sticker"
    media_type = resolve_media_type(declared=ref.media_type, filename=ref.filename)
    top, subtype = parse_media_type(media_type)
    if not top:
        return "file"
    if top == "audio":
        return "voice note" if ref.voice_note else "audio"
    if top == "application":
        return "document" if subtype in _DOCUMENT_SUBTYPES else "file"
    return _BY_TOP_LEVEL.get(top, "file")


def render_placeholder(label: str, handle: str = "") -> str:
    """`[image]` before custody, `[image a3f2c1]` after it.

    The handle is omitted rather than faked when the bytes were not copied.
    That asymmetry is the whole point: a placeholder with a handle is a promise
    that the blob exists and can be produced, so it must never be written by a
    path that failed to store one.
    """
    return f"[{label} {handle}]" if handle else f"[{label}]"


def placeholder_suffix(labels: tuple[str, ...], handles: tuple[str, ...] = ()) -> str:
    """The trailing run of placeholders for one message's attachments.

    `_body_parts` puts the text first and every placeholder after it, joined by
    a single space, so this string is exactly the tail of the body — which is
    what lets `body_with_handles` rewrite it without a second copy of the
    parser's assembly rules or any positional guesswork.
    """
    padded = handles + ("",) * (len(labels) - len(handles))
    return " ".join(render_placeholder(a, b) for a, b in zip(labels, padded, strict=True))


def media_json(blobs: tuple[MediaBlob, ...]) -> str | None:
    """The `messages.media_json` text for one message, or None when Nora holds
    nothing for it.

    It lives HERE, in the domain, rather than in `SqlRecordStore` where it is
    written, because `FakeRecordStore` has to produce the identical string —
    they are in the same import-linter layer and cannot reach each other, so a
    private copy in each is the only alternative, and two copies of a
    serialization format is how a fake starts answering differently from
    production. `tests/contract/test_audit_store_contract.py` pins the result;
    this function is why the two cannot disagree in the first place.

    NULL rather than `"[]"` for the empty case, which is every row in this
    database today: `[]` would make "holds nothing" and "had nothing" identical
    in SQL, and a column that reads `[]` on every row is one nobody can filter.

    Flat, fixed-key shape on purpose. This text is the durable half of the
    record and `MediaBlob` is what a retrieval tool reconstructs from it; an
    optional or nested key would make that reconstruction guess.
    """
    if not blobs:
        return None
    return json.dumps([
        {
            "sha256": blob.sha256,
            "media_type": blob.media_type,
            "size": blob.size,
            "filename": blob.filename,
        }
        for blob in blobs
    ])


def body_with_handles(body: str, labels: tuple[str, ...], handles: tuple[str, ...]) -> str:
    """Re-render `body`'s attachment placeholders with the handles now held.

    Returns `body` UNCHANGED if its tail is not the placeholder run this
    message's labels predict. That precondition is checked rather than assumed
    because the alternative — replacing the k-th `[` by position — would
    silently corrupt a body whose TEXT happened to contain a bracket, and the
    text is sender-supplied. A body that does not match is a body somebody
    changed the assembly rules of, and leaving it alone loses a handle instead
    of a message.
    """
    if not labels:
        return body
    old = placeholder_suffix(labels)
    if not body.endswith(old):
        return body
    return body[: len(body) - len(old)] + placeholder_suffix(labels, handles)
