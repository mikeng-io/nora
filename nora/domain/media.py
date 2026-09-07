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

    Deliberately a sniff of a closed, small set of magic numbers observed on
    real Signal stickers (spec §1's evidence table), not a general-purpose
    format detector: `RIFF`...`WEBP` (animated or static WebP), the PNG
    signature, and the GIF89a/GIF87a `GIF8` prefix. An unrecognised signature
    is never guessed at — the caller falls back to whatever was already
    resolved from the declared type.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"GIF8":
        return "image/gif"
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
