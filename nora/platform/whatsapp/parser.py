"""whatsapp-bridge SSE event → NormalizedMessage.

The bridge owns the WhatsApp wire; this module owns the normalization into
Nora's vocabulary; the bridge's transport contract is the only place the
shapes are defined — a drift here is a contract-test failure, not a judgment
call. Field names below (`chat_jid`, `sender_phone`, `mentioned`, `quoted`,
`media`, `timestamp_ms`) are the bridge's, verbatim.

Dropped before a `NormalizedMessage` is built (the contract's own list):
non-`message` types (heartbeat/dropped are listener-level, but this function
still answers None for them so it stays total on its own), `from_me`
(R-12's first pass), `status@broadcast`/newsletter JIDs (feeds, not rooms),
and turns carrying neither text nor media.

An EDIT arrives as an ordinary `message` event whose `edit_of` names the
original stanza, with `text`/`media` describing the NEW content. It becomes
`NormalizedMessage.edit_of = EditTarget(message_id=<that stanza id>)`, and
ingest records it beside the message it edits rather than as a row of its own.
`message_id` stays the edit's own stanza id —
the original's is what `edit_of` names.

A `reaction` or a `delete` (REVOKE) event becomes a `Touch` on the message
its `target_id` names (`_parse_touch`), recorded beside that message and
never as a message of its own.

Never raises: one malformed event is logged and skipped, mirroring the
sibling parsers — a bad turn must never kill the stream behind it.
"""

from __future__ import annotations

import logging

from ...domain.media import MediaRef, label_for, render_placeholder
from ...domain.models import EditTarget, NormalizedMessage, Quote, Touch
from .jid import (
    conversation_id_for,
    is_broadcast,
    is_group_jid,
    is_lid_jid,
    is_newsletter,
    is_phone_jid,
    lid_form,
    parse_jid,
    phone_form,
    sender_form,
)

log = logging.getLogger("nora.whatsapp.parser")


def _media_ref(entry: dict) -> MediaRef:
    """One bridge `media[]` entry reduced to what labelling needs.

    `platform_id` and `fetch_token` are EMPTY: the event carries the FACT of
    media, never the blob — custody is a later phase (contract §`media`), and
    a handle that named nothing would be a custody failure waiting to be
    counted. `filename` is deliberately NOT carried over: sender-supplied
    names never render (`nora/domain/media.py`'s rule), and recording one
    here would only create a place for it to leak from.

    `media_type` is the sender-declared mimetype, stored verbatim —
    `label_for`/`parse_media_type` already absorb the `; codecs=opus`
    parameter form a voice note arrives with."""
    return MediaRef(
        platform_id="",
        media_type=str(entry.get("mimetype") or ""),
        size=(int(entry["size"]) if isinstance(entry.get("size"), int) else None),
        voice_note=bool(entry.get("voice_note")),
    )


def _mentioned_ids(entries: object) -> frozenset[str]:
    """Every normalized id form of every `mentioned[]` entry.

    BOTH forms when both are known — `"+<phone>"` AND the bare lid digits —
    because `MentionPolicy` membership-tests this set against
    `registry.self_aliases()`, and which form that set carries depends on
    what `GET /self` returned (`nora.platform.whatsapp.self_id`). A mention
    that matched only the form Nora happened not to advertise would be a tag
    she never saw.

    When the bridge resolved neither field, the entry's own JID is
    normalized instead: a `@s.whatsapp.net` mention still yields its phone
    form, a `@lid` mention its lid form. An entry that yields nothing is
    skipped — a mention Nora cannot name is not evidence of being tagged."""
    out: set[str] = set()
    if not isinstance(entries, list):
        return frozenset()
    for m in entries:
        if not isinstance(m, dict):
            continue
        phone, lid = m.get("phone"), m.get("lid")
        if isinstance(phone, str) and phone:
            out.add(phone_form(phone))
        if isinstance(lid, str) and lid:
            out.add(lid_form(lid))
        if (not phone and not lid) and isinstance(m.get("jid"), str):
            parts = parse_jid(m["jid"])
            if parts is not None and is_phone_jid(m["jid"]):
                out.add(phone_form(parts.user))
            elif parts is not None and is_lid_jid(m["jid"]):
                out.add(lid_form(parts.user))
    return frozenset(out)


def _quoted(quoted: object) -> Quote | None:
    """The message this one replies to, as a pointer — `ReplyToNoraPolicy`'s
    half of the engagement contract.

    `author_id` gets the same normalization as `sender_id` (phone form, else
    lid form, else raw), because the policy matches it against the same
    self-alias set. `quoted_ts` is `quoted.timestamp_ms`, or **0 when the
    bridge's recent-message cache predates the parent** — the reply relation
    survives, the parent pointer does not: a `by_ts` lookup misses on 0 and
    renders no parent, which is the honest degradation the contract pins.
    """
    if not isinstance(quoted, dict):
        return None
    author_id = sender_form(
        str(quoted.get("participant_jid") or ""),
        quoted.get("participant_phone") if isinstance(quoted.get("participant_phone"), str)
        else None,
        quoted.get("participant_lid") if isinstance(quoted.get("participant_lid"), str)
        else None,
    )
    ts = quoted.get("timestamp_ms")
    return Quote(
        author_id=author_id,
        text=str(quoted.get("text") or "").strip(),
        quoted_ts=ts if isinstance(ts, int) and not isinstance(ts, bool) else 0,
    )


