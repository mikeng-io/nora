"""whatsapp-bridge send: `POST /send` and `POST /send-media`
(design/whatsapp-adoption/03-transport-contract.md). Never raises.

Mirrors `nora.platform.signal.send.SignalSender`'s contract — same session
construction, same "log and return" discipline — because the reader who has
read one should not have to re-read the other. The status mapping differs
where the bridge's documented shapes differ: **503 is retryable here** (the
bridge answers 503 `{"error": "not_connected"}` while the WhatsApp socket is
down, and a socket reconnecting is transient), where signal-cli's failures
are not.

1:1 sends are REFUSED — `conversation_type != "group"` logs an error and
returns `SendResult(ok=False)` without an HTTP call. Deny-by-default this
phase, exactly like Signal v2 and Discord v1: the DM-study session owns
whether that posture changes (design/whatsapp-adoption/02 §Open decisions).

`mentions`/`styles`/`preview` are the port's DECORATION parameters
(`nora.domain.ports.PlatformAdapter.send`'s own docstring: "a platform with
no mention concept ignores them") and are accepted and IGNORED — outbound
WhatsApp mentions are not in v1 scope. Ignoring them is what the port
promises: decoration degrades to the plain message, never to a refusal.

Attachments go through `POST /send-media`, one call per attachment, with
`caption` carrying the message text on the FIRST attachment only — and when
any attachment exists, `/send` is NOT also called for the text, or the group
would hear the reply twice. The bridge derives the media kind from the MIME
top-level type; `data_base64` carries the verified bytes.
"""

from __future__ import annotations

import base64
import logging

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import LinkPreview, Mention, OutboundAttachment, SendResult, TextStyle

log = logging.getLogger("nora.whatsapp.send")


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
    against, the same Bug B lesson Signal's `_sent_timestamp` records.
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
        if target.conversation_type != "group":
            log.error("refusing to send to conversation_type=%r (1:1 is deny-by-default "
                      "this phase)", target.conversation_type)
            return SendResult(ok=False)

        if attachments:
            result = SendResult(ok=True)
            for i, a in enumerate(attachments):
                payload: dict[str, object] = {
                    "jid": target.conversation_id,
                    "media_type": a.media_type,
                    "data_base64": base64.b64encode(a.content).decode("ascii"),
                    "filename": a.filename,
                }
                # The caption carries the message text, on the FIRST
                # attachment only — the rest travel bare, and `/send` is not
                # also called, or the group hears the words twice.
                if i == 0 and text:
                    payload["caption"] = text
                result = await self._post(
                    "/send-media", payload, conversation_id=target.conversation_id,
                )
                if not result.ok:
                    return result
            log.info("sent %d attachment(s) to %s (id=%s)",
                     len(attachments), target.conversation_id, request_id)
            return result

        result = await self._post(
            "/send", {"jid": target.conversation_id, "text": text},
            conversation_id=target.conversation_id,
        )
        if result.ok:
            log.info("sent %d chars to %s (id=%s)",
                     len(text), target.conversation_id, request_id)
        return result
