"""signal-cli SSE envelope → NormalizedMessage.

Group messages only; DMs and syncMessages are dropped. Attachments become a
placeholder plus a `MediaRef` recording what the attachment IS; this module
downloads nothing. The bytes are fetched one stage later
(`nora.ingest.stages.media`), from the ref this produces.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ...domain.media import MediaRef, label_for, render_placeholder, resolve_media_type
from ...domain.models import (
    MENTION_PLACEHOLDER,
    EditTarget,
    NormalizedMessage,
    Quote,
    Touch,
)

log = logging.getLogger("nora.signal.parser")

# OBJECT REPLACEMENT CHARACTER — what Signal puts in the body where an
# @-mention's name appears. It is the ONLY thing the body carries about a
# mention; the identity lives in the parallel `mentions` array. Written as an
# escape, not as the literal character: ￼ renders as a box or as nothing at all
# depending on the font, so a reader cannot verify a literal one by eye.
PLACEHOLDER = MENTION_PLACEHOLDER


# signal-cli 0.14.7's JSON-RPC `JsonAttachment` does NOT carry a voice-note
# flag at all: the internal `MessageEnvelope.Data.Attachment` has `isVoiceNote` (it IS what
# produces "Flags: voice note" in signal-cli's own text log for a real
# voice note), but the record actually serialized to Nora over JSON-RPC is
# only `contentType;filename;id;size;width;height;caption;uploadTimestamp` —
# the flag is detected internally and then dropped before it ever reaches us.
# Upstream has fixed this but not in a tagged release, so
# `att.get("voiceNote")` below is read for forward compatibility and will not
# fire on that version.
#
# Until a release carries that field, fall back to the filename Signal's own
# app generates for an in-recorder voice note — e.g.
# `signal-YYYY-MM-DD-HH-MM-SS-mmm.m4a`.
# A shared audio FILE keeps its original filename, so this is a strong, not
# proof-certain, signal. Remove this fallback once `voiceNote` is confirmed
# present in the deployed signal-cli's JSON-RPC output.
#
# R-58: the filename match is gated on the resolved media type ALSO
# being `audio/*`, deliberately narrower than the flag it stands in for.
# `nora.ingest.stages.media` gates TRANSCRIPTION on `ref.voice_note` alone,
# not on MIME type (DEC-131) — that gate trusted `voiceNote` because only
# signal-cli itself could set it. A bare filename match would let a sender
# name any attachment `signal-<timestamp>.<unknown-ext>` and have its bytes
# posted to the local STT endpoint even though `label_for` still displays it
# as `[file]` — what's shown and what's processed would diverge. Requiring
# an actual audio type keeps the heuristic sender-shaped but not
# sender-arbitrary: a `.dat` or other non-audio extension never matches.
_SIGNAL_APP_VOICE_NOTE_FILENAME = re.compile(
    r"^signal-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-\d{3}\.\w+$"
)


def _is_voice_note(att: dict, *, filename: str | None, media_type: str) -> bool:
    if att.get("voiceNote"):
        return True
    if not filename or not _SIGNAL_APP_VOICE_NOTE_FILENAME.match(filename):
        return False
    resolved = resolve_media_type(declared=media_type, filename=filename)
    return resolved.startswith("audio/")


def _media_ref(att: dict) -> MediaRef:
    """One `JsonAttachment` reduced to what custody and labelling need.

    Field names are signal-cli's: `id` (the handle its own store uses, and what
    `getAttachment` takes), `contentType`, `filename`, `size`, `voiceNote`.

    `voiceNote` is read for forward compatibility but is not yet emitted by
    the deployed signal-cli version — see `_is_voice_note`'s comment above for
    the root cause, the filename fallback it uses instead, and why
    that fallback also requires an audio media type (R-58). Either
    way, the failure mode stays harmless: an `audio/*` attachment this
    heuristic misses renders `[audio]` instead of `[voice note]`, which is
    still the fix (she can tell somebody sent sound) and is never a wrong
    claim about who said what. `nora.domain.media.label_for` is where that
    fallback lives.
    """
    filename = str(att["filename"]) if att.get("filename") else None
    media_type = str(att.get("contentType") or "")
    return MediaRef(
        platform_id=str(att.get("id") or ""),
        media_type=media_type,
        filename=filename,
        size=(int(att["size"]) if isinstance(att.get("size"), int) else None),
        voice_note=_is_voice_note(att, filename=filename, media_type=media_type),
    )


def _attachments(data: dict) -> tuple[MediaRef, ...]:
    return tuple(
        _media_ref(a) for a in (data.get("attachments") or []) if isinstance(a, dict)
    )


def _mentioned_ids(data: dict) -> frozenset[str]:
    """Every id form Signal gives for each @-mention in this message.

    A Signal mention is not text. The body carries U+FFFC (OBJECT REPLACEMENT
    CHARACTER) where the name appears, and `dataMessage.mentions` carries
    `{name, number, uuid, start, length}` alongside it — so an @-mention is
    structurally invisible to a substring trigger match. Both the number and
    the uuid are kept, because which one is present depends on the same
    sealed-sender rules that govern `sourceUuid`.
    """
    out: set[str] = set()
    for m in data.get("mentions") or []:
        if not isinstance(m, dict):
            continue
        for key in ("uuid", "number", "name"):
            value = m.get(key)
            if isinstance(value, str) and value:
                out.add(value)
    return frozenset(out)


@dataclass(frozen=True)
class MentionSpan:
    """One @-mention, positioned in the body Nora actually stored.

    `start`/`length` on the wire index into `dataMessage.message`, but the body
    that reaches `messages.body` is `" ".join(_body_parts(data))` — whose first
    part is that string **stripped**. `mention_spans` therefore rebases `start`
    by the leading whitespace it removed, so these offsets are usable against a
    stored row with no further arithmetic and no second copy of `_body_parts`'
    rules living at the call site.

    `ids` is ordered `(uuid, number)` and **deliberately excludes `name`**,
    which `_mentioned_ids` does keep. That difference is the whole safety
    story of this type: `_mentioned_ids` answers "was Nora tagged", where a
    superset costs nothing, whereas this one feeds a RENDERED line — and on
    every real stored row `name` is the sender's E.164 phone number, not a
    display name at all. A
    renderer that reached for the first non-empty field would put a phone
    number in the prompt, which is exactly what R-18 exists to prevent.
    """

    start: int
    length: int
    ids: tuple[str, ...]


def _data_message(env: dict) -> dict | None:
    """The `dataMessage` of an envelope, in either of the two framings it has.

    An EDITED message is not a `dataMessage` — signal-cli nests it as
    `editMessage.dataMessage` alongside a `targetSentTimestamp`
    (JsonEditMessage). Reading only the top-level key would make every edit
    silently invisible: not ingested, not audited, indistinguishable from a
    typing notification.

    The edit is NOT a message of its own. `parse_event` marks it with
    `edit_of` (`_edit_target`), and ingest records it beside the message it
    edits, which the transcript read then shows with the latest text at its
    original position.

    Extracted from `parse_event` so `mention_spans` unwraps the SAME two
    framings: a mention has to be looked up in the dataMessage the body came
    from, or an edited message renders its placeholders against the wrong
    array.
    """
    data = env.get("dataMessage")
    if isinstance(data, dict):
        return data
    edit = env.get("editMessage")
    data = edit.get("dataMessage") if isinstance(edit, dict) else None
    return data if isinstance(data, dict) else None


def _edit_target(env: dict) -> EditTarget | None:
    """The message an `editMessage` envelope edits, or None when this
    envelope is not an edit. signal-cli names it by `targetSentTimestamp` —
    the ORIGINAL's sent-timestamp, which with the author is how Signal
    addresses any message (a quote, a reaction, an edit). A malformed or
    missing timestamp is None: an edit that names nothing cannot be folded,
    and ingesting it as an ordinary message is the honest fallback."""
    if isinstance(env.get("dataMessage"), dict):
        return None
    edit = env.get("editMessage")
    if not isinstance(edit, dict):
        return None
    ts = edit.get("targetSentTimestamp")
    if not isinstance(ts, int) or isinstance(ts, bool) or ts <= 0:
        return None
    return EditTarget(sent_ts=ts)


def mention_spans(event: dict) -> tuple[MentionSpan, ...]:
    """Where each @-mention sits in the stored body, and who it names.

    PUBLIC and taking a whole raw event, because that is the shape both callers
    hold: `NormalizedMessage.raw` on the tag path and `TranscriptRow.raw_json`
    (parsed) for a historical row. `raw_json` carries the full envelope for
    every row ever audited, so rehydrating a mention works retroactively —
    which is the only reason the fix does not need a backfill.

    Total by construction: an envelope of any other shape, a malformed
    `mentions` entry, or a body with no text part yields `()`. A caller must
    never have to ask whether this platform's convention applies.
    """
    try:
        env = extract_envelope(event or {})
        data = _data_message(env) if isinstance(env, dict) else None
        if data is None:
            return ()
        raw_text = data.get("message") or ""
        if not raw_text.strip():
            # No text part, so the stored body starts with an attachment
            # placeholder and these offsets index nothing that survives. A
            # mention always puts its placeholder in the text, so this is the
            # malformed case rather than a real one.
            return ()
        lead = len(raw_text) - len(raw_text.lstrip())
        spans = []
        for m in data.get("mentions") or []:
            if not isinstance(m, dict):
                continue
            try:
                start = int(m["start"]) - lead
                length = int(m["length"])
            except (KeyError, TypeError, ValueError):
                continue
            ids = tuple(
                value for key in ("uuid", "number")
                if isinstance(value := m.get(key), str) and value
            )
            spans.append(MentionSpan(start=start, length=length, ids=ids))
        return tuple(spans)
    except Exception:
        log.exception("unreadable mentions array, rendering none")
        return ()


def _quoted(data: dict) -> Quote | None:
    """The message this one replies to, if any — as a pointer.

    Signal carries a reply as `dataMessage.quote` and NOT in the body. Dropping
    it would leave Nora reading replies as non-sequiturs: Grant says "where for
    dinner", Miles replies "your place", and without the quote she has no way to
    know whose place, or that the two messages are connected at all.

    `quote.id` is the parent's platform sent-timestamp and is REQUIRED by
    signal-cli's own `quote.schema.json`. A quote without one carries no
    pointer at a parent, so there is nothing threading could ever resolve — it
    is dropped rather than stored as a half-quote.

    There is no `authorName` on the wire (see `Quote`); attribution is derived
    from the resolved parent row, never from this payload.
    """
    q = data.get("quote")
    if not isinstance(q, dict):
        return None
    try:
        quoted_ts = int(q["id"])
    except (KeyError, TypeError, ValueError):
        return None
    # The ID is what identity is decided on — same split as the sender itself,
    # because a display name is spoofable.
    author_id = q.get("author") or q.get("authorUuid") or q.get("authorNumber") or ""
    text = (q.get("text") or "").strip()
    return Quote(author_id=str(author_id), text=text, quoted_ts=quoted_ts)


def _sticker_ref(data: dict) -> MediaRef | None:
    """One `MediaRef` for `dataMessage.sticker`, or None if it is unusable.

    Both fields are required because `getSticker` needs both — a half-formed
    sticker object cannot be fetched, and a ref for it would manufacture a
    custody failure to count on every one. The token is this adapter's private
    encoding; `SignalMediaSource` is the only code that reads it back.

    No `media_type`: the envelope declares none, and the bytes are WebP or PNG
    depending on the pack. `label_for` returns "sticker" off the flag
    rather than off a type nobody sent.
    """
    sticker = data.get("sticker")
    if not isinstance(sticker, dict):
        return None
    pack_id, sticker_id = sticker.get("packId"), sticker.get("stickerId")
    if not isinstance(pack_id, str) or not pack_id:
        return None
    if not isinstance(sticker_id, int):
        return None
    return MediaRef(
        platform_id="", fetch_token=f"sticker:{pack_id}:{sticker_id}", sticker=True
    )


def _body_parts(data: dict, refs: tuple[MediaRef, ...]) -> list[str]:
    """The renderable pieces of one dataMessage, in reading order.

    The attachment placeholders come LAST and in `refs` order, and that is
    load-bearing: `nora.domain.media.body_with_handles`
    rewrites exactly that trailing run once custody has the bytes, and it
    checks the tail it expects before touching anything. Reordering these parts
    without telling that function costs a handle, not a message.

    A sticker is an ordinary ref, so it renders through the same
    `render_placeholder` line as an image and lands in the SAME trailing run
    `body_with_handles` rewrites, including a sticker sent with a caption (see
    `_sticker_ref`).

    A REACTION is not a body part, and that is a different judgement rather
    than an oversight: a reaction is metadata attached to somebody else's
    message, not a message. Rendering a bare `👍` as its own transcript line
    would add a turn that never happened, detached from what it was reacting
    to. It is still RECORDED, as a `Touch`
    beside the message it reacts to (`_touch`), never through this function.
    """
    parts = []
    text = (data.get("message") or "").strip()
    if text:
        parts.append(text)
    parts += [render_placeholder(label_for(ref)) for ref in refs]
    return parts


def _touch(data: dict) -> Touch | None:
    """A reaction (`dataMessage.reaction`) or a remote delete
    (`dataMessage.remoteDelete`) as a `Touch`, or None when the message is
    neither.

    signal-cli's `JsonReaction` names its target by `targetSentTimestamp` and
    its author (`targetAuthor`, or the number/ACI forms); `isRemove` marks a
    reaction being taken back. `JsonRemoteDelete` carries only the deleted
    message's sent-timestamp, and Signal lets only its author delete it, so
    the envelope's sender IS the target's author. A target with no usable
    timestamp names nothing and is dropped (None, and `parse_event` then
    answers None)."""
    reaction = data.get("reaction")
    if isinstance(reaction, dict):
        ts = reaction.get("targetSentTimestamp")
        emoji = reaction.get("emoji")
        if not isinstance(ts, int) or isinstance(ts, bool) or ts <= 0:
            return None
        if not isinstance(emoji, str) or not emoji:
            return None
        author = (
            reaction.get("targetAuthor") or reaction.get("targetAuthorNumber")
            or reaction.get("targetAuthorUuid") or ""
        )
        return Touch(
            kind="reaction", target=EditTarget(sent_ts=ts),
            target_author=str(author), emoji=emoji,
            removed=bool(reaction.get("isRemove")),
        )
    deleted = data.get("remoteDelete")
    if isinstance(deleted, dict):
        ts = deleted.get("timestamp")
        if not isinstance(ts, int) or isinstance(ts, bool) or ts <= 0:
            return None
        return Touch(kind="delete", target=EditTarget(sent_ts=ts))
    return None


def extract_envelope(event: dict) -> dict | None:
    """Return the envelope from either of signal-cli's two framings, or None.

    signal-cli frames the *same* envelope two different ways, and its
    `signal-cli-jsonrpc(5)` man page documents only the first:

        socket / TCP / stdin   {"method": "receive", "params": {"envelope": …}}
        HTTP GET /api/v1/events    {"envelope": …, "account": …}

    The SSE stream carries `receive` in the SSE ``event:`` field — which the
    listener strips along with the framing — and puts the envelope at the top
    level with **no** ``method`` key at all. Nora runs on the HTTP transport,
    so a ``method != "receive"`` guard would drop every real message before it
    was looked at: signal-cli received, Nora ingested nothing, and under R-5
    that is indistinguishable from a quiet group.

    Both framings are live — the socket transports really do send the
    JSON-RPC form — so this accepts either rather than trading one hardcoded
    assumption for the opposite one.

    PUBLIC because `messages.raw_json` stores whatever `parse_event` was
    handed, so the operator control plane (`nora.app.pending`, reading a
    sender's id forms back out of a stored envelope) has to unwrap exactly
    the same two framings. A private copy there would be a second place to
    fix when a third framing appears.
    """
    if "method" in event:
        if event.get("method") != "receive":
            return None                       # typing/receipt notification
        params = event.get("params") or {}
        # subscribeReceive (--receive-mode=manual) nests one level deeper.
        result = params.get("result")
        if isinstance(result, dict):
            return result.get("envelope") or {}
        return params.get("envelope") or {}
    env = event.get("envelope")
    return env if isinstance(env, dict) else None


def parse_event(event: dict, own_account: str) -> NormalizedMessage | None:
    try:
        env = extract_envelope(event or {})
        if env is None:
            return None
        # An EDITED message is not a `dataMessage` — `_data_message` is what
        # unwraps both framings, and `mention_spans` shares it so a rehydrated
        # mention is looked up in the same place the body came from.
        data = _data_message(env)
        if data is None:
            return None                       # syncMessage / typing / receipt / malformed

        group_id = (data.get("groupInfo") or {}).get("groupId")
        if not group_id:
            return None                       # DM — out of scope in v2

        # `sourceUuid` matters: a hidden Signal phone number means `source`
        # and `sourceNumber` are both empty, and the ACI (`sourceUuid`) is the
        # only id Nora ever sees for that sender — without the fallback such a
        # sender could never resolve to a person at all.
        source = env.get("source") or env.get("sourceNumber") or env.get("sourceUuid") or ""
        if own_account and source == own_account:
            return None                       # R-12: Nora never ingests her own sends

        if "reaction" in data or "remoteDelete" in data:
            # Recorded beside the message it touches, never as a message.
            touch = _touch(data)
            if touch is None:
                return None
            return NormalizedMessage(
                platform="signal",
                conversation_id=group_id,
                conversation_type="group",
                sender_id=source,
                sender_name=env.get("sourceName") or None,
                timestamp=int(env.get("timestamp") or data.get("timestamp") or 0),
                body="",
                raw=event,
                touch=touch,
            )

        refs = _attachments(data)
        sticker = _sticker_ref(data)
        if sticker is not None:
            refs = (*refs, sticker)
        parts = _body_parts(data, refs)
        if not parts:
            return None                       # empty — no usable body

        return NormalizedMessage(
            platform="signal",
            conversation_id=group_id,
            conversation_type="group",
            sender_id=source,
            sender_name=env.get("sourceName") or None,
            timestamp=int(env.get("timestamp") or data.get("timestamp") or 0),
            body=" ".join(parts),
            raw=event,
            mentions=_mentioned_ids(data),
            quote=_quoted(data),
            # What the platform SAID. `media_blobs` — what Nora holds — is
            # filled in one stage later; this parser still downloads nothing.
            media=refs,
            edit_of=_edit_target(env),
        )
    except Exception:
        log.exception("unparseable envelope, skipping")
        return None
