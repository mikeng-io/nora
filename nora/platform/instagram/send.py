"""instagram-bridge send: `POST /send` and `POST /send-media`
(design/instagram-adoption/03-transport-contract.md). Never raises.

Mirrors `nora.platform.whatsapp.send.WhatsAppSender`'s contract — same session
construction, same "log and return" discipline — because the reader who has
read one should not have to re-read the other. Three things differ, and each
one is a real difference on the wire rather than a stylistic choice.

## 1. Text and media are ALWAYS two messages, and this INVERTS WhatsApp

`slidetypes.SendMediaRequest` has fields for attachment, thread, threading id
and reply — and **no text field of any kind** (`03` §"Outbound"). A caption
cannot be expressed on this wire; `POST /send-media` carries no caption field
and ignores one if sent.

WhatsApp's sender puts the reply text in `caption` on the first attachment and
DELIBERATELY SKIPS `/send`, so the group does not hear the reply twice.
Copying that shape here silently drops the text — the message goes out as a
bare image and the words are gone, with a 200 on every call. So on Instagram:
`POST /send` with the text first, then one `POST /send-media` per attachment.

The text goes FIRST on purpose. If the media upload fails, the group has
already heard what Nora said; the reverse order would leave an image with no
explanation of why it arrived.

## 2. 409 is a checkpoint and is NOT retryable

`03`'s status table: 409 means the Instagram session is checkpointed or logged
out, and clearing it needs a human. Retrying a 409 is how a captcha becomes a
ban, so it is `ok=False` with `retryable=False` and an error line that says a
human is needed. 503 (socket down or reconnecting) stays retryable, the same
as WhatsApp's — the bridge heals that one on its own.

500 is the fail-closed default the bridge chose deliberately: 503 is a CLAIM
that retrying will work, and an unclassifiable refusal has not earned it.

## 3. `mentions` are FORWARDED, not ignored

WhatsApp v1 accepts and ignores them. Instagram's `/send` takes
`mentions: [{fbid, offset, length}]` and `02` measured the round trip, so the
field is filled — a decoration parameter that the wire supports and the
adapter dropped would be a capability lost at the last inch.

**The offset unit is an open ambiguity and is reported as one.** `Mention.
start`/`length` are UTF-16 code units (signal-cli's documented unit, and the
one `nora.domain.utf16` computes). `03` pins the field NAMES and never states
Instagram's unit, and no probe measured a mention past a non-BMP character.
They are passed through unchanged, which is right if the units agree and puts
the span a few characters off if they do not. Nothing composes an Instagram
mention today (`mention_resolve` is Discord's), so the exposure is zero until
one does — and when one does, this is the line to measure before trusting.

`styles` and `preview` stay accepted-and-IGNORED: the port's own docstring
says a platform with no such concept ignores them, and this wire has neither.

1:1 sends are REFUSED — `conversation_type != "group"` logs an error and
returns `SendResult(ok=False)` without an HTTP call. Deny-by-default this
phase, exactly like Signal v2, Discord v1 and WhatsApp v1. Note the limit of
what this can catch: the inbound event carries no group/DM discriminator
(`parser` docstring), so an Instagram DM arrives typed `group` and this guard
never sees it. It still fires for every caller that builds a
`ConversationTarget` itself.
"""

from __future__ import annotations

import base64
import logging

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import (
    LinkPreview,
    Mention,
    OutboundAttachment,
    OutboundQuote,
    SendResult,
    TextStyle,
)

log = logging.getLogger("nora.instagram.send")


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a `Retry-After` header as seconds — delta-seconds form only,
    mirroring `nora.platform.whatsapp.send._retry_after_seconds`. A missing or
    unparseable header degrades to None: the caller still learns the send is
    retryable, just without a hint of how long to wait."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _sent_timestamp(body: object) -> int | None:
    """`timestamp_ms` out of the bridge's `{ok, id, timestamp_ms}` result.

    The PLATFORM's own sent-timestamp — what a later reply's quote resolves
    against, the same lesson `nora.platform.signal.send._sent_timestamp`
    records. Anything unparseable degrades to None: a send that reached the
    group must never be reported as failed over a missing field."""
    if not isinstance(body, dict):
        return None
    value = body.get("timestamp_ms")
    # `bool` is an `int` in Python and `True` would silently become 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _mention_payload(mentions: tuple[Mention, ...]) -> list[dict[str, object]]:
    """`Mention(start, length, recipient)` → `{fbid, offset, length}`.

    `recipient` carries the interop fbid here, the same number
    `mentions[].fbid` arrives as inbound — see the module docstring for the
    offset-unit ambiguity this does NOT resolve. An entry with no recipient is
    dropped rather than sent with an empty fbid: a mention that names nobody
    is not a mention, and the bridge would have to guess what it meant.
    """
    out: list[dict[str, object]] = []
    for m in mentions:
        recipient = (m.recipient or "").strip()
        if not recipient:
            continue
        out.append({"fbid": recipient, "offset": m.start, "length": m.length})
    return out


