"""signal-cli SSE envelope → NormalizedMessage (spec C.3, design/01).

Group messages only in v2; DMs and syncMessages are dropped. Attachments become
placeholders — no download (v2.1).
"""

from __future__ import annotations

import logging

from ...domain.models import NormalizedMessage

log = logging.getLogger("nora.signal.parser")


def _attachment_placeholder(att: dict) -> str:
    if str(att.get("contentType", "")).startswith("image/"):
        return "[image]"
    return f"[attachment: {att.get('filename') or att.get('id') or 'file'}]"


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


def _quoted(data: dict) -> tuple[str, str] | None:
    """`(author, text)` of the message this one replies to, if any.

    Signal carries a reply as `dataMessage.quote` — the quoted author and the
    quoted text — and NOT in the body. Dropping it left Nora reading replies as
    non-sequiturs: Grant says "where for dinner", Miles replies "your place",
    and without the quote she has no way to know whose place, or that the two
    messages are connected at all.
    """
    q = data.get("quote")
    if not isinstance(q, dict):
        return None
    author = q.get("authorName") or q.get("author") or q.get("authorNumber") or ""
    text = (q.get("text") or "").strip()
    if not text:
        return None
    return (str(author), text)


def _body_parts(data: dict) -> list[str]:
    """The renderable pieces of one dataMessage, in reading order.

    A sticker becomes `[sticker]` for the same reason an image becomes
    `[image]`: a sticker IS an image, and dropping it made a sticker-only
    message vanish entirely — the sender saw Nora ignore something they had
    clearly sent. Its payload carries only `packId`/`stickerId` (per
    signal-cli's sticker schema), no name or emoji, so the bare placeholder is
    genuinely all there is to render.

    A REACTION stays dropped, and that is a different judgement rather than an
    oversight: a reaction is metadata attached to somebody else's message, not
    a message. Rendering a bare `👍` as its own transcript line would add a
    turn that never happened, detached from what it was reacting to.
    """
    parts = []
    text = (data.get("message") or "").strip()
    if text:
        parts.append(text)
    parts += [_attachment_placeholder(a) for a in (data.get("attachments") or [])]
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
        data = env.get("dataMessage")
        if not isinstance(data, dict):
            # An EDITED message is not a `dataMessage` — signal-cli nests it as
            # `editMessage.dataMessage` alongside a `targetSentTimestamp`
            # (JsonEditMessage). Reading only the top-level key made every edit
            # silently invisible: not ingested, not audited, indistinguishable
            # from a typing notification. Someone correcting a typo would watch
            # Nora act on the text they had just replaced.
            #
            # The edit is ingested as its own row rather than amending the
            # original. The transcript is an append-only audit (A9), `recent`
            # tie-breaks on `id`, and the prefill wants the LATEST text — which
            # a new row gives, in order, without a second write on the ingest
            # path. It does mean both versions appear in context; that is the
            # honest record, and it is what the operator would see in Signal.
            edit = env.get("editMessage")
            data = edit.get("dataMessage") if isinstance(edit, dict) else None
            if not isinstance(data, dict):
                return None                   # syncMessage / typing / receipt / malformed

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

        parts = _body_parts(data)
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
        )
    except Exception:
        log.exception("unparseable envelope, skipping")
        return None
