"""signal-cli SSE envelope → NormalizedMessage (spec C.3, design/01).

Group messages only in v2; DMs and syncMessages are dropped. Attachments become
a placeholder plus a `MediaRef` — this module still downloads nothing, but it
no longer throws away what an attachment WAS. The bytes are fetched one stage
later (`nora.ingest.stages.media`), from the ref this produces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ...domain.media import MediaRef, label_for, render_placeholder
from ...domain.models import NormalizedMessage, Quote

log = logging.getLogger("nora.signal.parser")

# OBJECT REPLACEMENT CHARACTER — what Signal puts in the body where an
# @-mention's name appears. It is the ONLY thing the body carries about a
# mention; the identity lives in the parallel `mentions` array. Written as an
# escape, not as the literal character: ￼ renders as a box or as nothing at all
# depending on the font, so a reader cannot verify a literal one by eye.
PLACEHOLDER = "\ufffc"


def _media_ref(att: dict) -> MediaRef:
    """One `JsonAttachment` reduced to what custody and labelling need.

    Field names are signal-cli's: `id` (the handle its own store uses, and what
    `getAttachment` takes), `contentType`, `filename`, `size`, `voiceNote`.

    **`voiceNote` is the one field here not verified against a real message**,
    because no attachment has ever arrived on this deployment — the honest
    statement of what is and is not known, rather than a claim dressed as a
    fact. Its failure mode was chosen to be harmless: absent or renamed, an
    `audio/*` attachment renders `[audio]` instead of `[voice note]`, which is
    still the fix (she can tell somebody sent sound) and is never a wrong
    claim about who said what. `nora.domain.media.label_for` is where that
    fallback lives.
    """
    return MediaRef(
        platform_id=str(att.get("id") or ""),
        media_type=str(att.get("contentType") or ""),
        filename=(str(att["filename"]) if att.get("filename") else None),
        size=(int(att["size"]) if isinstance(att.get("size"), int) else None),
        voice_note=bool(att.get("voiceNote")),
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
    display name at all (verified against `data/messages.db`, ids 3-28). A
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
    (JsonEditMessage). Reading only the top-level key made every edit silently
    invisible: not ingested, not audited, indistinguishable from a typing
    notification. Someone correcting a typo would watch Nora act on the text
    they had just replaced.

    The edit is ingested as its own row rather than amending the original. The
    transcript is an append-only audit (A9), `recent` tie-breaks on `id`, and
    the prefill wants the LATEST text — which a new row gives, in order,
    without a second write on the ingest path. It does mean both versions
    appear in context; that is the honest record, and it is what the operator
    would see in Signal.

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
    it left Nora reading replies as non-sequiturs: Grant says "where for
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
    # because a display name is spoofable (design/10 §1.3).
    author_id = q.get("author") or q.get("authorUuid") or q.get("authorNumber") or ""
    text = (q.get("text") or "").strip()
    return Quote(author_id=str(author_id), text=text, quoted_ts=quoted_ts)


def _body_parts(data: dict, refs: tuple[MediaRef, ...]) -> list[str]:
    """The renderable pieces of one dataMessage, in reading order.

    The attachment placeholders come LAST and in `refs` order, and that is now
    load-bearing rather than incidental: `nora.domain.media.body_with_handles`
    rewrites exactly that trailing run once custody has the bytes, and it
    checks the tail it expects before touching anything. Reordering these parts
    without telling that function costs a handle, not a message.

    A sticker becomes `[sticker]` for the same reason an image becomes
    `[image]`: a sticker IS an image, and dropping it made a sticker-only
    message vanish entirely — the sender saw Nora ignore something they had
    clearly sent. Its payload carries only `packId`/`stickerId` (per
    signal-cli's sticker schema), no name or emoji, so the bare placeholder is
    genuinely all there is to render. It is deliberately NOT a `MediaRef`:
    there is no attachment id to fetch and no bytes to take custody of, so
    inventing one would create a custody failure to count on every sticker.

    A REACTION stays dropped, and that is a different judgement rather than an
    oversight: a reaction is metadata attached to somebody else's message, not
    a message. Rendering a bare `👍` as its own transcript line would add a
    turn that never happened, detached from what it was reacting to.
    """
    parts = []
    text = (data.get("message") or "").strip()
    if text:
        parts.append(text)
    parts += [render_placeholder(label_for(ref)) for ref in refs]
    if not parts and isinstance(data.get("sticker"), dict):
        parts.append("[sticker]")
    return parts


def extract_envelope(event: dict) -> dict | None:
    """Return the envelope from either of signal-cli's two framings, or None.

    signal-cli frames the *same* envelope two different ways, and its
    `signal-cli-jsonrpc(5)` man page documents only the first:

        socket / TCP / stdin   {"method": "receive", "params": {"envelope": …}}
        HTTP GET /api/v1/events    {"envelope": …, "account": …}

    The SSE stream carries `receive` in the SSE ``event:`` field — which the
    listener strips along with the framing — and puts the envelope at the top
    level with **no** ``method`` key at all. Nora runs on the HTTP transport,
    so the old ``method != "receive"`` guard dropped every real message before
    it was ever looked at: signal-cli received, Nora ingested nothing, and
    under R-5 that is indistinguishable from a quiet group.

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
        # only id Nora ever sees for that sender — never falling back to it
        # meant such a sender could never resolve to a person at all
        # (design/10 §1.1, §1.2).
        source = env.get("source") or env.get("sourceNumber") or env.get("sourceUuid") or ""
        if own_account and source == own_account:
            return None                       # R-12: Nora never ingests her own sends

        refs = _attachments(data)
        parts = _body_parts(data, refs)
        if not parts:
            return None                       # reaction / empty — no usable body

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
        )
    except Exception:
        log.exception("unparseable envelope, skipping")
        return None
