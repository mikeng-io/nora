"""whatsapp-bridge send: `POST /send` and `POST /send-media`. Never raises.

Mirrors `nora.platform.signal.send.SignalSender`'s contract — same session
construction, same "log and return" discipline — because the reader who has
read one should not have to re-read the other. The status mapping differs
where the bridge's documented shapes differ: **503 is retryable here** (the
bridge answers 503 `{"error": "not_connected"}` while the WhatsApp socket is
down, and a socket reconnecting is transient), where signal-cli's failures
are not.

A 1:1 send goes only to a JID in `dm_recipients` — the direct addresses of
the people in `identity.toml` (`nora.persons.Persons.direct_addresses`). Any
other 1:1 JID is refused without an HTTP call (Nora never messages somebody
who is not in `identity.toml`), and a sender
built without the set refuses every 1:1 send.

`mentions`/`styles`/`preview` are the port's DECORATION parameters
(`nora.domain.ports.PlatformAdapter.send`'s own docstring: "a platform with
no mention concept ignores them") and are accepted and IGNORED — outbound
WhatsApp mentions are not in v1 scope. Ignoring them is what the port
promises: decoration degrades to the plain message, never to a refusal.

Attachments go through `POST /send-media`, one call per attachment, with
`caption` carrying the message text on the FIRST attachment only — and when
any attachment exists, `/send` is NOT also called for the text, or the group
would hear the reply twice. `data_base64` carries the verified bytes.

**`media_type` is the bridge's KIND, not a MIME type** (posting a MIME
string like `"image/png"` in `media_type` gets a
bridge 400 `{"error": "media_type must be image|video|audio|sticker|document"}`
— `handleSendMedia`/`buildMediaPayload` in `bridge.js` switch on one of those
five literal kinds and reject anything else). `_bridge_media_kind` derives the
kind from `OutboundAttachment.media_type`'s RFC 6838 top-level type
(`nora.domain.media.parse_media_type`, the same parser the inbound label path
uses); the MIME itself still travels, under the bridge's separate `mimetype`
field, which Baileys needs to pick a codec/extension.
"""

from __future__ import annotations

import base64
import logging

import aiohttp

from ...domain.media import parse_media_type
from ...domain.models import ConversationTarget
from ...domain.ports import LinkPreview, Mention, OutboundAttachment, SendResult, TextStyle

log = logging.getLogger("nora.whatsapp.send")

# The bridge's `/send-media` closed kind vocabulary (`buildMediaPayload` in
# `deploy/whatsapp-bridge/bridge_helpers.js`) — anything else is a 400.
# `sticker` is a real bridge kind but is never PRODUCED here: nothing in this
# codebase builds an outbound sticker send (see module docstring), and
# `OutboundAttachment` carries no sticker flag to decide it from — the same
# reason `nora.domain.media` keeps `sticker` as a separate boolean axis from
# `image/*` rather than something derivable from a MIME type ("a sticker IS
# an image, so `image/webp` cannot tell pack art from somebody's
# photograph" — `MediaRef.sticker`'s docstring). So `image/webp` maps to
# `image` here, same as any other image subtype.
_BRIDGE_MEDIA_KINDS = frozenset({"image", "video", "audio"})


def _bridge_media_kind(media_type: str) -> str:
    """The bridge's `media_type` KIND for one attachment's MIME type.

    `image/*`, `video/*` and `audio/*` map to themselves; everything else —
    `application/pdf`, `text/plain`, an unparseable or empty string — maps to
    `document`, the bridge's catch-all kind. Never returns anything outside
    the bridge's five literal kinds, so `/send-media` can never see the 400
    this function exists to prevent."""
    top, _ = parse_media_type(media_type)
    return top if top in _BRIDGE_MEDIA_KINDS else "document"


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a `Retry-After` header as seconds — delta-seconds form only,
    mirroring `nora.platform.signal.send._retry_after_seconds`. A missing or
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
    against, as in Signal's `_sent_timestamp`.
    Anything unparseable degrades to None: a send that reached the group must
    never be reported as failed over a missing field."""
    if not isinstance(body, dict):
        return None
    value = body.get("timestamp_ms")
    # `bool` is an `int` in Python and `True` would silently become 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class WhatsAppSender:
    def __init__(
        self, base_url: str, *, timeout: float = 10.0,
        dm_recipients: frozenset[str] = frozenset(),
    ) -> None:
        self._dm_recipients = dm_recipients
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
                if resp.status == 429:
                    retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
                    log.error("send rate-limited: HTTP 429 (conversation=%s)", conversation_id)
                    return SendResult(ok=False, retryable=True, retry_after=retry_after)
                if resp.status == 503:
                    # The WhatsApp socket is down. Transient by definition —
                    # the bridge reconnects on its own, so the caller may
                    # back off and retry rather than latch a failure.
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
        request_id: str = "",
    ) -> SendResult:
        # Logs name a 1:1 chat "dm", never its JID: a phone number in a log
        # line is an identifier at rest in a file the operator greps.
        where = target.conversation_id
        if target.conversation_type == "dm":
            where = "dm"
            if target.conversation_id not in self._dm_recipients:
                log.error("refusing a direct message to a JID not in identity.toml")
                return SendResult(ok=False)
        elif target.conversation_type != "group":
            log.error("refusing to send to conversation_type=%r", target.conversation_type)
            return SendResult(ok=False)

        if attachments:
            result = SendResult(ok=True)
            for i, a in enumerate(attachments):
                payload: dict[str, object] = {
                    "jid": target.conversation_id,
                    "media_type": _bridge_media_kind(a.media_type),
                    "mimetype": a.media_type,
                    "data_base64": base64.b64encode(a.content).decode("ascii"),
                    "filename": a.filename,
                }
                # The caption carries the message text, on the FIRST
                # attachment only — the rest travel bare, and `/send` is not
                # also called, or the group hears the words twice.
                if i == 0 and text:
                    payload["caption"] = text
                result = await self._post(
                    "/send-media", payload, conversation_id=where,
                )
                if not result.ok:
                    return result
            log.info("sent %d attachment(s) to %s (id=%s)",
                     len(attachments), where, request_id)
            return result

        result = await self._post(
            "/send", {"jid": target.conversation_id, "text": text},
            conversation_id=where,
        )
        if result.ok:
            log.info("sent %d chars to %s (id=%s)",
                     len(text), where, request_id)
        return result