def _parse_touch(event: dict) -> NormalizedMessage | None:
    """A bridge `reaction` or `delete` event → a `Touch` (see the module
    docstring). Nora's own (`from_me`) is dropped (R-12); so is one naming no
    target, and one from a status broadcast or a newsletter. A DM's touch is
    parsed like a group's; the room gate drops it at ingest, as it drops a DM
    message unless a policy.toml conversation admits it, and a DM from anybody
    not in identity.toml is dropped whatever policy.toml says (R-101)."""
    if event.get("from_me"):
        return None
    chat_jid = event.get("chat_jid")
    target = str(event.get("target_id") or "")
    if not isinstance(chat_jid, str) or not chat_jid or not target:
        return None
    if is_broadcast(chat_jid) or is_newsletter(chat_jid):
        return None
    if event.get("type") == "reaction":
        emoji = event.get("emoji")
        emoji = emoji if isinstance(emoji, str) else ""
        # A removal carries NO emoji on WhatsApp: the row records `""` and
        # `removed`, rather than guess which of the sender's reactions it was.
        touch = Touch(
            kind="reaction", target=EditTarget(message_id=target),
            emoji=emoji, removed=bool(event.get("removed")) or not emoji,
        )
    else:
        touch = Touch(kind="delete", target=EditTarget(message_id=target))
    is_group = bool(event.get("is_group")) or is_group_jid(chat_jid)
    resolved_phone = event.get("sender_phone")
    timestamp_ms = event.get("timestamp_ms")
    return NormalizedMessage(
        platform="whatsapp",
        conversation_id=conversation_id_for(
            chat_jid, resolved_phone if isinstance(resolved_phone, str) else None,
        ),
        conversation_type="group" if is_group else "dm",
        sender_id=sender_form(
            str(event.get("sender_jid") or ""),
            resolved_phone if isinstance(resolved_phone, str) else None,
            event.get("sender_lid") if isinstance(event.get("sender_lid"), str) else None,
        ),
        sender_name=event.get("push_name") or None,
        timestamp=(
            timestamp_ms
            if isinstance(timestamp_ms, int) and not isinstance(timestamp_ms, bool)
            else 0
        ),
        body="",
        raw=event,
        message_id=str(event.get("id") or ""),
        touch=touch,
    )


def parse_event(event: dict) -> NormalizedMessage | None:
    try:
        if isinstance(event, dict) and event.get("type") in ("reaction", "delete"):
            return _parse_touch(event)
        if not isinstance(event, dict) or event.get("type") != "message":
            return None                     # heartbeat / dropped are listener-level
        if event.get("from_me"):
            return None                     # R-12's first pass: never ingest our own sends
        chat_jid = event.get("chat_jid")
        if not isinstance(chat_jid, str) or not chat_jid:
            return None
        if is_broadcast(chat_jid) or is_newsletter(chat_jid):
            return None                     # a feed, not a room Nora is in

        refs = tuple(
            _media_ref(m) for m in (event.get("media") or []) if isinstance(m, dict)
        )
        parts = []
        text = str(event.get("text") or "").strip()
        if text:
            parts.append(text)
        # Placeholders LAST and in `media[]` order — the same load-bearing
        # trailing run `nora.domain.media.body_with_handles` rewrites on the
        # Signal side once custody has the bytes. A voice-note-only turn
        # survives through this line as a bare `[voice note]`.
        parts += [render_placeholder(label_for(ref)) for ref in refs]
        if not parts:
            return None                     # neither text nor media — nothing to ingest

        is_group = bool(event.get("is_group")) or is_group_jid(chat_jid)
        # For a 1:1 the chat's phone comes from the event's own resolution
        # (`sender_phone` — the bridge's LID→phone map), with the user part
        # of a direct phone JID as the form that needs no map at all.
        resolved_phone = event.get("sender_phone")
        if not (isinstance(resolved_phone, str) and resolved_phone):
            parts_jid = parse_jid(chat_jid)
            resolved_phone = parts_jid.user if parts_jid and is_phone_jid(chat_jid) else None

        sender_jid = event.get("sender_jid")
        timestamp_ms = event.get("timestamp_ms")
        return NormalizedMessage(
            platform="whatsapp",
            conversation_id=conversation_id_for(chat_jid, resolved_phone),
            conversation_type="group" if is_group else "dm",
            sender_id=sender_form(
                str(sender_jid or ""),
                event.get("sender_phone") if isinstance(event.get("sender_phone"), str)
                else None,
                event.get("sender_lid") if isinstance(event.get("sender_lid"), str)
                else None,
            ),
            sender_name=event.get("push_name") or None,
            timestamp=(
                timestamp_ms
                if isinstance(timestamp_ms, int) and not isinstance(timestamp_ms, bool)
                else 0
            ),
            body=" ".join(parts),
            raw=event,
            mentions=_mentioned_ids(event.get("mentioned")),
            quote=_quoted(event.get("quoted")),
            media=refs,
            # The bridge's stanza id — what `POST /react` addresses a message
            # by, the way Discord's snowflake does. `(author, timestamp)`
            # means nothing to that endpoint.
            message_id=str(event.get("id") or ""),
            edit_of=(
                EditTarget(message_id=str(event["edit_of"]))
                if event.get("edit_of") else None
            ),
        )
    except Exception:
        log.exception("unparseable whatsapp event, skipping")
        return None
