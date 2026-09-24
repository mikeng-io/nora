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
`nora.platform.signal.parser` for the deployed signal-cli's own gap here — its
JSON-RPC `voiceNote` field is not yet emitted (confirmed 2026-08-23; the fix
landed upstream but is unreleased) — and the filename heuristic that stands
in for it. Either way, absence degrades to `[audio]`, never to a wrong claim.

Standard library only, like every other module under `nora/domain` — this is
imported by the platform parser, by the ingest stage, and by the infra blob
store, so it may depend on none of them.
"""

from __future__ import annotations

import json
import mimetypes
import re
from collections.abc import Mapping
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
# placeholder's LABEL and HANDLE safe by construction: those two can only ever
# be one of these plus hex, so no sender-supplied string reaches the body
# through them. The optional DESCRIPTION is NOT covered by that guarantee — it
# is free text from a vision model, made safe by explicit screening in
# `sanitize_description`, not by a closed set.
#
# `cover image` is the one label that names a ROLE rather than a kind of
# bytes: the still picture a shared reel, post or story arrives with
# (`MediaRef.on_demand`). It exists because the bare `[image be4be6]` beside
# a `[shared reel …]` line read as THE REEL to a model, which then answered
# "what is in this reel" off one frame and never opened the link it was
# looking at (2026-09-24). The transcript has to say what the model is
# reading; a tool description telling it what it can do was already there
# and was not enough.
LABELS = frozenset({
    "image", "cover image", "video", "audio", "voice note", "document", "file",
    "sticker",
})

#: `MediaBlob.role` for the still picture a shared item arrived with. The only
#: role there is; an absent role is an ordinary attachment somebody sent.
COVER_ROLE = "cover"


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
    # READ ONLY WHEN ASKED. Custody is unchanged: the bytes are still copied
    # and the handle is still rendered. What this turns off is the ingest
    # comprehension pass (`nora/ingest/stages/media.py` schedules no subject
    # for it), so no model looks at the bytes until `media_fetch` is called on
    # the handle. Set by an adapter for an attachment that is a PREVIEW of
    # something posted elsewhere rather than something a member sent: an
    # Instagram reel, post or story shared into a room arrives with its
    # thumbnail. The operator's rule (2026-09-24) is that Nora reads shared
    # content only when tagged, never on every share. A property of the ref,
    # never of the bytes: the same bytes later sent as a direct photo are
    # read then, because the comprehension job keys on whether the ROW has
    # been read, not on whether the bytes were ever seen.
    on_demand: bool = False


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
    # WHAT THESE BYTES WERE TO THE MESSAGE, not what they are. `""` is an
    # attachment somebody sent; `COVER_ROLE` is the still picture a shared
    # reel, post or story arrived with (`MediaRef.on_demand`, set by the
    # adapter). It rides here because `MediaRef` is gone by the time anything
    # asks: `media_json` holds blobs, and `media_fetch` has only that row to
    # tell a cover from a photo when it serves the handle. A property of this
    # SIGHTING, never of the bytes: the same bytes sent later as a photo are
    # a second blob entry with no role.
    role: str = ""

    @property
    def handle(self) -> str:
        return self.sha256[:HANDLE_CHARS]


@dataclass(frozen=True)
class MediaObject:
    """One row of Nora's unified media index (DEC-138, migration 0032).

    `sha256` is the content address of the bytes in the SAME `blob_store`
    (rustfs) every acquisition path uses — inbound photos, generated images
    and fetched attachments land in ONE table, distinguished by `source`
    (an attribute, not a table).

    **`title` / `description` / `content` are the three comprehension fields
    (issue #116, migration 0052), and they are ONE model pass, not three.**
    Reading an image or a video is the expensive part; extra output fields on
    the same call are near-free, so the pass produces all three at once:

      `title`       what the thing IS — a few words, the handle a human
                    would use for it.
      `description` what it SHOWS — a sentence or two.
      `content`     everything legible or audible IN it, in full: the text on
                    the whiteboard, the words in a voice note. Never
                    truncated; `media_fetch` is what serves it on demand.

    Each is a pure function of the bytes, which is what makes the sha256 a
    valid forever-key for all three (`VisionCache`'s argument, now applying
    to the whole triple). ALL THREE EMPTY is an INDEX ENTRY — the row says
    Nora holds these bytes and claims nothing about them, which is exactly
    the state every inbound object is in between custody and comprehension.

    A query-conditioned answer (`media_fetch`'s layer 2) is a function of
    (bytes, question) and therefore belongs in NONE of these columns — see
    `nora/agent/tools.py`'s media_fetch.

    `provenance` is source-dependent JSONB — the birth record:
      generated: prompt, title, description, provider, model, aspect_ratio,
                 platform, conversation_id
    `describe_model` records which describer wrote the comprehension (NULL
    when seeded from the prompt). `platform`/`conversation_id`/`created_at_ms`
    record where the object was born — the audit trail.
    """

    sha256: str
    media_type: str
    size: int
    source: str
    description: str
    provenance: dict[str, object] | None = None
    describe_model: str | None = None
    created_at_ms: int = 0
    # Defaulted, so every existing construction site — the ingest index write,
    # `generate_image`, four contract fixtures — stays valid and produces the
    # honest "not comprehended yet" row.
    title: str = ""
    content: str = ""

    @property
    def handle(self) -> str:
        return self.sha256[:HANDLE_CHARS]

    @property
    def caveat(self) -> str:
        """The sentence this READ has to carry about what it did NOT see.

        Stored in provenance rather than glued onto `description`, and that is
        a correctness fix rather than tidiness. Appended to the description it
        was indistinguishable from it at render time, so the transcript's
        `comprehension_render_chars` bound clipped it off — and it clipped it
        precisely when the description was RICH, which is the case where a
        confident-sounding partial read does the most damage. «This clip runs
        about 20 minutes and I only watched the first 4» became «(This clip
        runs a».

        Kept apart, every renderer appends it LAST and after its own clip, so
        no bound can eat it: the transcript join, `media_fetch`'s stored
        reading, and the Honcho feed all carry it whole.
        """
        return str((self.provenance or {}).get("media_caveat", ""))

    @property
    def comprehended(self) -> bool:
        """Whether anything has actually read these bytes.

        The three columns default to `''` on every row custody writes, so
        «indexed» and «understood» are the same shape in SQL and must not be
        the same answer in Python: a comprehension job that treated an index
        entry as done would never read a single photo, and `media_fetch`
        serving one would answer `ok` with nothing in it.
        """
        return bool(self.title or self.description or self.content)

    # Generated-media provenance accessors (NULL-equivalent when the object
    # is not generated): the generated-image library shape DEC-137 built.
    @property
    def prompt(self) -> str:
        return str((self.provenance or {}).get("prompt", ""))

    @property
    def provider(self) -> str:
        return str((self.provenance or {}).get("provider", ""))

    @property
    def model(self) -> str:
        return str((self.provenance or {}).get("model", ""))

    @property
    def aspect_ratio(self) -> str | None:
        v = (self.provenance or {}).get("aspect_ratio")
        return None if v is None else str(v)

    @property
    def platform(self) -> str:
        return str((self.provenance or {}).get("platform", ""))

    @property
    def conversation_id(self) -> str:
        return str((self.provenance or {}).get("conversation_id", ""))


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


def sniff_media_type(data: bytes) -> str:
    """The container type read off the leading bytes, or `""` if none of the
    known signatures match.

    Stickers are the reason this exists: a platform declares a type when it
    has one — Discord's sticker format maps straight to `image/png` /
    `image/apng` / `image/gif` — and Signal never does at all (spec §1:
    `dataMessage.sticker` carries only `packId`/`stickerId`, no `contentType`).
    `resolve_media_type` can only resolve what was DECLARED or guessable from
    a FILENAME, and a Signal sticker has neither, so the type has to come from
    the bytes actually fetched, or the description never reaches the vision
    model at all — the comprehension pass (correctly) refuses a media type
    outside image/video/audio before any model call, and an unresolved sticker
    resolves to `""`.

    Instagram is the second caller and widened it: that wire carries no
    mimetype at all, so `ground_media_type` reads the subtype off the bytes
    for a type declared `image/*` — which needs the one signature the sticker
    set never had a use for, JPEG, since no sticker is one and most photos
    are.

    Deliberately a sniff of a closed, small set of magic numbers, not a
    general-purpose format detector: `RIFF`...`WEBP` (animated or static
    WebP), the PNG signature, the GIF89a/GIF87a `GIF8` prefix — all three
    observed on real Signal stickers (spec §1's evidence table) — plus the
    JPEG SOI marker and the ISO base media `ftyp` box. The first three are
    measured; the last two are format definitions rather than observations on
    this deployment, which is why `ground_media_type` discards a sniff that
    contradicts the declared top-level type instead of trusting it. An
    unrecognised signature is never guessed at — the caller falls back to
    whatever was already resolved from the declared type.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[4:8] == b"ftyp":
        # ISO base media format (ISO/IEC 14496-12): the `ftyp` box names the
        # brand, and the brand is the only thing separating an .m4a from an
        # .mp4 — the container is identical. `M4A`/`M4B` are the registered
        # audio brands; everything else here (`isom`, `mp42`, `avc1`, `qt  `)
        # is a video container. That distinction is from the brand registry,
        # NOT measured on this deployment, which is exactly why
        # `ground_media_type` refuses a sniff that disagrees with the
        # top-level type the platform declared.
        return "audio/mp4" if data[8:11] in (b"M4A", b"M4B") else "video/mp4"
    return ""


def ground_media_type(*, declared: str, data: bytes) -> str:
    """A declared WILDCARD subtype replaced by what the BYTES say it is.

    `image/*` is a real answer and a half of one: Instagram's wire carries no
    mimetype anywhere, so its parser records the top level it observed and
    refuses to invent the subtype (`nora.platform.instagram.parser`). That
    half survives everything that classifies — `parse_media_type` reads it,
    `label_for` labels it `image`, the handle is unaffected — and then breaks
    at the one place a type is not classified but USED: the comprehension pass
    builds a `data:` URI out of it, and `data:image/*;base64,…` is not a media
    type any decoder can act on. For `audio/*` it fails harder and more
    honestly, with no `input_audio` format for subtype `*` at all.

    So the wildcard is ground against the fetched bytes at custody time, where
    the bytes are in hand for the first time. **Fail-closed, two ways**: a
    sniff that recognises nothing leaves the declared wildcard exactly as it
    was (the observed half is still true, and inventing `jpeg` because most
    images are would be a guess the record would then carry), and a sniff
    whose TOP-LEVEL disagrees with the declaration is discarded rather than
    allowed to overrule the platform — an `audio/*` delta whose bytes look
    like a video container stays `audio/*`.

    A declared type with a real subtype is returned untouched, so this is
    inert for Signal, Discord and WhatsApp, which all declare one.
    """
    top, subtype = parse_media_type(declared)
    if subtype != "*":
        return declared
    sniffed_top, sniffed_subtype = parse_media_type(sniff_media_type(data))
    if not sniffed_top or sniffed_top != top:
        return declared
    return f"{sniffed_top}/{sniffed_subtype}"


def label_from_media_type(media_type: str) -> str:
    """The label a RECORDED media type earns on its own.

    `label_for` answers the same question from a `MediaRef`, which carries the
    platform's `voice_note`/`sticker` flags; this answers it from what the
    custody stage actually stored, which does not. So it can never return
    `voice note` or `sticker` — those two are separate axes, and inventing
    either from `audio/*` or `image/*` would put a claim in the record's mouth.

    It exists for the render join, which must not take a label from a body:
    `body` is sender-supplied and `voice note` is the one label whose detail
    renders UNCLIPPED, so a typed `[voice note a3f2c1]` was a way for any member
    to switch off the only bound on per-attachment prompt spend.
    """
    top, subtype = parse_media_type(media_type)
    if not top:
        return "file"
    if top == "audio":
        return "audio"
    if top == "application":
        return "document" if subtype in _DOCUMENT_SUBTYPES else "file"
    return _BY_TOP_LEVEL.get(top, "file")


def label_agrees(label: str, media_type: str) -> bool:
    """Whether a label CLAIMED by a body is consistent with the recorded type.

    The join reads a placeholder's label off the body — the record does not
    store one, and `voice note` and `audio` are the same top-level type — so
    the body is the only place the distinction survives. That is fine for a
    label the custody stage wrote and dangerous for one a member typed, and the
    two are indistinguishable at render.

    So the body may REFINE but never contradict: `voice note` is accepted over
    `audio/*`, and `sticker` and `cover image` over `image/*`, because those
    are the axes the media type genuinely cannot express. Anything else must
    match what `label_from_media_type` derives, and a mismatch falls back to
    it.
    """
    if not label or label not in LABELS:
        return False
    derived = label_from_media_type(media_type)
    if label == derived:
        return True
    if label == "voice note":
        return derived == "audio"
    if label in ("sticker", "cover image"):
        return derived == "image"
    return False


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
    label = _BY_TOP_LEVEL.get(top, "file")
    # A shared item's still picture says so, because it is not the item: see
    # `LABELS`. Only for an image, since that is the one shape a preview has
    # ever arrived as; anything else keeps its plain label rather than gaining
    # a word nobody has measured.
    if ref.on_demand and label == "image":
        return "cover image"
    return label


# Characters a description may never contain, and why each one:
#   `]`  would close the placeholder early, leaving the rest reading as body
#        prose the room never wrote;
#   `[`  would open a second one, so a description could forge a placeholder;
#   the C0 control range and DEL would forge a transcript LINE (newline/CR) or
#        worse, an escape sequence (ESC) read by whatever terminal or log
#        later renders the transcript — a surface that never expected model
#        output to control it. `\t \n \r \v \f` are exempted from this table
#        because the whitespace collapse below already turns each of them
#        into a single space; deleting them here first would just make that
#        step redundant.
_FORBIDDEN = str.maketrans({
    "]": None,
    "[": None,
    **{chr(c): None for c in [*range(0x20), 0x7F] if chr(c) not in "\t\n\r\v\f"},
})


def sanitize_description(raw: str, *, max_chars: int = 0) -> str:
    """A model's sentence, made safe to render inside a placeholder.

    This module used to be safe BY CONSTRUCTION: `render_placeholder` could
    only emit a member of a closed label set plus hex, so no sender-supplied
    string could reach the body through it. A free-text description ends that
    property, and this function is what replaces it — explicit screening rather
    than a guarantee.

    **`max_chars` defaults to 0 = UNBOUNDED, and that is the change issue #116
    made.** The 200-character clip existed because the description was baked
    into `messages.body`, a durable string two consumers read verbatim, so
    every character of it was paid for on every prefill forever. It is not
    baked in any more: the comprehension lives in `media_objects` and each
    consumer selects the fields it wants at render time, so the bound belongs
    on what the TRANSCRIPT renders (title + description), not on what is
    STORED. Truncating the stored text would throw away exactly the half
    `media_fetch` exists to serve.

    It does NOT screen for identifiers. That is `nora.agent.egress
    .scan_identifiers`' job and it runs in the caller, at every render, for the
    reason `VisionCache`'s docstring gives: a screen applied at STORE time can
    be bypassed by a cache hit.
    """
    collapsed = " ".join(str(raw).split())
    cleaned = collapsed.translate(_FORBIDDEN).strip()
    return cleaned[:max_chars] if max_chars > 0 else cleaned


# The label whose comprehension is the WORDS IN THE ROOM rather than a
# description OF something (issue #116 decision 4). A voice note is
# conversation content: `nora/ingest/stages/media.py` has said so since the
# STT lane landed, and collapsing it to «title - description» would mean Nora
# stops hearing what people actually said and starts reading a summary of it.
# So this one label renders its `content` — the full transcript — and the
# title/description it also carries exist for Honcho and for search.
#
# It is a SET rather than a string comparison so a future label with the same
# property (a transcribed video note) is one entry, not a second branch.
CONTENT_LABELS = frozenset({"voice note"})


def media_detail(
    *, label: str, title: str, description: str, content: str = "",
) -> str:
    """What rides after the colon in `[image a3f2c1: …]` at RENDER time.

    `<title> - <description>` for everything Nora looked at, and the FULL
    `content` for a voice note (see `CONTENT_LABELS`). Either half may be
    missing — a comprehension that produced only a title renders only the
    title, never a dangling separator — and all-empty renders `""`, which
    `render_placeholder` turns back into the bare `[image a3f2c1]` an
    un-comprehended object has always been.

    **`content` is the FALLBACK for any label, not only a voice note's.** A
    read can legitimately produce content and nothing else — a video read with
    no `comprehension_text_model` (the shipped default) has a full timecoded
    window run and no synthesized headline, and a model can answer a still with
    a transcription and no caption. Without this fallback such a row is
    `comprehended` (so the job never retries it) and renders BARE forever: the
    read happened, cost what it cost, and reaches the prompt as nothing. The
    caller clips it, which is what keeps the fallback from spending a window's
    worth of tokens on one attachment.

    Sanitized by the CALLER, not here: this composes, and the screen
    (`sanitize_description` plus `scan_identifiers`) runs on the composed
    string at every render, never once at store time. The CAVEAT is appended by
    the caller too, and after the clip — see `MediaObject.caveat`.
    """
    if label in CONTENT_LABELS and content:
        return content
    headline = " - ".join(part for part in (title, description) if part)
    return headline or content


# `[<label> <handle>]` for any label this module can produce, with the handle
# captured. A CLOSED alternation over `LABELS` plus fixed-width hex, built from
# the frozenset rather than hand-written, so a new label cannot be added to the
# taxonomy and silently miss the transcript join. Longest-first, or `audio`
# would win the prefix of nothing and `voice note` would never match.
_PLACEHOLDER_RE = re.compile(
    r"\[(" + "|".join(re.escape(x) for x in sorted(LABELS, key=len, reverse=True))
    + r") ([0-9a-f]{" + str(HANDLE_CHARS) + r"})\]"
)


def placeholders_in(body: str) -> tuple[tuple[str, str], ...]:
    """`(label, handle)` for every placeholder in a stored body, in order.

    The reader's half of `placeholder_suffix`. It exists so the render join
    can take a row's LABEL from the body it is rendering rather than
    re-deriving it from the blob's media type: `voice note` and `audio` are the
    same top-level type and different events (this module's own docstring), and
    it is the label that decides whether a transcript renders somebody's words
    or a summary of them. Re-deriving would be a second opinion about a fact
    already written down.
    """
    return tuple(
        (m.group(1), m.group(2)) for m in _PLACEHOLDER_RE.finditer(body)
    )


def enrich_placeholders(body: str, details: Mapping[str, str]) -> str:
    """`body` with each `[image a3f2c1]` re-rendered as `[image a3f2c1: …]`.

    The RENDER-time half of issue #116: `messages.body` carries the handle and
    nothing else, and the comprehension is joined on in the assembler for the
    one prompt that is about to be built. A handle with no entry in `details`
    (nothing has read those bytes yet, or the join failed) is left exactly as
    stored — `[image a3f2c1]`, claiming nothing.

    Rewriting is by REGEX over the closed label set rather than by position,
    for `body_with_handles`' reason: the text around the placeholders is
    sender-supplied and may contain brackets, so a positional replacement would
    silently corrupt somebody's message. A match here is a label from `LABELS`
    followed by exactly `HANDLE_CHARS` hex — a shape a sender cannot forge into
    a DIFFERENT blob's handle, because the handle still has to resolve in
    `details`, which the caller builds from this conversation's own
    `media_json`.
    """
    if not details:
        return body

    def _sub(match: re.Match[str]) -> str:
        label, handle = match.group(1), match.group(2)
        detail = details.get(handle, "")
        return render_placeholder(label, handle, detail)

    return _PLACEHOLDER_RE.sub(_sub, body)


def render_placeholder(label: str, handle: str = "", description: str = "") -> str:
    """`[image]` before custody, `[image a3f2c1]` after it, and
    `[image a3f2c1: a plate of noodles - …]` once something has read the bytes.

    The description rides only WITH a handle, never alone. The handle is a
    promise that the blob exists and can be produced; a description without one
    would claim Nora read bytes she does not hold, which is the same lie the
    handle's own asymmetry already refuses to tell.

    **The third argument is a RENDER-time value only** (issue #116). It reaches
    this function from `enrich_placeholders`, called by the context assembler
    for one prompt; it never reaches `body_with_handles`, and so it never lands
    in `messages.body`. That is the whole of decision 2: one string cannot
    serve both «Honcho detailed» and «transcript short», so the durable string
    carries the handle and each consumer selects what it wants at render.
    """
    if not handle:
        return f"[{label}]"
    if not description:
        return f"[{label} {handle}]"
    return f"[{label} {handle}: {description}]"


def placeholder_suffix(
    labels: tuple[str, ...],
    handles: tuple[str, ...] = (),
) -> str:
    """The trailing run of placeholders for one message's attachments.

    `_body_parts` puts the text first and every placeholder after it, joined by
    a single space, so this string is exactly the tail of the body — which is
    what lets `body_with_handles` rewrite it without a second copy of the
    parser's assembly rules or any positional guesswork.

    **Takes no descriptions** — see `render_placeholder`. A body is a label and
    a handle, forever.
    """
    padded_handles = handles + ("",) * (len(labels) - len(handles))
    return " ".join(
        render_placeholder(a, b)
        for a, b in zip(labels, padded_handles, strict=True)
    )


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

    Flat shape on purpose. This text is the durable half of the record and
    `MediaBlob` is what a retrieval tool reconstructs from it; a nested key
    would make that reconstruction guess.

    **`role` is the one key written only when it is set** (2026-09-24). Every
    row written before it existed carries none, so a reader has to treat an
    absent `role` as an ordinary attachment whatever this function writes; an
    always-written `"role": ""` would add nothing to that and change the text
    of every row from now on. Absence has one meaning, so nothing guesses.
    """
    if not blobs:
        return None
    return json.dumps([
        {
            "sha256": blob.sha256,
            "media_type": blob.media_type,
            "size": blob.size,
            "filename": blob.filename,
            **({"role": blob.role} if blob.role else {}),
        }
        for blob in blobs
    ])


def blobs_from_media_json(text: str | None) -> tuple[MediaBlob, ...]:
    """`media_json`'s inverse, total: NULL or anything malformed is `()`."""
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
        if not isinstance(item, dict) or not isinstance(item.get("sha256"), str):
            return ()
        out.append(MediaBlob(
            sha256=item["sha256"], media_type=str(item.get("media_type") or ""),
            size=int(item.get("size") or 0), filename=item.get("filename"),
            role=str(item.get("role") or ""),
        ))
    return tuple(out)


def body_with_handles(
    body: str,
    labels: tuple[str, ...],
    handles: tuple[str, ...],
) -> str:
    """Re-render `body`'s attachment placeholders with the handles now held.

    **Handles only.** The `descriptions` argument this used to take is what
    issue #116 removed: a sticker description baked in here rode into
    `messages.body`, which `memory_feed` and the prefill both read VERBATIM, so
    "Honcho detailed, transcript short" was not tunable — it was structurally
    impossible while one string served both. The comprehension now lives in
    `media_objects` and is joined at render time.

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


@dataclass(frozen=True)
class VideoReadingKey:
    """Everything a question-less video reading is a function of.

    An image description is a pure function of its bytes, which is why the
    vision cache keys on the sha256 alone. A chunked VIDEO reading is not
    (issue #117): which windows were cut depends on `chunk_seconds` and
    `chunk_max`, and the coverage sentence that says what was missed depends on
    them too. Caching on the sha256 alone put #117's bug back through the cache,
    so video was excluded from it entirely. Every input goes in the key instead:

    * `sha256` — the bytes.
    * `provider`, `model` — the lane and describer that read it. BOTH: the
      same model name through OmniRoute answers «I cannot see any video» as a
      200 (it drops `video_url`), and that must never be served once the lane
      moves to a direct provider.
    * `chunk_seconds`, `chunk_max` — the geometry; raising `chunk_max` must
      miss, or a stale «I have not seen the rest» is served forever.
    * `reading_digest` — everything else the model's view depends on: the
      question it was asked, the declared media type, the reasoning effort and
      every encode setting (`preprocess.video_encode_signature`). Turning on
      speech (DEC-197), or changing the resolution or audio encode, misses.
    """

    sha256: str
    provider: str
    model: str
    chunk_seconds: int
    chunk_max: int
    reading_digest: str