class InstagramSender:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _post(self, path: str, payload: dict, *, conversation_id: str) -> SendResult:
        """One POST against the bridge, with the contract's status mapping."""
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(f"{self._base_url}{path}", json=payload) as resp,
            ):
                if resp.status == 200:
                    return SendResult(ok=True, timestamp=_sent_timestamp(await resp.json()))
                if resp.status == 409:
                    # A CHECKPOINT. Explicitly NOT retryable: the session is
                    # challenged and only a human can clear it, and hammering
                    # a challenged account is how a captcha becomes a ban
                    # (`03`, and the bridge's own status-mapping comment).
                    log.error(
                        "send refused: instagram session is checkpointed or logged out "
                        "(conversation=%s) — a human has to clear it; retrying would not",
                        conversation_id,
                    )
                    return SendResult(ok=False)
                if resp.status == 429:
                    retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
                    log.error("send rate-limited: HTTP 429 (conversation=%s)", conversation_id)
                    return SendResult(ok=False, retryable=True, retry_after=retry_after)
                if resp.status == 503:
                    # The Instagram socket is down or reconnecting. Transient
                    # by definition — the bridge reconnects on its own, so the
                    # caller may back off and retry rather than latch.
                    log.error("send failed: bridge not connected (conversation=%s)",
                              conversation_id)
                    return SendResult(ok=False, retryable=True)
                log.error("send failed: HTTP %s (conversation=%s)",
                          resp.status, conversation_id)
                return SendResult(ok=False)
        except Exception as exc:               # noqa: BLE001 — a send failure is never fatal
            log.error("send failed: %s (conversation=%s)", type(exc).__name__, conversation_id)
            return SendResult(ok=False)

    async def send(
        self,
        target: ConversationTarget,
        text: str,
        *,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: OutboundQuote | None = None,
        request_id: str = "",
    ) -> SendResult:
        if target.conversation_type != "group":
            log.error("refusing to send to conversation_type=%r (1:1 is deny-by-default "
                      "this phase)", target.conversation_type)
            return SendResult(ok=False)

        # `reply_to` is the parent's PLATFORM message id (`mid.$…`), which is
        # the only handle this wire accepts. `OutboundQuote.ts`/`author_id` are
        # Signal's scheme and cannot reconstruct one, so a quote without the
        # id sends unthreaded — a reply that lost its thread is still a reply.
        reply_to = quote.platform_message_id if quote is not None else None

        result = SendResult(ok=True)
        # `or not attachments` so a text-less, attachment-less send still makes
        # the call and the bridge decides. Returning `ok=True` for a send that
        # never left the process would be a delivery status Nora invented.
        if text or not attachments:
            payload: dict[str, object] = {"room_id": target.conversation_id, "text": text}
            mention_payload = _mention_payload(mentions)
            if mention_payload:
                payload["mentions"] = mention_payload
            if reply_to:
                payload["reply_to"] = reply_to
            result = await self._post(
                "/send", payload, conversation_id=target.conversation_id,
            )
            if not result.ok:
                # The words did not land. Sending the attachments anyway would
                # put an image in the room with nothing explaining it, and
                # would report the TEXT's failure as a success.
                return result
            log.info("sent %d chars to %s (id=%s)",
                     len(text), target.conversation_id, request_id)

        for a in attachments:
            media_result = await self._post(
                "/send-media",
                {
                    "room_id": target.conversation_id,
                    "data_base64": base64.b64encode(a.content).decode("ascii"),
                    "filename": a.filename,
                    "mimetype": a.media_type,
                    # No `caption` key, ever: the wire has nowhere to put one
                    # and the bridge ignores it. See the module docstring.
                    **({"reply_to": reply_to} if reply_to else {}),
                },
                conversation_id=target.conversation_id,
            )
            if not media_result.ok:
                return media_result
            result = media_result
        if attachments:
            log.info("sent %d attachment(s) to %s (id=%s)",
                     len(attachments), target.conversation_id, request_id)
        return result
